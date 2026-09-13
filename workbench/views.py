from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch, Q
from django.http import HttpResponseForbidden, JsonResponse
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
from managed_music.forms import ManagedRecordingCreationForm
from managed_music.models import ManagedRecording
from managed_music.services import create_managed_recording
from media_assets.models import FileAsset
from music_library.models import MusicLibraryEntry
from parties.models import ArtistIdentity, Party
from provenance.models import AppliedMetadataChange, MetadataAssertion, SourceSystem
from provenance.services import (
    apply_assertion,
    correct_assertion,
    decide_assertion,
)
from rights_core.models import VerificationStatus

from .forms import (
    ArtistIdentityForm,
    AssertionActionForm,
    ContributionForm,
    FileAssetForm,
    FileLocationForm,
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
        "help": "Brukerhjelp",
    }
    return {
        "section": section,
        "section_label": labels.get(section, "Katalogarbeid"),
        "title": title,
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
    return render(
        request,
        "workbench/home.html",
        _page_context("home", "Start", tasks=tasks),
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
            "recording__identifiers",
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
    return render(
        request,
        "workbench/library_list.html",
        _page_context(
            "library",
            "Musikkarkiv",
            form=form,
            page=page,
            page_query=query,
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
    queryset = (
        ManagedRecording.objects.select_related(
            "library_entry__recording", "source_system"
        )
        .prefetch_related(
            "library_entry__recording__identifiers",
            "library_entry__recording__contributions__party",
            "library_entry__recording__contributions__artist_identity",
        )
        .order_by("library_entry__recording__title", "id")
    )
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
    page, query = _paginate(request, queryset)
    return render(
        request,
        "workbench/managed_list.html",
        _page_context(
            "managed",
            "Forvaltet musikk",
            form=form,
            page=page,
            page_query=query,
            sources=SourceSystem.objects.all(),
        ),
    )


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
    form = None
    if request.method == "POST" and not request.user.has_perm(
        "catalogue.change_release"
    ):
        return HttpResponseForbidden("Du kan ikke endre utgivelsen.")
    if request.user.has_perm("catalogue.change_release"):
        form = ReleaseForm(request.POST or None, instance=release)
        if request.method == "POST" and form.is_valid():
            form.save()
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
                formset._non_form_errors = formset.error_class(
                    ["Sorteringsrekkefølge må være unik og kan ikke finnes fra før."],
                    renderer=formset.renderer,
                )
            else:
                try:
                    with transaction.atomic():
                        for form in changed_forms:
                            values = dict(form.cleaned_data)
                            values.pop("existing_recording_search", None)
                            create_release_track(release=release, **values)
                except (ValueError, IntegrityError) as error:
                    formset._non_form_errors = formset.error_class(
                        [str(error)], renderer=formset.renderer
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
    )


@staff
@permission_required("catalogue.view_recording", raise_exception=True)
def recording_detail(request, pk):
    recording = get_object_or_404(_recording_queryset(), pk=pk)
    tab = request.GET.get("fane", "overview")
    if tab not in {"overview", "radio", "releases", "contributors", "files", "sources"}:
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
            return_url=_safe_return(request, reverse("workbench:library")),
        ),
    )


@staff
@permission_required("catalogue.change_recording", raise_exception=True)
def recording_edit(request, pk):
    recording = get_object_or_404(Recording, pk=pk)
    form = RecordingForm(request.POST or None, instance=recording)
    if request.method == "POST" and form.is_valid():
        form.save()
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
        "workbench/form.html",
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
    return render(
        request,
        "workbench/control.html",
        _page_context(
            "control",
            "Kontroll",
            selected=selected,
            duplicates=duplicates[:100],
            assertions=assertions[:100],
        ),
    )


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
    return render(request, "workbench/help.html", _page_context("help", "Hjelp"))
