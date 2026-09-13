from io import BytesIO
from pathlib import Path
from uuid import UUID

from PIL import Image, UnidentifiedImageError
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch, Q
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from catalogue.models import (
    DuplicateCandidate,
    Recording,
    RecordingContribution,
    Release,
    ReleaseTrack,
)
from catalogue.services import create_release_track
from flac_ingest.models import FlacIngestBatch, FlacIngestItem
from flac_ingest.services import (
    SOURCE_SYSTEM_NAME,
    apply_batch,
    confirm_existing_flac_assertions,
    review_item,
    scan_directory,
    sync_recording_files,
)
from managed_music.forms import ManagedRecordingCreationForm
from managed_music.models import ManagedRecording
from managed_music.services import create_managed_recording
from media_assets.models import FileAsset, FileLocation
from music_library.models import MusicLibraryEntry
from parties.models import ArtistIdentity, Party
from provenance.models import AppliedMetadataChange, MetadataAssertion, SourceSystem
from provenance.services import (
    apply_assertion,
    correct_assertion,
    decide_assertion,
)
from rights.forms import (
    AgreementDocumentForm,
    AgreementForm,
    AgreementPartyForm,
    ClaimAgreementForm,
    RightsClaimForm,
    RightsDecisionForm,
)
from rights.help_content import RIGHTS_FORM_HELP, RIGHTS_HELP, RIGHTS_HELP_SECTIONS
from rights.models import Agreement, RightsClaim
from rights.services import (
    create_rights_claim,
    decide_rights_claim,
    get_local_organization,
    link_claim_agreement,
    supersede_rights_claim,
)
from rights.summaries import (
    OwnershipCategory,
    classify_ownership,
    has_local_confirmed_right,
    local_confirmed_right_recording_ids,
    ownership_summaries_for_recordings,
)
from rights_core.models import VerificationStatus

from .forms import (
    ArtistIdentityForm,
    AssertionActionForm,
    ContributionForm,
    FileAssetForm,
    FileLocationForm,
    FlacIngestReviewForm,
    FlacScanForm,
    LibraryMembershipForm,
    ManagedFilterForm,
    MusicLibraryFilterForm,
    PartyForm,
    RadioMetadataForm,
    RecordingForm,
    RecordingIdentifierForm,
    ReleaseFilterForm,
    ReleaseForm,
    ReleaseIdentifierForm,
    SearchForm,
    TrackFormSet,
)


def _safe_return(request, default):
    candidate = request.POST.get("return") or request.GET.get("return")
    if (
        candidate
        and candidate.startswith("/")
        and not candidate.startswith("//")
        and url_has_allowed_host_and_scheme(
            candidate,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
    ):
        return candidate
    return default


def _page_context(section, title, **extra):
    labels = {
        "home": "Arbeidsoversikt",
        "library": "Musikkarkiv",
        "managed": "Forvaltet musikk",
        "releases": "Utgivelser",
        "parties": "Artister og personer",
        "control": "Kvalitetskontroll",
        "files": "Filregister",
        "rights": "Rettigheter og avtaler",
        "help": "Brukerhjelp",
    }
    return {
        "section": section,
        "section_label": labels.get(section, "Katalogarbeid"),
        "title": title,
        "rights_help": RIGHTS_HELP,
        **extra,
    }


def _paginate(request, queryset, per_page=30):
    from django.core.paginator import Paginator

    page = Paginator(queryset, per_page).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return page, query.urlencode()


staff = staff_member_required(login_url="/admin/login/")


@staff
def home(request):
    tasks = []
    if request.user.has_perm("catalogue.view_duplicatecandidate"):
        tasks.append(
            {
                "label": "Mulige dubletter",
                "count": DuplicateCandidate.objects.filter(status="open").count(),
                "url": reverse("workbench:control") + "?type=dubletter",
            }
        )
    if request.user.has_perm("provenance.view_metadataassertion"):
        tasks.extend(
            (
                {
                    "label": "Uverifiserte kildeopplysninger",
                    "count": MetadataAssertion.objects.filter(
                        status=VerificationStatus.UNVERIFIED
                    ).count(),
                    "url": reverse("workbench:control") + "?type=uverifisert",
                },
                {
                    "label": "Bestridte kildeopplysninger",
                    "count": MetadataAssertion.objects.filter(
                        status=VerificationStatus.DISPUTED
                    ).count(),
                    "url": reverse("workbench:control") + "?type=konflikter",
                },
            )
        )
    if request.user.has_perm("rights.view_rightsclaim") and request.user.has_perm(
        "managed_music.view_managedrecording"
    ):
        tasks.append(
            {
                "label": "Uavklarte rettighetskrav",
                "count": RightsClaim.objects.filter(
                    status__in=(
                        VerificationStatus.UNVERIFIED,
                        VerificationStatus.DISPUTED,
                    )
                ).count(),
                "url": reverse("workbench:managed"),
            }
        )
    return render(
        request,
        "workbench/home.html",
        _page_context("home", "Start", tasks=tasks),
    )


def _primary_credit(recording):
    credits = list(recording.contributions.all())
    return next(
        (
            credit
            for credit in credits
            if credit.role == RecordingContribution.Role.PRIMARY
        ),
        credits[0] if credits else None,
    )


@staff
@permission_required("music_library.view_musiclibraryentry", raise_exception=True)
def library_list(request):
    form = MusicLibraryFilterForm(request.GET)
    queryset = (
        MusicLibraryEntry.objects.select_related(
            "recording", "managed_recording", "managed_recording__source_system"
        )
        .prefetch_related(
            "channels",
            "target_audiences",
            "recording__identifiers",
            "recording__release_tracks__release",
            "recording__file_assets__locations",
            Prefetch(
                "recording__contributions",
                RecordingContribution.objects.select_related(
                    "party", "artist_identity"
                ),
            ),
        )
        .order_by("recording__title", "id")
    )
    if form.is_valid():
        q = form.cleaned_data.get("q")
        if q:
            queryset = queryset.filter(
                Q(recording__title__icontains=q)
                | Q(recording__identifiers__normalized_value__icontains=q)
                | Q(recording__contributions__party__name__icontains=q)
                | Q(
                    recording__contributions__artist_identity__display_name__icontains=q
                )
            ).distinct()
        status = form.cleaned_data.get("status")
        for field in ("genre", "language"):
            if form.cleaned_data.get(field):
                queryset = queryset.filter(
                    **{field + "__iexact": form.cleaned_data[field]}
                )
        ordering = {
            "title": "recording__title",
            "-title": "-recording__title",
            "recent": "-updated_at",
        }
        queryset = queryset.order_by(
            ordering.get(form.cleaned_data.get("order"), "recording__title"), "id"
        )
        if status:
            queryset = queryset.filter(verification_status=status)
        managed = form.cleaned_data.get("managed")
        if managed == "yes":
            queryset = queryset.filter(managed_recording__isnull=False)
        elif managed == "no":
            queryset = queryset.filter(managed_recording__isnull=True)
        source = form.cleaned_data.get("source")
        if source:
            recording_ids = MetadataAssertion.objects.filter(
                entity_type=MetadataAssertion.EntityType.RECORDING,
                source_record__source_system_id=source,
            ).values("entity_uuid")
            queryset = queryset.filter(recording_id__in=recording_ids)
    page, query = _paginate(request, queryset)
    rows = list(page.object_list)
    selected = next(
        (
            entry
            for entry in rows
            if str(entry.recording_id) == request.GET.get("selected")
        ),
        None,
    )
    if not selected and rows and request.GET.get("selected") != "none":
        selected = rows[0]
    if request.user.has_perms(
        ("media_assets.view_fileasset", "media_assets.view_filelocation")
    ):
        covers = dict(
            FileAsset.objects.filter(
                role="cover_image", recording_id__in=[e.recording_id for e in rows]
            )
            .order_by("id")
            .values_list("recording_id", "id")
        )
        for entry in rows:
            entry.cover_id = covers.get(entry.recording_id)
    list_params = request.GET.copy()
    list_params.pop("selected", None)
    for entry in rows:
        params = list_params.copy()
        params["selected"] = str(entry.recording_id)
        entry.preview_url = "?" + params.urlencode()
        duration = entry.recording.duration_ms
        entry.display_duration = (
            f"{duration // 60000}:{duration // 1000 % 60:02}"
            if duration is not None
            else "—"
        )
        entry.isrc = next(
            (
                identifier.normalized_value
                for identifier in entry.recording.identifiers.all()
                if identifier.scheme == "ISRC"
            ),
            "—",
        )
        entry.primary_credit = _primary_credit(entry.recording)
        if request.user.has_perm("media_assets.view_fileasset"):
            assets = list(entry.recording.file_assets.all())
            locations = [
                location
                for asset in assets
                for location in asset.locations.all()
                if location.is_current
            ]
            if any(
                location.verification_status
                == FileLocation.VerificationStatus.VERIFIED
                for location in locations
            ):
                entry.file_state = "verified"
                entry.file_state_label = "Kontrollert"
            elif locations:
                entry.file_state = "unchecked"
                entry.file_state_label = "Ikke kontrollert"
            elif assets:
                entry.file_state = "reference"
                entry.file_state_label = "Kun referanse"
            else:
                entry.file_state = "missing"
                entry.file_state_label = "Ingen fil"
    panel_sources = MetadataAssertion.objects.none()
    panel_file = None
    panel_location = None
    if selected and request.user.has_perm("provenance.view_metadataassertion"):
        panel_sources = MetadataAssertion.objects.filter(
            entity_type="recording", entity_uuid=selected.recording_id
        ).select_related("source_record__source_system")[:5]
    if selected and request.user.has_perm("media_assets.view_fileasset"):
        panel_assets = list(selected.recording.file_assets.all())
        panel_file = next(
            (asset for asset in panel_assets if asset.role == FileAsset.Role.RADIO_FLAC),
            panel_assets[0] if panel_assets else None,
        )
        if panel_file:
            panel_location = next(
                (
                    location
                    for location in panel_file.locations.all()
                    if location.is_current
                ),
                None,
            )
    return render(
        request,
        "workbench/library_list.html",
        _page_context(
            "library",
            "Musikkarkiv",
            form=form,
            page=page,
            page_query=query,
            selected_entry=selected,
            panel_sources=panel_sources,
            panel_file=panel_file,
            panel_location=panel_location,
            close_panel_url="?" + list_params.urlencode() + "&selected=none",
            sources=SourceSystem.objects.all(),
        ),
    )


@staff
@permission_required("music_library.add_musiclibraryentry", raise_exception=True)
def library_add(request):
    initial = (
        {"recording": request.GET["recording"]}
        if request.GET.get("recording")
        else None
    )
    form = LibraryMembershipForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        entry, created = MusicLibraryEntry.objects.get_or_create(
            recording=form.cleaned_data["recording"]
        )
        if created:
            messages.success(request, "Innspillingen ble lagt til i Musikkarkivet.")
        else:
            messages.info(request, "Innspillingen finnes allerede i Musikkarkivet.")
        return redirect("workbench:recording", pk=entry.recording_id)
    return render(
        request,
        "workbench/form.html",
        _page_context(
            "library",
            "Legg til i Musikkarkivet",
            form=form,
            submit_label="Legg til",
            cancel_url=reverse("workbench:library"),
        ),
    )


@staff
@permission_required("managed_music.view_managedrecording", raise_exception=True)
def managed_list(request):
    form = ManagedFilterForm(request.GET)
    can_view_rights = request.user.has_perm("rights.view_rightsclaim")
    if not can_view_rights:
        for field_name in (
            "ownership",
            "local_administration",
            "local_distribution",
        ):
            form.fields.pop(field_name)
    queryset = (
        ManagedRecording.objects.select_related(
            "library_entry__recording", "source_system"
        )
        .prefetch_related(
            "library_entry__recording__identifiers",
            "library_entry__recording__contributions__party",
            "library_entry__recording__contributions__artist_identity",
            Prefetch(
                "library_entry__recording__rights_claims",
                RightsClaim.objects.select_related(
                    "rights_holder",
                    "grantor",
                    "agreement",
                    "source_record__source_system",
                ).prefetch_related("territories", "decisions__decided_by"),
            ),
        )
        .order_by("library_entry__recording__title", "id")
    )
    local_organization = get_local_organization() if can_view_rights else None
    if form.is_valid():
        q = form.cleaned_data.get("q")
        if q:
            queryset = queryset.filter(
                Q(library_entry__recording__title__icontains=q)
                | Q(
                    library_entry__recording__identifiers__normalized_value__icontains=q
                )
                | Q(library_entry__recording__contributions__party__name__icontains=q)
            ).distinct()
        if form.cleaned_data.get("status"):
            queryset = queryset.filter(status=form.cleaned_data["status"])
        if form.cleaned_data.get("source"):
            queryset = queryset.filter(source_system=form.cleaned_data["source"])
        if can_view_rights:
            ownership = form.cleaned_data.get("ownership")
            recording_ids = tuple(
                queryset.values_list(
                    "library_entry__recording_id", flat=True
                ).distinct()
            )
            if ownership:
                summaries = ownership_summaries_for_recordings(
                    recording_ids, local_organization
                )
                matching_ids = [
                    recording_id
                    for recording_id, summary in summaries.items()
                    if summary.category == ownership
                ]
                queryset = queryset.filter(
                    library_entry__recording_id__in=matching_ids
                )
            for field_name, right_type in (
                (
                    "local_administration",
                    RightsClaim.RightType.ADMINISTRATION,
                ),
                ("local_distribution", RightsClaim.RightType.DISTRIBUTION),
            ):
                selected = form.cleaned_data.get(field_name)
                if not selected:
                    continue
                recording_ids = tuple(
                    queryset.values_list(
                        "library_entry__recording_id", flat=True
                    ).distinct()
                )
                matching_ids = local_confirmed_right_recording_ids(
                    recording_ids, local_organization, right_type
                )
                lookup = {"library_entry__recording_id__in": matching_ids}
                queryset = (
                    queryset.filter(**lookup)
                    if selected == "yes"
                    else queryset.exclude(**lookup)
                )
    page, query = _paginate(request, queryset)
    rows = list(page.object_list)
    page.object_list = rows
    for managed in rows:
        managed.primary_credit = _primary_credit(managed.recording)
    if can_view_rights:
        for managed in rows:
            claims = tuple(managed.recording.rights_claims.all())
            managed.ownership_summary = classify_ownership(
                claims, local_organization
            )
            managed.local_administration = has_local_confirmed_right(
                claims,
                local_organization,
                RightsClaim.RightType.ADMINISTRATION,
            )
            managed.local_distribution = has_local_confirmed_right(
                claims,
                local_organization,
                RightsClaim.RightType.DISTRIBUTION,
            )
    return render(
        request,
        "workbench/managed_list.html",
        _page_context(
            "managed",
            "Forvaltet musikk",
            form=form,
            form_help={
                "ownership": RIGHTS_HELP["ownership"],
                "local_administration": RIGHTS_HELP["administration"],
                "local_distribution": RIGHTS_HELP["distribution"],
            },
            page=page,
            page_query=query,
            sources=SourceSystem.objects.all(),
            local_organization=local_organization,
            ownership_categories=OwnershipCategory.choices,
        ),
    )


@staff
@permission_required("flac_ingest.add_flacingestbatch", raise_exception=True)
def flac_ingest_start(request):
    form = FlacScanForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            batch = scan_directory(user=request.user, **form.cleaned_data)
        except (ImproperlyConfigured, ValidationError) as error:
            form.add_error(None, error)
        else:
            messages.success(
                request,
                f"{batch.items.count()} FLAC-filer er lest. Kontroller forhåndsvisningen før bruk.",
            )
            return redirect("workbench:flac_ingest_preview", pk=batch.pk)
    return render(
        request,
        "workbench/flac_ingest_start.html",
        _page_context(
            "library",
            "Les inn fra musikkarkiv",
            form=form,
            cancel_url=reverse("workbench:library"),
        ),
    )


@staff
@permission_required("flac_ingest.view_flacingestbatch", raise_exception=True)
def flac_ingest_preview(request, pk):
    batch = get_object_or_404(
        FlacIngestBatch.objects.select_related("created_by"),
        pk=pk,
    )
    queryset = batch.items.select_related("recording", "release", "file_asset")
    counts = {
        action: batch.items.filter(action=action).count()
        for action, _label in FlacIngestItem.Action.choices
    }
    selected_action = request.GET.get("status", "")
    warning_queryset = batch.items.exclude(messages=[]).exclude(
        action__in=(
            FlacIngestItem.Action.CONFLICT,
            FlacIngestItem.Action.INVALID,
            FlacIngestItem.Action.RETRY,
        )
    )
    counts["warning"] = warning_queryset.count()
    valid_actions = {action for action, _label in FlacIngestItem.Action.choices}
    if selected_action == "warning":
        queryset = queryset.exclude(messages=[]).exclude(
            action__in=(
                FlacIngestItem.Action.CONFLICT,
                FlacIngestItem.Action.INVALID,
                FlacIngestItem.Action.RETRY,
            )
        )
    elif selected_action in valid_actions:
        queryset = queryset.filter(action=selected_action)
    else:
        selected_action = ""
    page, page_query = _paginate(request, queryset, per_page=100)
    items = list(page.object_list)
    return render(
        request,
        "workbench/flac_ingest_preview.html",
        _page_context(
            "library",
            "Forhåndsvis FLAC-innlesing",
            batch=batch,
            items=items,
            total_count=batch.items.count(),
            counts=counts,
            selected_action=selected_action,
            page=page,
            page_query=page_query,
            applicable_count=batch.items.filter(
                action__in=(
                    FlacIngestItem.Action.NEW,
                    FlacIngestItem.Action.MATCHED,
                    FlacIngestItem.Action.UPDATED,
                ),
                applied_at__isnull=True,
            ).count(),
            cancel_url=reverse("workbench:library"),
        ),
    )


@staff
@permission_required(
    (
        "flac_ingest.view_flacingestbatch",
        "flac_ingest.apply_flacingestbatch",
    ),
    raise_exception=True,
)
def flac_ingest_review(request, pk, item_pk):
    batch = get_object_or_404(FlacIngestBatch, pk=pk)
    item = get_object_or_404(
        FlacIngestItem.objects.select_related("recording"),
        pk=item_pk,
        batch=batch,
        action=FlacIngestItem.Action.CONFLICT,
        applied_at__isnull=True,
    )
    form = FlacIngestReviewForm(request.POST or None, item=item)
    cancel_url = _safe_return(
        request, reverse("workbench:flac_ingest_preview", args=[batch.pk])
    )
    if request.method == "POST" and form.is_valid():
        try:
            review_item(
                item,
                parsed=form.interpreted_metadata(),
                resolution=form.cleaned_data["resolution"],
                user=request.user,
                note=form.cleaned_data["review_note"],
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(
                request,
                "Tolkningen er kontrollert. Filen er nå klar til import.",
            )
            return redirect(cancel_url + f"#item-{item.pk}")
    return render(
        request,
        "workbench/flac_ingest_review.html",
        _page_context(
            "library",
            "Kontroller FLAC-tolkning",
            batch=batch,
            item=item,
            form=form,
            cancel_url=cancel_url,
        ),
    )


@staff
@require_POST
@permission_required(
    (
        "flac_ingest.add_flacingestbatch",
        "flac_ingest.view_flacingestbatch",
    ),
    raise_exception=True,
)
def flac_ingest_rescan(request, pk):
    batch = get_object_or_404(FlacIngestBatch, pk=pk)
    mode = request.POST.get("mode", "all")
    relative_paths = None
    if mode == "failed":
        relative_paths = list(
            batch.items.filter(
                action__in=(
                    FlacIngestItem.Action.INVALID,
                    FlacIngestItem.Action.RETRY,
                )
            ).values_list("relative_path", flat=True)
        )
    elif mode == "selected":
        try:
            selected_ids = [
                UUID(value) for value in request.POST.getlist("items")[:500]
            ]
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Ugyldig filvalg.")
        relative_paths = list(
            batch.items.filter(pk__in=selected_ids).values_list(
                "relative_path", flat=True
            )
        )
    elif mode != "all":
        return HttpResponseBadRequest("Ukjent type ny skanning.")
    if relative_paths == []:
        messages.warning(request, "Ingen aktuelle filer ble valgt for ny skanning.")
        return redirect("workbench:flac_ingest_preview", pk=batch.pk)
    try:
        new_batch = scan_directory(
            relative_root=batch.relative_root,
            recursive=batch.recursive,
            relative_paths=relative_paths,
            user=request.user,
        )
    except (ImproperlyConfigured, ValidationError) as error:
        details = error.messages if hasattr(error, "messages") else [str(error)]
        messages.error(request, "; ".join(details))
        return redirect("workbench:flac_ingest_preview", pk=batch.pk)
    messages.success(request, f"{new_batch.items.count()} filer ble skannet på nytt.")
    return redirect("workbench:flac_ingest_preview", pk=new_batch.pk)


@staff
@require_POST
@permission_required(
    (
        "flac_ingest.view_flacingestbatch",
        "flac_ingest.apply_flacingestbatch",
    ),
    raise_exception=True,
)
def flac_ingest_apply(request, pk):
    batch = get_object_or_404(FlacIngestBatch, pk=pk)
    try:
        applied = apply_batch(batch, user=request.user)
    except (ImproperlyConfigured, ValidationError) as error:
        details = error.messages if hasattr(error, "messages") else [str(error)]
        messages.error(request, "; ".join(details))
    else:
        messages.success(request, f"{applied} FLAC-filer ble brukt i Musikkarkivet.")
    return redirect("workbench:flac_ingest_preview", pk=batch.pk)


@staff
@permission_required("managed_music.add_managedrecording", raise_exception=True)
def managed_add(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden("Bare administrator kan registrere forvaltning.")
    initial = {}
    if request.GET.get("recording"):
        initial["recording"] = request.GET["recording"]
    form = ManagedRecordingCreationForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            managed = create_managed_recording(**form.cleaned_data)
        except (ValueError, IntegrityError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(
                request,
                "Innspillingen ble uttrykkelig registrert i Forvaltet musikk og finnes også i Musikkarkivet.",
            )
            return redirect("workbench:recording", pk=managed.recording.pk)
    return render(
        request,
        "workbench/form.html",
        _page_context(
            "managed",
            "Legg til i Forvaltet musikk",
            form=form,
            submit_label="Registrer forvaltning",
            cancel_url=reverse("workbench:managed"),
            introduction="Dette registrerer forvaltning, ikke at P7 eier masteren.",
        ),
    )


@staff
@permission_required("catalogue.view_release", raise_exception=True)
def release_list(request):
    form = ReleaseFilterForm(request.GET)
    queryset = (
        Release.objects.select_related("label")
        .prefetch_related("identifiers")
        .annotate(track_total=Count("tracks", distinct=True))
        .order_by("title", "id")
    )
    if form.is_valid():
        q = form.cleaned_data.get("q")
        if q:
            queryset = queryset.filter(
                Q(title__icontains=q)
                | Q(catalogue_number__icontains=q)
                | Q(label__name__icontains=q)
                | Q(identifiers__normalized_value__icontains=q)
                | Q(tracks__recording__title__icontains=q)
                | Q(tracks__recording__identifiers__normalized_value__icontains=q)
                | Q(tracks__recording__contributions__party__name__icontains=q)
                | Q(
                    tracks__recording__contributions__artist_identity__display_name__icontains=q
                )
            ).distinct()
        if form.cleaned_data.get("release_type"):
            queryset = queryset.filter(release_type=form.cleaned_data["release_type"])
        if form.cleaned_data.get("status"):
            queryset = queryset.filter(verification_status=form.cleaned_data["status"])
        source = form.cleaned_data.get("source")
        if source:
            release_ids = MetadataAssertion.objects.filter(
                entity_type=MetadataAssertion.EntityType.RELEASE,
                source_record__source_system=source,
            ).values("entity_uuid")
            queryset = queryset.filter(pk__in=release_ids)
    page, query = _paginate(request, queryset)
    return render(
        request,
        "workbench/release_list.html",
        _page_context(
            "releases",
            "Utgivelser",
            form=form,
            page=page,
            page_query=query,
        ),
    )


@staff
@permission_required("catalogue.add_release", raise_exception=True)
def release_add(request):
    form = ReleaseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        release = form.save()
        messages.success(request, "Utgivelsen ble opprettet.")
        return redirect("workbench:release", pk=release.pk)
    return render(
        request,
        "workbench/form.html",
        _page_context(
            "releases",
            "Ny utgivelse",
            form=form,
            submit_label="Opprett utgivelse",
            cancel_url=reverse("workbench:releases"),
        ),
    )


@staff
@permission_required("catalogue.view_release", raise_exception=True)
def release_detail(request, pk):
    release = get_object_or_404(
        Release.objects.select_related("label").prefetch_related(
            "identifiers",
            Prefetch(
                "tracks",
                ReleaseTrack.objects.select_related("recording").prefetch_related(
                    "recording__identifiers",
                    "recording__contributions__party",
                    "recording__contributions__artist_identity",
                ),
            ),
            Prefetch("file_assets", FileAsset.objects.prefetch_related("locations")),
        ),
        pk=pk,
    )
    for track in release.tracks.all():
        track.primary_credit = _primary_credit(track.recording)
    form = None
    if request.method == "POST" and not request.user.has_perm(
        "catalogue.change_release"
    ):
        return HttpResponseForbidden("Du kan ikke endre utgivelsen.")
    if request.user.has_perm("catalogue.change_release"):
        form = ReleaseForm(request.POST or None, instance=release)
        if request.method == "POST" and form.is_valid():
            form.save()
            recordings = Recording.objects.filter(
                release_tracks__release=release
            ).distinct()
            for recording in recordings:
                sync_recording_files(recording)
            messages.success(request, "Utgivelsen ble lagret.")
            return redirect(
                _safe_return(request, reverse("workbench:release", args=(pk,)))
            )
    assertions = MetadataAssertion.objects.filter(
        entity_type=MetadataAssertion.EntityType.RELEASE, entity_uuid=release.pk
    ).select_related("source_record__source_system")
    return render(
        request,
        "workbench/release_detail.html",
        _page_context(
            "releases",
            release.title,
            release=release,
            form=form,
            assertions=assertions,
            return_url=_safe_return(request, reverse("workbench:releases")),
        ),
    )


@staff
@permission_required("catalogue.change_release", raise_exception=True)
@permission_required("catalogue.add_releasetrack", raise_exception=True)
def release_tracks(request, pk):
    release = get_object_or_404(Release, pk=pk)
    formset = TrackFormSet(request.POST or None, prefix="tracks")
    if request.method == "POST" and formset.is_valid():
        changed_forms = [form for form in formset if form.has_changed()]
        if not changed_forms:
            formset._non_form_errors = formset.error_class(
                ["Fyll ut minst ett spor."], renderer=formset.renderer
            )
        else:
            sequences = [form.cleaned_data["sequence_number"] for form in changed_forms]
            used = set(
                release.tracks.filter(sequence_number__in=sequences).values_list(
                    "sequence_number", flat=True
                )
            )
            duplicates = {value for value in sequences if sequences.count(value) > 1}
            if used or duplicates:
                for form in changed_forms:
                    sequence = form.cleaned_data.get("sequence_number")
                    if sequence in used:
                        form.add_error(
                            "sequence_number",
                            "Rekkefølgen finnes allerede på utgivelsen.",
                        )
                    elif sequence in duplicates:
                        form.add_error(
                            "sequence_number",
                            "Rekkefølgen er brukt i flere utfylte rader.",
                        )
                formset._non_form_errors = formset.error_class(
                    ["Rett rekkefølgen i de markerte radene."],
                    renderer=formset.renderer,
                )
            else:
                failed_form = None
                try:
                    with transaction.atomic():
                        for form in changed_forms:
                            failed_form = form
                            values = dict(form.cleaned_data)
                            values.pop("existing_recording_search", None)
                            values.pop("duration_display", None)
                            create_release_track(release=release, **values)
                except ValueError as error:
                    failed_form.add_error("new_recording_title", str(error))
                except IntegrityError:
                    failed_form.add_error(
                        None,
                        "Sporet kunne ikke lagres. Kontroller posisjon, ISRC og koblet innspilling.",
                    )
                else:
                    messages.success(
                        request, f"{len(changed_forms)} spor ble registrert samlet."
                    )
                    return redirect(
                        _safe_return(
                            request, reverse("workbench:release", args=(release.pk,))
                        )
                    )
    next_sequence = (
        release.tracks.order_by("-sequence_number")
        .values_list("sequence_number", flat=True)
        .first()
        or 0
    ) + 1
    return render(
        request,
        "workbench/release_tracks.html",
        _page_context(
            "releases",
            f"Registrer spor — {release.title}",
            release=release,
            formset=formset,
            next_sequence=next_sequence,
            return_url=_safe_return(
                request, reverse("workbench:release", args=(release.pk,))
            ),
        ),
    )


def _recording_queryset():
    return Recording.objects.prefetch_related(
        "identifiers",
        Prefetch(
            "contributions",
            RecordingContribution.objects.select_related("party", "artist_identity"),
        ),
        Prefetch(
            "release_tracks",
            ReleaseTrack.objects.select_related("release", "release__label"),
        ),
        Prefetch("file_assets", FileAsset.objects.prefetch_related("locations")),
        "music_library_entry__channels",
        "music_library_entry__target_audiences",
    )


@staff
@permission_required("catalogue.view_recording", raise_exception=True)
def recording_detail(request, pk):
    recording = get_object_or_404(_recording_queryset(), pk=pk)
    tab = request.GET.get("fane", "overview")
    allowed_tabs = {"overview", "radio", "releases", "contributors", "files", "sources"}
    if request.user.has_perm("rights.view_rightsclaim"):
        allowed_tabs.add("rights")
    if tab not in allowed_tabs:
        tab = "overview"
    assertions = (
        MetadataAssertion.objects.filter(
            entity_type=MetadataAssertion.EntityType.RECORDING,
            entity_uuid=recording.pk,
        )
        .select_related("source_record__source_system")
        .prefetch_related("decisions__decided_by", "applied_changes__changed_by")
    )
    changes = AppliedMetadataChange.objects.filter(
        entity_type=MetadataAssertion.EntityType.RECORDING,
        entity_uuid=recording.pk,
    ).select_related("assertion__source_record__source_system", "changed_by")
    rights_claims = []
    local_organization = None
    ownership_summary = None
    if request.user.has_perm("rights.view_rightsclaim"):
        local_organization = get_local_organization()
        rights_claims = list(
            RightsClaim.objects.filter(recording=recording)
            .select_related(
                "rights_holder", "grantor", "agreement", "source_record__source_system"
            )
            .prefetch_related("territories", "decisions__decided_by")
        )
        ownership_summary = classify_ownership(
            rights_claims, local_organization
        )
    return render(
        request,
        "workbench/recording_detail.html",
        _page_context(
            "library",
            recording.title,
            recording=recording,
            tab=tab,
            assertions=assertions,
            applied_changes=changes,
            ownership_claims=[
                claim
                for claim in rights_claims
                if claim.right_type == RightsClaim.RightType.OWNERSHIP
                and claim.status == VerificationStatus.CONFIRMED
            ],
            administration_claims=[
                claim
                for claim in rights_claims
                if claim.right_type == RightsClaim.RightType.ADMINISTRATION
                and claim.status == VerificationStatus.CONFIRMED
            ],
            distribution_claims=[
                claim
                for claim in rights_claims
                if claim.right_type == RightsClaim.RightType.DISTRIBUTION
                and claim.status == VerificationStatus.CONFIRMED
            ],
            unresolved_claims=[
                claim
                for claim in rights_claims
                if claim.status
                in {VerificationStatus.UNVERIFIED, VerificationStatus.DISPUTED}
            ],
            historical_claims=[
                claim
                for claim in rights_claims
                if claim.status
                in {VerificationStatus.REJECTED, VerificationStatus.SUPERSEDED}
            ],
            local_organization=local_organization,
            ownership_summary=ownership_summary,
            return_url=_safe_return(request, reverse("workbench:library")),
        ),
    )


@staff
@permission_required(
    ("catalogue.view_recording", "rights.view_rightsclaim", "rights.add_rightsclaim"),
    raise_exception=True,
)
def rights_claim_add(request, pk):
    recording = get_object_or_404(Recording, pk=pk)
    form = RightsClaimForm(request.POST or None)
    if not request.user.has_perm("rights.view_agreement"):
        form.fields["agreement"].queryset = Agreement.objects.none()
    if not request.user.has_perm("provenance.view_sourcerecord"):
        form.fields["source_record"].queryset = form.fields[
            "source_record"
        ].queryset.none()
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data.copy()
        territories = data.pop("territories")
        try:
            create_rights_claim(
                recording=recording, territories=territories, **data
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(
                request, "Rettighetskravet ble registrert som ikke verifisert."
            )
            return redirect(
                reverse("workbench:recording", args=(recording.pk,)) + "?fane=rights"
            )
    return render(
        request,
        "workbench/form.html",
        _page_context(
            "rights",
            f"Nytt rettighetskrav — {recording.title}",
            introduction="Et nytt krav er uverifisert til en autorisert bruker tar en beslutning.",
            form=form,
            form_help=RIGHTS_FORM_HELP,
            submit_label="Registrer krav",
            cancel_url=reverse("workbench:recording", args=(recording.pk,))
            + "?fane=rights",
        ),
    )


@staff
@require_POST
@permission_required(
    ("rights.view_rightsclaim", "rights.decide_rightsclaim"), raise_exception=True
)
def rights_claim_decide(request, pk):
    claim = get_object_or_404(RightsClaim, pk=pk)
    form = RightsDecisionForm(request.POST)
    if form.is_valid():
        decisions = {
            "confirm": VerificationStatus.CONFIRMED,
            "dispute": VerificationStatus.DISPUTED,
            "reject": VerificationStatus.REJECTED,
        }
        try:
            decide_rights_claim(
                claim,
                decisions[form.cleaned_data["action"]],
                user=request.user,
                note=form.cleaned_data["note"],
            )
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
        else:
            messages.success(request, "Rettighetsbeslutningen ble loggført.")
    else:
        messages.error(request, "Rettighetsbeslutningen var ikke gyldig.")
    return redirect(
        _safe_return(
            request,
            reverse("workbench:recording", args=(claim.recording_id,)) + "?fane=rights",
        )
    )


@staff
@permission_required(
    (
        "rights.view_rightsclaim",
        "rights.add_rightsclaim",
        "rights.decide_rightsclaim",
    ),
    raise_exception=True,
)
def rights_claim_supersede(request, pk):
    previous = get_object_or_404(RightsClaim, pk=pk)
    initial = {
        "right_type": previous.right_type,
        "rights_holder": previous.rights_holder,
        "grantor": previous.grantor,
        "share": previous.share,
        "territory_mode": previous.territory_mode,
        "territories": previous.territories.all(),
        "valid_from": previous.valid_from,
        "valid_until": previous.valid_until,
        "evidence_strength": previous.evidence_strength,
        "source_record": previous.source_record,
        "agreement": previous.agreement,
        "notes": previous.notes,
    }
    form = RightsClaimForm(request.POST or None, initial=initial)
    form.fields["right_type"].disabled = True
    if not request.user.has_perm("rights.view_agreement"):
        form.fields["agreement"].queryset = Agreement.objects.filter(
            pk=previous.agreement_id
        )
        form.fields["agreement"].disabled = True
    if not request.user.has_perm("provenance.view_sourcerecord"):
        form.fields["source_record"].queryset = form.fields[
            "source_record"
        ].queryset.filter(pk=previous.source_record_id)
        form.fields["source_record"].disabled = True
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data.copy()
        territories = data.pop("territories")
        data.pop("right_type", None)
        try:
            replacement = supersede_rights_claim(
                previous,
                user=request.user,
                territories=territories,
                note="Erstattet gjennom arbeidsgrensesnittet",
                **data,
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Det tidligere kravet er bevart og erstattet.")
            return redirect(
                reverse("workbench:recording", args=(replacement.recording_id,))
                + "?fane=rights"
            )
    return render(
        request,
        "workbench/form.html",
        _page_context(
            "rights",
            "Erstatt rettighetskrav",
            introduction="Det tidligere kravet og beslutningshistorikken blir bevart.",
            form=form,
            form_help=RIGHTS_FORM_HELP,
            submit_label="Opprett erstatningskrav",
            cancel_url=reverse("workbench:recording", args=(previous.recording_id,))
            + "?fane=rights",
        ),
    )


@staff
@permission_required(
    (
        "rights.view_rightsclaim",
        "rights.change_rightsclaim",
        "rights.manage_agreement",
    ),
    raise_exception=True,
)
def rights_claim_link_agreement(request, pk):
    claim = get_object_or_404(RightsClaim, pk=pk)
    form = ClaimAgreementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        link_claim_agreement(
            claim,
            form.cleaned_data["agreement"],
            user=request.user,
            note=form.cleaned_data["note"],
        )
        messages.success(
            request, "Avtalen ble knyttet til kravet og handlingen loggført."
        )
        return redirect(
            reverse("workbench:recording", args=(claim.recording_id,)) + "?fane=rights"
        )
    return render(
        request,
        "workbench/form.html",
        _page_context(
            "rights",
            "Knytt avtale",
            form=form,
            form_help={"agreement": RIGHTS_HELP["agreement"]},
            submit_label="Knytt avtale",
            cancel_url=reverse("workbench:recording", args=(claim.recording_id,))
            + "?fane=rights",
        ),
    )


@staff
@permission_required("rights.view_agreement", raise_exception=True)
def agreement_list(request):
    form = SearchForm(request.GET)
    agreements = Agreement.objects.prefetch_related("party_roles__party").order_by(
        "title", "id"
    )
    if form.is_valid() and form.cleaned_data.get("q"):
        q = form.cleaned_data["q"]
        agreements = agreements.filter(
            Q(title__icontains=q)
            | Q(internal_reference__icontains=q)
            | Q(party_roles__party__name__icontains=q)
        ).distinct()
    page, query = _paginate(request, agreements)
    return render(
        request,
        "workbench/agreement_list.html",
        _page_context("rights", "Avtaler", form=form, page=page, page_query=query),
    )


@staff
@permission_required("rights.view_agreement", raise_exception=True)
def agreement_detail(request, pk):
    agreement = get_object_or_404(
        Agreement.objects.prefetch_related(
            "party_roles__party",
            "document_links__file_asset",
            "rights_claims__recording",
        ),
        pk=pk,
    )
    return render(
        request,
        "workbench/agreement_detail.html",
        _page_context("rights", agreement.title, agreement=agreement),
    )


@staff
@permission_required(
    ("rights.view_agreement", "rights.manage_agreement"), raise_exception=True
)
def agreement_add(request):
    form = AgreementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        agreement = form.save()
        messages.success(request, "Avtalen ble registrert.")
        return redirect("workbench:agreement", pk=agreement.pk)
    return render(
        request,
        "workbench/form.html",
        _page_context(
            "rights",
            "Ny avtale",
            form=form,
            submit_label="Registrer avtale",
            cancel_url=reverse("workbench:agreements"),
        ),
    )


@staff
@permission_required(
    ("rights.view_agreement", "rights.manage_agreement"), raise_exception=True
)
def agreement_edit(request, pk):
    agreement = get_object_or_404(Agreement, pk=pk)
    form = AgreementForm(request.POST or None, instance=agreement)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Avtalen ble oppdatert.")
        return redirect("workbench:agreement", pk=agreement.pk)
    return render(
        request,
        "workbench/form.html",
        _page_context(
            "rights",
            f"Rediger avtale — {agreement.title}",
            form=form,
            submit_label="Lagre avtale",
            cancel_url=reverse("workbench:agreement", args=(agreement.pk,)),
        ),
    )


@staff
@permission_required(
    ("rights.view_agreement", "rights.manage_agreement"), raise_exception=True
)
def agreement_party_add(request, pk):
    agreement = get_object_or_404(Agreement, pk=pk)
    form = AgreementPartyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        role = form.save(commit=False)
        role.agreement = agreement
        role.save()
        messages.success(request, "Avtaleparten ble registrert.")
        return redirect("workbench:agreement", pk=agreement.pk)
    return render(
        request,
        "workbench/form.html",
        _page_context(
            "rights",
            f"Ny avtalepart — {agreement.title}",
            form=form,
            submit_label="Registrer avtalepart",
            cancel_url=reverse("workbench:agreement", args=(agreement.pk,)),
        ),
    )


@staff
@permission_required(
    ("rights.view_agreement", "rights.manage_agreement"), raise_exception=True
)
def agreement_document_add(request, pk):
    agreement = get_object_or_404(Agreement, pk=pk)
    form = AgreementDocumentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        document = form.save(commit=False)
        document.agreement = agreement
        document.save()
        messages.success(request, "Avtaledokumentet ble knyttet til avtalen.")
        return redirect("workbench:agreement", pk=agreement.pk)
    return render(
        request,
        "workbench/form.html",
        _page_context(
            "rights",
            f"Knytt dokument — {agreement.title}",
            form=form,
            submit_label="Knytt dokument",
            cancel_url=reverse("workbench:agreement", args=(agreement.pk,)),
        ),
    )


@staff
@permission_required("catalogue.change_recording", raise_exception=True)
def recording_edit(request, pk):
    recording = get_object_or_404(Recording, pk=pk)
    form = RecordingForm(request.POST or None, instance=recording)
    if request.method == "POST" and form.is_valid():
        form.save()
        sync_results = sync_recording_files(recording)
        if any(result and result.result != "success" for result in sync_results):
            messages.warning(
                request,
                "Katalogendringen er lagret, men én eller flere radiofiler venter på synkronisering.",
            )
        messages.success(request, "Innspillingen ble lagret.")
        return redirect(
            _safe_return(request, reverse("workbench:recording", args=(pk,)))
        )
    return render(
        request,
        "workbench/form.html",
        _page_context(
            "library",
            f"Rediger — {recording.title}",
            form=form,
            submit_label="Lagre innspilling",
            cancel_url=reverse("workbench:recording", args=(pk,)),
            return_url=_safe_return(
                request, reverse("workbench:recording", args=(pk,))
            ),
        ),
    )


@staff
@permission_required("catalogue.add_externalidentifier", raise_exception=True)
def recording_identifier_add(request, pk):
    recording = get_object_or_404(Recording, pk=pk)
    form = RecordingIdentifierForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        identifier = form.save(commit=False)
        identifier.recording = recording
        identifier.save()
        sync_recording_files(recording)
        messages.success(request, "Identifikatoren ble registrert.")
        return redirect("workbench:recording", pk=pk)
    return render(
        request,
        "workbench/form.html",
        _page_context(
            "library",
            f"Ny identifikator — {recording.title}",
            form=form,
            submit_label="Registrer identifikator",
            cancel_url=reverse("workbench:recording", args=(pk,)),
        ),
    )


@staff
@permission_required("catalogue.add_recordingcontribution", raise_exception=True)
def contribution_add(request, pk):
    recording = get_object_or_404(Recording, pk=pk)
    form = ContributionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        contribution = form.save(commit=False)
        contribution.recording = recording
        contribution.save()
        sync_recording_files(recording)
        messages.success(request, "Den medvirkende ble registrert.")
        return redirect(
            reverse("workbench:recording", args=(pk,)) + "?fane=contributors"
        )
    return render(
        request,
        "workbench/form.html",
        _page_context(
            "library",
            f"Ny medvirkende — {recording.title}",
            form=form,
            submit_label="Registrer medvirkende",
            cancel_url=reverse("workbench:recording", args=(pk,))
            + "?fane=contributors",
        ),
    )


@staff
@permission_required("music_library.change_musiclibraryentry", raise_exception=True)
def radio_edit(request, pk):
    recording = get_object_or_404(Recording, pk=pk)
    entry = get_object_or_404(MusicLibraryEntry, recording=recording)
    detail_url = reverse("workbench:recording", args=(pk,)) + "?fane=radio"
    return_url = _safe_return(request, detail_url)
    form = RadioMetadataForm(request.POST or None, instance=entry)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Radiometadata ble lagret.")
        return redirect(return_url)
    return render(
        request,
        "workbench/radio_metadata_form.html",
        _page_context(
            "library",
            f"Radiometadata — {recording.title}",
            form=form,
            submit_label="Lagre radiometadata",
            cancel_url=return_url,
            return_url=return_url,
        ),
    )


@staff
@require_POST
@permission_required("provenance.change_metadataassertion", raise_exception=True)
def assertion_action(request, pk):
    assertion = get_object_or_404(MetadataAssertion, pk=pk)
    form = AssertionActionForm(request.POST)
    fallback = reverse("workbench:control")
    if not form.is_valid():
        messages.error(request, "Handlingen mangler gyldige opplysninger.")
        return redirect(_safe_return(request, fallback))
    action = form.cleaned_data["action"]
    note = form.cleaned_data["note"]
    try:
        if action in {"apply", "confirm_apply"}:
            if not request.user.has_perm("catalogue.change_recording"):
                return HttpResponseForbidden("Du kan ikke endre katalogverdien.")
            apply_assertion(
                assertion,
                expected_revision=form.cleaned_data["expected_revision"],
                user=request.user,
                confirm=action == "confirm_apply",
                note=note,
            )
            messages.success(
                request, "Kildeverdien ble brukt som gjeldende katalogverdi."
            )
        elif action == "confirm":
            decide_assertion(
                assertion, VerificationStatus.CONFIRMED, user=request.user, note=note
            )
            messages.success(request, "Kildeopplysningen ble bekreftet.")
        elif action == "dispute":
            decide_assertion(
                assertion, VerificationStatus.DISPUTED, user=request.user, note=note
            )
            messages.success(request, "Kildeopplysningen ble markert som bestridt.")
        elif action == "reject":
            decide_assertion(
                assertion, VerificationStatus.REJECTED, user=request.user, note=note
            )
            messages.success(request, "Kildeopplysningen ble avvist.")
        elif action == "correct":
            correct_assertion(
                assertion,
                raw_value=form.cleaned_data["correction"],
                user=request.user,
                note=note,
            )
            messages.success(request, "En korrigert kildeopplysning ble opprettet.")
    except (ValidationError, ValueError, IntegrityError) as error:
        messages.error(request, str(error))
    return redirect(_safe_return(request, fallback))


@staff
@permission_required("catalogue.add_externalidentifier", raise_exception=True)
def release_identifier_add(request, pk):
    release = get_object_or_404(Release, pk=pk)
    form = ReleaseIdentifierForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        identifier = form.save(commit=False)
        identifier.release = release
        identifier.save()
        messages.success(request, "Identifikatoren ble registrert.")
        return redirect("workbench:release", pk=pk)
    return render(
        request,
        "workbench/form.html",
        _page_context(
            "releases",
            f"Ny identifikator — {release.title}",
            form=form,
            submit_label="Registrer identifikator",
            cancel_url=reverse("workbench:release", args=(pk,)),
        ),
    )


@staff
@permission_required("catalogue.view_recording", raise_exception=True)
def recording_search(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})
    recordings = (
        Recording.objects.filter(
            Q(title__icontains=q)
            | Q(identifiers__normalized_value__icontains=q)
            | Q(contributions__party__name__icontains=q)
            | Q(contributions__artist_identity__display_name__icontains=q)
        )
        .prefetch_related("identifiers")
        .distinct()
        .order_by("title", "id")[:20]
    )
    return JsonResponse(
        {
            "results": [
                {
                    "id": str(recording.pk),
                    "text": recording.title,
                    "meta": ", ".join(
                        identifier.normalized_value
                        for identifier in recording.identifiers.all()
                        if identifier.scheme == "ISRC"
                    ),
                }
                for recording in recordings
            ]
        }
    )


@staff
def parties_list(request):
    if not (
        request.user.has_perm("parties.view_party")
        or request.user.has_perm("parties.view_artistidentity")
    ):
        return HttpResponseForbidden("Du har ikke tilgang til personer og artister.")
    form = SearchForm(request.GET)
    parties = Party.objects.prefetch_related("artist_identities")
    artists = ArtistIdentity.objects.select_related("party")
    if form.is_valid() and form.cleaned_data.get("q"):
        q = form.cleaned_data["q"]
        parties = parties.filter(
            Q(name__icontains=q) | Q(artist_identities__display_name__icontains=q)
        ).distinct()
        artists = artists.filter(
            Q(display_name__icontains=q) | Q(party__name__icontains=q)
        )
    party_page, query = _paginate(request, parties, 30)
    return render(
        request,
        "workbench/parties.html",
        _page_context(
            "parties",
            "Artister og personer",
            form=form,
            page=party_page,
            artists=artists[:50],
            page_query=query,
        ),
    )


@staff
@permission_required("parties.add_party", raise_exception=True)
def party_add(request):
    form = PartyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Personen eller organisasjonen ble opprettet.")
        return redirect("workbench:parties")
    return render(
        request,
        "workbench/form.html",
        _page_context(
            "parties",
            "Ny person eller organisasjon",
            form=form,
            submit_label="Opprett",
            cancel_url=reverse("workbench:parties"),
        ),
    )


@staff
@permission_required("parties.add_artistidentity", raise_exception=True)
def artist_add(request):
    form = ArtistIdentityForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Artistidentiteten ble opprettet.")
        return redirect("workbench:parties")
    return render(
        request,
        "workbench/form.html",
        _page_context(
            "parties",
            "Ny artistidentitet",
            form=form,
            submit_label="Opprett",
            cancel_url=reverse("workbench:parties"),
        ),
    )


@staff
def control(request):
    if not (
        request.user.has_perm("catalogue.view_duplicatecandidate")
        or request.user.has_perm("provenance.view_metadataassertion")
    ):
        return HttpResponseForbidden("Du har ikke tilgang til kontrolloppgavene.")
    selected = request.GET.get("type", "dubletter")
    duplicates = DuplicateCandidate.objects.none()
    assertions = MetadataAssertion.objects.none()
    if request.user.has_perm("catalogue.view_duplicatecandidate"):
        duplicates = DuplicateCandidate.objects.filter(status="open").select_related(
            "recording_a", "recording_b"
        )
    if request.user.has_perm("provenance.view_metadataassertion"):
        assertions = MetadataAssertion.objects.select_related(
            "source_record__source_system"
        )
        if selected == "konflikter":
            assertions = assertions.filter(status=VerificationStatus.DISPUTED)
        else:
            assertions = assertions.filter(status=VerificationStatus.UNVERIFIED)
    flac_unverified_count = 0
    if request.user.has_perm("provenance.change_metadataassertion"):
        flac_unverified_count = MetadataAssertion.objects.filter(
            source_record__source_system__name=SOURCE_SYSTEM_NAME,
            source_record__external_record_id__startswith="flac:",
            status=VerificationStatus.UNVERIFIED,
        ).count()
    return render(
        request,
        "workbench/control.html",
        _page_context(
            "control",
            "Kontroll",
            selected=selected,
            duplicates=duplicates[:100],
            assertions=assertions[:100],
            flac_unverified_count=flac_unverified_count,
        ),
    )


@require_POST
@staff
@permission_required("provenance.change_metadataassertion", raise_exception=True)
def confirm_flac_metadata(request):
    count = confirm_existing_flac_assertions(user=request.user)
    if count:
        messages.success(
            request,
            f"{count} tidligere anvendte FLAC-opplysninger ble bekreftet.",
        )
    else:
        messages.info(request, "Ingen uverifiserte FLAC-opplysninger gjenstod.")
    return redirect(reverse("workbench:control") + "?type=uverifisert")


@staff
@permission_required("media_assets.view_fileasset", raise_exception=True)
def files_list(request):
    form = SearchForm(request.GET)
    queryset = FileAsset.objects.select_related(
        "recording", "release"
    ).prefetch_related("locations")
    if form.is_valid() and form.cleaned_data.get("q"):
        q = form.cleaned_data["q"]
        queryset = queryset.filter(
            Q(filename__icontains=q)
            | Q(recording__title__icontains=q)
            | Q(release__title__icontains=q)
            | Q(locations__relative_path__icontains=q)
        ).distinct()
    page, query = _paginate(request, queryset)
    return render(
        request,
        "workbench/files.html",
        _page_context("files", "Filer", form=form, page=page, page_query=query),
    )


@staff
@permission_required("media_assets.view_fileasset", raise_exception=True)
def file_detail(request, pk):
    asset = get_object_or_404(
        FileAsset.objects.select_related("recording", "release").prefetch_related(
            "locations"
        ),
        pk=pk,
    )
    form = None
    if request.method == "POST" and not request.user.has_perm(
        "media_assets.change_fileasset"
    ):
        return HttpResponseForbidden("Du kan ikke endre filreferansen.")
    if request.user.has_perm("media_assets.change_fileasset"):
        form = FileAssetForm(request.POST or None, instance=asset)
        if request.method == "POST" and form.is_valid():
            form.save()
            messages.success(request, "Filreferansen ble lagret.")
            return redirect(
                _safe_return(request, reverse("workbench:file", args=(pk,)))
            )
    return render(
        request,
        "workbench/file_detail.html",
        _page_context(
            "files",
            asset.filename,
            asset=asset,
            form=form,
            return_url=_safe_return(request, reverse("workbench:files")),
        ),
    )


@staff
@permission_required("media_assets.add_fileasset", raise_exception=True)
def file_add(request):
    form = FileAssetForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        asset = form.save()
        messages.success(
            request, "Filreferansen ble opprettet. Filen er ikke kontrollert av dette."
        )
        return redirect("workbench:file", pk=asset.pk)
    return render(
        request,
        "workbench/form.html",
        _page_context(
            "files",
            "Ny filreferanse",
            form=form,
            submit_label="Opprett referanse",
            cancel_url=reverse("workbench:files"),
        ),
    )


@staff
@permission_required("media_assets.add_filelocation", raise_exception=True)
def file_location_add(request, pk):
    asset = get_object_or_404(FileAsset, pk=pk)
    form = FileLocationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        location = form.save(commit=False)
        location.asset = asset
        location.save()
        messages.success(
            request,
            "Filplasseringen ble registrert. Stien er ikke kontrollert automatisk.",
        )
        return redirect("workbench:file", pk=pk)
    return render(
        request,
        "workbench/form.html",
        _page_context(
            "files",
            f"Ny plassering — {asset.filename}",
            form=form,
            submit_label="Registrer plassering",
            cancel_url=reverse("workbench:file", args=(pk,)),
        ),
    )


@staff
def help_page(request):
    return render(
        request,
        "workbench/help.html",
        _page_context(
            "help",
            "Hjelp",
            rights_help_sections=RIGHTS_HELP_SECTIONS,
        ),
    )


@staff
@permission_required(
    ("media_assets.view_fileasset", "media_assets.view_filelocation"),
    raise_exception=True,
)
def cover_image(request, pk):
    """Read a bounded raster preview inside the configured NAS root only."""
    asset = get_object_or_404(FileAsset, pk=pk, role="cover_image")
    if not request.user.has_perm("music_library.view_musiclibraryentry"):
        return HttpResponseForbidden()
    if not settings.P7_NAS_ROOT:
        raise Http404
    root = Path(settings.P7_NAS_ROOT).resolve()
    for location in asset.locations.filter(
        storage_type="nas", is_current=True, status="active"
    )[:5]:
        try:
            path = location.resolved_nas_path().resolve()
            if not path.is_relative_to(root) or path.stat().st_size > 20 * 1024 * 1024:
                continue
            with Image.open(path) as source:
                if (
                    source.format not in {"JPEG", "PNG", "WEBP"}
                    or source.width * source.height > 25000000
                ):
                    continue
                source.thumbnail((640, 640))
                output = BytesIO()
                source.convert("RGB").save(output, format="JPEG", quality=85)
            response = HttpResponse(output.getvalue(), content_type="image/jpeg")
            response["Cache-Control"] = "private, no-store"
            response["X-Content-Type-Options"] = "nosniff"
            return response
        except (
            OSError,
            ValueError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
        ):
            continue
    raise Http404
