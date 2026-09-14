import mimetypes
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.staticfiles import finders
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, CharField, Count, Exists, F, IntegerField, OuterRef, Prefetch, Q, Subquery, Value, When
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from catalogue.models import DuplicateCandidate, ExternalIdentifier, Recording, RecordingContribution, Release, ReleaseTrack
from flac_ingest.models import FlacIngestItem
from flac_ingest.services import apply_batch, preview_radio_file_split, resolve_music_path, scan_directory, split_radio_file_to_new_recording
from managed_music.models import ManagedRecording
from media_assets.models import FileAsset, FileLocation
from music_library.models import Channel, MusicLibraryChannel, MusicLibraryEntry, MusicLibraryTargetAudience, TargetAudience
from provenance.models import MetadataAssertion
from rights.forms import ReleaseRightsClaimForm
from rights.models import RightsClaim
from rights.services import create_release_rights_claims, get_local_organization
from rights.summaries import OwnershipCategory, local_confirmed_right_recording_ids, ownership_summaries_for_recordings

from .forms import MusicLibraryFilterForm, ReleaseMetadataForm, TrackRowFormSet
from .presentation import compact_names, radio_language_name
from .services import save_release_track_rows


def _artist_text(recording):
    names = [
        item.display_credit
        for item in recording.contributions.all()
        if item.role in {RecordingContribution.Role.PRIMARY, RecordingContribution.Role.FEATURED}
    ]
    return ", ".join(dict.fromkeys(names)) or "Uavklart artist"


def _duration(value):
    if value is None:
        return ""
    seconds = round(value / 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _query_without(request, *names):
    query = request.GET.copy()
    for name in names:
        query.pop(name, None)
    return query.urlencode()


def _safe_return(request, default):
    value = request.POST.get("return") or request.GET.get("return") or ""
    if value.startswith("/") and not value.startswith("//") and url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return value
    return default


def _entry_url(request, entry_id):
    query = request.GET.copy()
    query["selected"] = str(entry_id)
    return f"{reverse('gui_v2:music_library')}?{query.urlencode()}"


def _selected_entry_return(value, entry_id):
    parts = urlsplit(value)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["selected"] = str(entry_id)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


@require_GET
@login_required
def home(request):
    return render(request, "gui_v2/home.html", {"section": "home", "writes_enabled": settings.GUI_V2_WRITES_ENABLED})


@require_GET
@login_required
@permission_required("music_library.view_musiclibraryentry", raise_exception=True)
def music_library(request):
    form = MusicLibraryFilterForm(request.GET or None)
    active_filters = []
    page_size = "40"
    artist_prefetch = Prefetch(
        "recording__contributions",
        queryset=RecordingContribution.objects.select_related("artist_identity", "party").order_by("display_order"),
    )
    radio_assets = FileAsset.objects.filter(recording_id=OuterRef("recording_id"), role=FileAsset.Role.RADIO_FLAC)
    artist_sort = (
        RecordingContribution.objects.filter(
            recording_id=OuterRef("recording_id"),
            role__in=(RecordingContribution.Role.PRIMARY, RecordingContribution.Role.FEATURED),
        )
        .annotate(sort_name=Case(
            When(~Q(credited_as=""), then=F("credited_as")),
            When(artist_identity__isnull=False, then=F("artist_identity__display_name")),
            When(party__isnull=False, then=F("party__name")),
            default=Value(""), output_field=CharField(),
        ))
        .order_by("display_order", "id")
        .values("sort_name")[:1]
    )
    isrc_sort = ExternalIdentifier.objects.filter(
        recording_id=OuterRef("recording_id"), scheme=ExternalIdentifier.Scheme.ISRC
    ).values("normalized_value")[:1]
    channel_sort = (
        MusicLibraryChannel.objects.filter(library_entry_id=OuterRef("pk"))
        .order_by("channel__name")
        .values("channel__name")[:1]
    )
    target_sort = (
        MusicLibraryTargetAudience.objects.filter(library_entry_id=OuterRef("pk"))
        .order_by("target_audience__name")
        .values("target_audience__name")[:1]
    )
    queryset = (
        MusicLibraryEntry.objects.select_related("recording")
        .prefetch_related(
            artist_prefetch, "channels", "target_audiences", "recording__identifiers",
            "recording__file_assets__locations", "recording__release_tracks__release",
        )
        .annotate(
            sort_artist=Subquery(artist_sort),
            sort_isrc=Subquery(isrc_sort),
            sort_channel=Subquery(channel_sort),
            sort_target=Subquery(target_sort),
            sort_managed=Exists(ManagedRecording.objects.filter(library_entry_id=OuterRef("pk"))),
            has_radio_file=Exists(radio_assets),
            has_active_radio_location=Exists(FileLocation.objects.filter(
                asset__recording_id=OuterRef("recording_id"), asset__role=FileAsset.Role.RADIO_FLAC,
                is_current=True, status=FileLocation.Status.ACTIVE,
            )),
            has_file_problem=Exists(radio_assets.filter(sync_status__in=(
                FileAsset.SyncStatus.MISSING, FileAsset.SyncStatus.CONFLICT, FileAsset.SyncStatus.FAILED,
            ))),
            has_release=Exists(ReleaseTrack.objects.filter(recording_id=OuterRef("recording_id"))),
            has_duplicate_a=Exists(DuplicateCandidate.objects.filter(recording_a_id=OuterRef("recording_id"), status=DuplicateCandidate.Status.OPEN)),
            has_duplicate_b=Exists(DuplicateCandidate.objects.filter(recording_b_id=OuterRef("recording_id"), status=DuplicateCandidate.Status.OPEN)),
            has_ingest_issue=Exists(FlacIngestItem.objects.filter(recording_id=OuterRef("recording_id"), applied_at__isnull=True, action__in=(
                FlacIngestItem.Action.CONFLICT, FlacIngestItem.Action.RETRY, FlacIngestItem.Action.INVALID,
            ))),
        )
        .annotate(
            sort_file_status=Case(
                When(has_file_problem=True, then=Value(3)),
                When(has_active_radio_location=True, then=Value(2)),
                When(has_radio_file=True, then=Value(1)),
                default=Value(0), output_field=IntegerField(),
            ),
            sort_follow_up=Case(
                When(
                    Q(has_radio_file=False) | Q(has_active_radio_location=False) | Q(has_file_problem=True)
                    | Q(has_release=False) | Q(has_duplicate_a=True) | Q(has_duplicate_b=True)
                    | Q(has_ingest_issue=True),
                    then=Value(1),
                ),
                default=Value(0), output_field=IntegerField(),
            ),
        )
    )
    if form.is_valid():
        data = form.cleaned_data
        page_size = data.get("per_page") or "40"
        term = (data.get("q") or "").strip()
        if term:
            queryset = queryset.filter(
                Q(recording__title__icontains=term)
                | Q(recording__contributions__credited_as__icontains=term)
                | Q(recording__identifiers__normalized_value__icontains=term)
            )
        selected_genres = [value for value in request.GET.getlist("genre") if value]
        if selected_genres:
            genre_query = Q(pk__in=[])
            for selected_genre in selected_genres:
                genre_query |= (
                    Q(genre__iexact=selected_genre)
                    | Q(genre__istartswith=f"{selected_genre};")
                    | Q(genre__iendswith=f"; {selected_genre}")
                    | Q(genre__iendswith=f";{selected_genre}")
                    | Q(genre__icontains=f"; {selected_genre};")
                    | Q(genre__icontains=f";{selected_genre};")
                )
            queryset = queryset.filter(genre_query)
        for field in ("language", "energy"):
            values = [value for value in request.GET.getlist(field) if value]
            if values:
                queryset = queryset.filter(**{f"{field}__in": values})
        if data.get("gender") not in (None, ""):
            queryset = queryset.filter(gender=data["gender"])
        rotations = [value for value in request.GET.getlist("rotation_suitability") if value]
        if rotations:
            rotation_query = Q(pk__in=[])
            for rotation in rotations:
                if rotation == MusicLibraryEntry.RotationSuitability.SUITABLE:
                    rotation_query |= (
                        Q(rotation_suitability=MusicLibraryEntry.RotationSuitability.SUITABLE)
                        | Q(channels__isnull=False)
                    ) & ~Q(rotation_suitability__in=(
                        MusicLibraryEntry.RotationSuitability.NOT_SUITABLE,
                        MusicLibraryEntry.RotationSuitability.UNASSESSED,
                    ))
                elif rotation == MusicLibraryEntry.RotationSuitability.NOT_SUITABLE:
                    rotation_query |= Q(rotation_suitability=rotation)
                elif rotation == MusicLibraryEntry.RotationSuitability.UNASSESSED:
                    rotation_query |= (
                        Q(rotation_suitability=rotation)
                        | Q(rotation_suitability="", channels__isnull=True)
                    )
            queryset = queryset.filter(rotation_query)
        managed_values = set(request.GET.getlist("managed")) & {"yes", "no"}
        if managed_values == {"yes"}:
            queryset = queryset.filter(managed_recording__isnull=False)
        elif managed_values == {"no"}:
            queryset = queryset.filter(managed_recording__isnull=True)
        for relation, mode_name, chosen in (
            ("channels", "channel_mode", data.get("channels") or []),
            ("target_audiences", "target_mode", data.get("target_audiences") or []),
        ):
            if chosen and data.get(mode_name) == "all":
                for value in chosen:
                    queryset = queryset.filter(**{relation: value})
            elif chosen:
                queryset = queryset.filter(**{f"{relation}__in": chosen})
        file_statuses = set(request.GET.getlist("file_status")) & {"available", "missing", "problem", "none"}
        radio = Q(recording__file_assets__role=FileAsset.Role.RADIO_FLAC)
        if file_statuses:
            file_query = Q(pk__in=[])
            if "available" in file_statuses:
                file_query |= radio & Q(recording__file_assets__locations__status="active")
            if "missing" in file_statuses:
                file_query |= radio & Q(recording__file_assets__locations__status="missing")
            if "problem" in file_statuses:
                file_query |= radio & Q(recording__file_assets__sync_status__in=["conflict", "failed", "missing"])
            if "none" in file_statuses:
                file_query |= Q(has_radio_file=False)
            queryset = queryset.filter(file_query)
        needs_follow_up = (
            Q(has_radio_file=False) | Q(has_active_radio_location=False) | Q(has_file_problem=True)
            | Q(has_release=False) | Q(has_duplicate_a=True) | Q(has_duplicate_b=True) | Q(has_ingest_issue=True)
        )
        follow_up_values = set(request.GET.getlist("follow_up")) & {"yes", "no"}
        if follow_up_values == {"yes"}:
            queryset = queryset.filter(needs_follow_up)
        elif follow_up_values == {"no"}:
            queryset = queryset.exclude(needs_follow_up)
        current_ordering = data.get("ordering") or "title"
        ordering = {
            "title": ("recording__title", "id"), "-title": ("-recording__title", "id"),
            "artist": ("sort_artist", "recording__title", "id"), "-artist": ("-sort_artist", "recording__title", "id"),
            "isrc": ("sort_isrc", "recording__title", "id"), "-isrc": ("-sort_isrc", "recording__title", "id"),
            "duration": ("recording__duration_ms", "recording__title", "id"), "-duration": ("-recording__duration_ms", "recording__title", "id"),
            "genre": ("genre", "recording__title", "id"), "-genre": ("-genre", "recording__title", "id"),
            "language": ("language", "recording__title", "id"), "-language": ("-language", "recording__title", "id"),
            "energy": ("energy", "recording__title", "id"), "-energy": ("-energy", "recording__title", "id"),
            "rotation": ("rotation_suitability", "recording__title", "id"), "-rotation": ("-rotation_suitability", "recording__title", "id"),
            "channels": ("sort_channel", "recording__title", "id"), "-channels": ("-sort_channel", "recording__title", "id"),
            "targets": ("sort_target", "recording__title", "id"), "-targets": ("-sort_target", "recording__title", "id"),
            "file_status": ("sort_file_status", "recording__title", "id"), "-file_status": ("-sort_file_status", "recording__title", "id"),
            "managed": ("sort_managed", "recording__title", "id"), "-managed": ("-sort_managed", "recording__title", "id"),
            "follow_up": ("sort_follow_up", "recording__title", "id"), "-follow_up": ("-sort_follow_up", "recording__title", "id"),
            "-updated": ("-updated_at", "id"), "updated": ("updated_at", "id"),
        }.get(current_ordering, ("recording__title", "id"))
        queryset = queryset.order_by(*ordering)
        simple_labels = {"q": "Søk", "gender": "Vokal"}
        for name, label in simple_labels.items():
            value = data.get(name)
            if value not in (None, ""):
                display = dict(form.fields[name].choices).get(value, value) if hasattr(form.fields[name], "choices") else value
                active_filters.append({"label": f"{label}: {display}", "query": _query_without(request, name, "page")})
        multi_labels = {
            "genre": "Sjanger", "language": "Språk", "energy": "Energy",
            "rotation_suitability": "Rotasjon", "file_status": "Filstatus",
            "managed": "Forvaltning", "follow_up": "Oppfølging",
        }
        for name, label in multi_labels.items():
            values = [value for value in request.GET.getlist(name) if value]
            if values:
                choices = {str(key): str(value) for key, value in form.fields[name].choices}
                display = ", ".join(choices.get(str(value), str(value)) for value in values)
                active_filters.append({
                    "label": f"{label}: {display}",
                    "query": _query_without(request, name, "page", "column_filter"),
                })
        for name, mode, label in (("channels", "channel_mode", "Kanal"), ("target_audiences", "target_mode", "Målgruppe")):
            values = data.get(name) or []
            if values:
                qualifier = "alle" if data.get(mode) == "all" else "minst én"
                active_filters.append({"label": f"{label} ({qualifier}): {', '.join(str(value) for value in values)}", "query": _query_without(request, name, mode, "page", "column_filter")})
    else:
        current_ordering = "title"
        queryset = queryset.order_by("recording__title", "id")
    queryset = queryset.distinct()
    paginator_size = max(queryset.count(), 1) if page_size == "all" else int(page_size)
    page = Paginator(queryset, paginator_size).get_page(request.GET.get("page"))
    for entry in page.object_list:
        entry.artist_text = _artist_text(entry.recording)
        entry.isrc = next((item.normalized_value for item in entry.recording.identifiers.all() if item.scheme == ExternalIdentifier.Scheme.ISRC), "")
        entry.duration_text = _duration(entry.recording.duration_ms)
        entry.radio_files = [item for item in entry.recording.file_assets.all() if item.role == FileAsset.Role.RADIO_FLAC]
        entry.is_managed = hasattr(entry, "managed_recording")
        entry.language_display = radio_language_name(entry.language)
        channel_values = list(entry.channels.all())
        target_values = list(entry.target_audiences.all())
        entry.channel_summary = compact_names(channel_values)
        entry.channel_names = ", ".join(str(value) for value in channel_values)
        entry.target_summary = compact_names(target_values)
        entry.target_names = ", ".join(str(value) for value in target_values)
        if entry.rotation_suitability == MusicLibraryEntry.RotationSuitability.NOT_SUITABLE:
            entry.rotation_display = MusicLibraryEntry.RotationSuitability.NOT_SUITABLE.label
            entry.rotation_kind = "warning"
        elif entry.rotation_suitability == MusicLibraryEntry.RotationSuitability.UNASSESSED:
            entry.rotation_display = MusicLibraryEntry.RotationSuitability.UNASSESSED.label
            entry.rotation_kind = "muted"
        elif entry.rotation_suitability == MusicLibraryEntry.RotationSuitability.SUITABLE or channel_values:
            entry.rotation_display = MusicLibraryEntry.RotationSuitability.SUITABLE.label
            entry.rotation_kind = "ok"
        else:
            entry.rotation_display = "Ikke vurdert"
            entry.rotation_kind = "muted"
        entry.preview_url = _entry_url(request, entry.pk)
        entry.detail_url = f"{reverse('workbench:recording', args=[entry.recording_id])}?{urlencode({'return': entry.preview_url})}"
        entry.follow_up_reasons = []
        if not entry.has_radio_file:
            entry.follow_up_reasons.append("Ingen radio-FLAC")
        elif not entry.has_active_radio_location:
            entry.follow_up_reasons.append("Filen er ikke tilgjengelig")
        if entry.has_file_problem:
            entry.follow_up_reasons.append("Fil- eller synkroniseringsproblem")
        if not entry.has_release:
            entry.follow_up_reasons.append("Ingen utgivelseskobling")
        if entry.has_duplicate_a or entry.has_duplicate_b:
            entry.follow_up_reasons.append("Mulig dublett")
        if entry.has_ingest_issue:
            entry.follow_up_reasons.append("Uløst innlesingsavvik")
        entry.last_read_at = max((item.metadata_read_at for item in entry.radio_files if item.metadata_read_at), default=None)
        if not entry.radio_files:
            entry.file_status_text = "Ingen radiofil"
            entry.file_status_kind = "muted"
        elif any(location.status == "active" for item in entry.radio_files for location in item.locations.all()):
            entry.file_status_text = "Tilgjengelig"
            entry.file_status_kind = "ok"
        elif any(item.sync_status in {"failed", "conflict", "missing"} for item in entry.radio_files):
            entry.file_status_text = entry.radio_files[0].get_sync_status_display()
            entry.file_status_kind = "error"
        else:
            entry.file_status_text = "Ikke kontrollert"
            entry.file_status_kind = "muted"

    selected_id = request.GET.get("selected")
    selected = next((item for item in page.object_list if str(item.pk) == selected_id), None)
    if selected is None and page.object_list:
        selected = page.object_list[0]
    if selected:
        selected.releases = list({track.release_id: track.release for track in selected.recording.release_tracks.all()}.values())
        selected.source_assertions = MetadataAssertion.objects.filter(
            entity_type=MetadataAssertion.EntityType.MUSIC_LIBRARY_ENTRY, entity_uuid=selected.pk
        ).select_related("source_record__source_system")[:10]
        for asset in selected.radio_files:
            asset.current_locations = [location for location in asset.locations.all() if location.is_current]
            asset.can_split = (
                len(selected.radio_files) > 1
                and not selected.is_managed
                and request.user.is_staff
                and request.user.has_perms((
                    "catalogue.add_recording",
                    "catalogue.change_releasetrack",
                    "media_assets.change_fileasset",
                    "music_library.add_musiclibraryentry",
                    "flac_ingest.apply_flacingestbatch",
                ))
            )
            for location in asset.current_locations:
                try:
                    if location.storage_type == FileLocation.StorageType.NAS:
                        _, file_path = resolve_music_path(location.relative_path)
                        location.onetagger_path = str(file_path.parent)
                    else:
                        location.onetagger_path = ""
                except (ImproperlyConfigured, ValidationError, OSError):
                    location.onetagger_path = ""
    selected_channel_ids = set(request.GET.getlist("channels"))
    channel_filter_options = list(
        Channel.objects.filter(is_active=True)
        .annotate(usage_count=Count("library_links"))
        .order_by("-usage_count", "name")
    )
    for channel in channel_filter_options:
        channel.is_filter_selected = str(channel.pk) in selected_channel_ids
        for extension in ("png", "svg"):
            candidate = f"gui_v2/channel_logos/{channel.code}.{extension}"
            if finders.find(candidate):
                channel.built_in_logo_path = candidate
                break
        else:
            channel.built_in_logo_path = ""
    selected_target_ids = set(request.GET.getlist("target_audiences"))
    target_filter_options = list(TargetAudience.objects.filter(is_active=True).order_by("name"))
    for target in target_filter_options:
        target.is_filter_selected = str(target.pk) in selected_target_ids
    return render(
        request,
        "gui_v2/music_library.html",
        {
            "section": "music_library", "filter_form": form, "page": page, "selected": selected,
            "query_without_page": _query_without(request, "page"),
            "sort_query": _query_without(request, "ordering", "page", "selected"),
            "current_ordering": current_ordering,
            "query_without_selected": _query_without(request, "selected"),
            "page_size": page_size,
            "page_size_params": [
                (key, value)
                for key, values in request.GET.lists()
                if key not in {"page", "per_page", "selected"}
                for value in values
            ],
            "search_params": [
                (key, value)
                for key, values in request.GET.lists()
                if key not in {"q", "page", "selected"}
                for value in values
            ],
            "active_filters": active_filters,
            "channel_filter_options": channel_filter_options,
            "target_filter_options": target_filter_options,
            "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
        },
    )


@require_GET
@login_required
@permission_required("music_library.view_musiclibraryentry", raise_exception=True)
def channel_logo(request, channel_id):
    channel = get_object_or_404(Channel, pk=channel_id)
    if not channel.logo:
        raise Http404("Kanalen har ingen egendefinert logo.")
    content_type = mimetypes.guess_type(channel.logo.name)[0] or "application/octet-stream"
    response = FileResponse(
        channel.logo.open("rb"),
        content_type=content_type,
        filename=PurePosixPath(channel.logo.name).name,
    )
    response["Cache-Control"] = "private, max-age=300"
    return response


@require_POST
@login_required
@permission_required(
    ("music_library.view_musiclibraryentry", "flac_ingest.add_flacingestbatch", "flac_ingest.apply_flacingestbatch"),
    raise_exception=True,
)
def rescan_library_file(request, entry_id, asset_id):
    if not settings.GUI_V2_WRITES_ENABLED:
        raise PermissionDenied("Ny innlesing er deaktivert i dette prototypeoppsettet.")
    entry = get_object_or_404(MusicLibraryEntry.objects.select_related("recording"), pk=entry_id)
    asset = get_object_or_404(FileAsset, pk=asset_id, recording=entry.recording, role=FileAsset.Role.RADIO_FLAC)
    location = get_object_or_404(asset.locations, is_current=True, storage_type=FileLocation.StorageType.NAS)
    return_url = _safe_return(request, _entry_url(request, entry.pk))
    before_radio = {
        "radiosjanger": entry.genre, "radiospråk": entry.language, "Energy": entry.energy,
        "vokalklassifisering": entry.gender,
        "rotasjonsvurdering": entry.rotation_suitability,
        "kanaler": tuple(entry.channels.values_list("name", flat=True).order_by("name")),
        "målgrupper": tuple(entry.target_audiences.values_list("name", flat=True).order_by("name")),
    }
    before_catalogue = (entry.recording.title, entry.recording.duration_ms)
    try:
        # This explicit re-read follows an already registered FileAsset. P7UUID may
        # therefore recover that known link; the ingest service still rejects UUID/
        # ISRC conflicts instead of silently attaching another Recording.
        batch = scan_directory(
            relative_root=".", recursive=False, relative_paths=[location.relative_path],
            allow_uuid_recovery=True, force_read=True, user=request.user,
        )
        item = batch.items.first()
        if not item:
            messages.error(request, "Filen kunne ikke leses – prøv igjen.")
        elif item.can_apply:
            apply_batch(batch, user=request.user)
            entry.refresh_from_db()
            entry.recording.refresh_from_db()
            after_radio = {
                "radiosjanger": entry.genre, "radiospråk": entry.language, "Energy": entry.energy,
                "vokalklassifisering": entry.gender,
                "rotasjonsvurdering": entry.rotation_suitability,
                "kanaler": tuple(entry.channels.values_list("name", flat=True).order_by("name")),
                "målgrupper": tuple(entry.target_audiences.values_list("name", flat=True).order_by("name")),
            }
            changed = [label for label, value in after_radio.items() if value != before_radio[label]]
            catalogue_changed = before_catalogue != (entry.recording.title, entry.recording.duration_ms)
            if changed:
                text = f"Radiometadata oppdatert: {', '.join(changed)}."
                if catalogue_changed:
                    text += " Katalogmetadata ble også oppdatert fra filen."
                messages.success(request, text)
            elif catalogue_changed:
                messages.success(request, "Katalogmetadata ble oppdatert fra filen. Ingen radiometadata ble endret.")
            else:
                messages.info(request, "Filmetadata lest inn på nytt. Ingen katalogverdier ble endret.")
        else:
            explanation = "; ".join(str(value) for value in (item.messages or []))
            messages.error(request, explanation or "Filen krever kontroll og ble ikke brukt.")
    except (OSError, ValidationError) as error:
        messages.error(request, f"Filen kunne ikke leses – {error}")
    return redirect(return_url)


@require_http_methods(["GET", "POST"])
@login_required
@permission_required(
    (
        "music_library.view_musiclibraryentry",
        "catalogue.add_recording",
        "catalogue.change_releasetrack",
        "media_assets.change_fileasset",
        "music_library.add_musiclibraryentry",
        "flac_ingest.apply_flacingestbatch",
    ),
    raise_exception=True,
)
def split_library_file(request, entry_id, asset_id):
    if not settings.GUI_V2_WRITES_ENABLED:
        raise PermissionDenied("Utskilling er deaktivert utenfor den isolerte testdatabasen.")
    entry = get_object_or_404(MusicLibraryEntry.objects.select_related("recording"), pk=entry_id)
    asset = get_object_or_404(
        FileAsset, pk=asset_id, recording=entry.recording, role=FileAsset.Role.RADIO_FLAC
    )
    return_url = _safe_return(request, _entry_url(request, entry.pk))
    try:
        preview = preview_radio_file_split(asset_id=asset.pk)
    except (OSError, ValidationError) as error:
        messages.error(request, str(error))
        return redirect(return_url)
    if request.method == "POST":
        if request.POST.get("confirmed") != "yes":
            messages.error(request, "Bekreft utskillingen før den utføres.")
        elif preview["blocked_reason"]:
            messages.error(request, preview["blocked_reason"])
        else:
            try:
                recording, old_recording, remaining_assets = split_radio_file_to_new_recording(
                    asset_id=asset.pk, user=request.user
                )
            except (OSError, ValidationError) as error:
                messages.error(request, str(error))
            else:
                # Restore the former Recording from its one remaining authoritative
                # FLAC. Several remaining files require explicit human review.
                refreshed = False
                if len(remaining_assets) == 1:
                    remaining_location = remaining_assets[0].locations.filter(
                        is_current=True,
                        storage_type=FileLocation.StorageType.NAS,
                        status=FileLocation.Status.ACTIVE,
                    ).first()
                    if remaining_location:
                        batch = scan_directory(
                            relative_root=".", recursive=False,
                            relative_paths=[remaining_location.relative_path],
                            force_read=True, user=request.user,
                        )
                        refreshed = apply_batch(batch, user=request.user) == 1
                new_entry = recording.music_library_entry
                message = (
                    f"«{asset.filename}» er skilt ut som innspillingen "
                    f"«{recording.title}». Filen ble ikke endret."
                )
                if refreshed:
                    message += f" «{old_recording.title}» ble lest på nytt fra gjenværende fil."
                elif len(remaining_assets) > 1:
                    message += " Flere filer gjenstår på den opprinnelige innspillingen og må kontrolleres."
                messages.success(request, message)
                return redirect(_selected_entry_return(return_url, new_entry.pk))
    return render(request, "gui_v2/split_library_file.html", {
        "section": "music_library", "entry": entry, "asset": asset,
        "preview": preview, "return_url": return_url,
        "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
    })


@require_http_methods(["GET", "POST"])
@login_required
@permission_required("catalogue.view_release", raise_exception=True)
def release_list(request):
    create_form = ReleaseMetadataForm(
        request.POST or None, prefix="release"
    )
    if request.method == "POST":
        if not settings.GUI_V2_WRITES_ENABLED:
            raise PermissionDenied("GUI v2 er skrivebeskyttet utenfor den isolerte testdatabasen.")
        if not request.user.has_perm("catalogue.add_release"):
            raise PermissionDenied
        if (request.POST.get("release-barcode") or "").strip() and not request.user.has_perm(
            "catalogue.add_externalidentifier"
        ):
            raise PermissionDenied
        if create_form.is_valid():
            with transaction.atomic():
                release = create_form.save()
                create_form.save_barcode()
            messages.success(request, "Utgivelsen er opprettet. Du kan nå registrere spor.")
            return redirect("gui_v2:release_detail", release_id=release.pk)
    releases = Release.objects.select_related("label").annotate(track_count=Count("tracks"))
    q = request.GET.get("q", "").strip()
    if q:
        releases = releases.filter(Q(title__icontains=q) | Q(catalogue_number__icontains=q) | Q(label__name__icontains=q))
    page = Paginator(releases.order_by("title", "id"), 40).get_page(request.GET.get("page"))
    return render(request, "gui_v2/release_list.html", {
        "section": "releases", "page": page, "q": q,
        "create_form": create_form,
        "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
    })


def _track_initial(track):
    contributions = list(track.recording.contributions.all())
    def credits(role):
        return "; ".join(item.display_credit for item in contributions if item.role == role)
    isrc = next((item.normalized_value for item in track.recording.identifiers.all() if item.scheme == ExternalIdentifier.Scheme.ISRC), "")
    return {
        "track_id": track.pk, "recording_id": track.recording_id,
        "sequence_number": track.sequence_number, "disc_number": track.disc_number,
        "side": track.side, "track_number": track.track_number,
        "title_override": track.title_override, "recording_title": track.recording.title,
        "artists": credits(RecordingContribution.Role.PRIMARY),
        "composers": credits(RecordingContribution.Role.COMPOSER),
        "lyricists": credits(RecordingContribution.Role.LYRICIST),
        "arrangers": credits(RecordingContribution.Role.ARRANGER),
        "duration": _duration(track.duration_ms or track.recording.duration_ms), "isrc": isrc,
    }


def _require_track_write_permissions(user, rows):
    required = {"catalogue.change_release", "catalogue.add_releasetrack", "catalogue.change_releasetrack"}
    if any(not row.get("track_id") for row in rows):
        required.add("catalogue.add_recording")
    if any(row.get("remove") for row in rows):
        required.add("catalogue.delete_releasetrack")
    if any(row.get("update_shared_recording") or not row.get("recording_id") for row in rows):
        required.update({"catalogue.add_recordingcontribution", "catalogue.add_externalidentifier"})
    if not user.has_perms(required):
        raise PermissionDenied


@require_http_methods(["GET", "POST"])
@login_required
@permission_required("catalogue.view_release", raise_exception=True)
def release_detail(request, release_id):
    release = get_object_or_404(Release.objects.select_related("label"), pk=release_id)
    action = request.POST.get("action", "tracks") if request.method == "POST" else ""
    active_tab = request.POST.get("tab") or request.GET.get("tab", "tracks")
    if active_tab not in {"tracks", "details", "files", "rights"}:
        active_tab = "tracks"
    return_url = _safe_return(request, reverse("gui_v2:release_list"))
    tracks = list(
        release.tracks.select_related("recording")
        .prefetch_related("recording__contributions__artist_identity", "recording__contributions__party", "recording__identifiers", "file_assets")
        .order_by("sequence_number")
    )
    initial = [_track_initial(track) for track in tracks]
    formset = TrackRowFormSet(
        request.POST if action == "tracks" else None,
        initial=None if action == "tracks" else initial,
        form_kwargs={"release": release}, prefix="tracks",
    )
    track_has_files = {str(track.pk): bool(track.file_assets.all()) for track in tracks}
    for row_form in formset.forms:
        row_form.has_files = track_has_files.get(str(row_form["track_id"].value() or ""), False)
    release_form = ReleaseMetadataForm(
        request.POST if action == "release" else None, instance=release, prefix="release"
    )
    can_manage_rights = request.user.is_staff and request.user.has_perms(
        ("rights.view_rightsclaim", "rights.add_rightsclaim")
    )
    rights_form = None
    if can_manage_rights:
        rights_form = ReleaseRightsClaimForm(
            request.POST if action == "rights" else None, release=release, prefix="rights"
        )
        if not request.user.has_perm("rights.view_agreement"):
            rights_form.fields["agreement"].queryset = rights_form.fields["agreement"].queryset.none()
        if not request.user.has_perm("provenance.view_sourcerecord"):
            rights_form.fields["source_record"].queryset = rights_form.fields["source_record"].queryset.none()
    if request.method == "POST" and action == "release":
        if not settings.GUI_V2_WRITES_ENABLED:
            raise PermissionDenied("GUI v2 er skrivebeskyttet utenfor den isolerte testdatabasen.")
        if not request.user.has_perm("catalogue.change_release"):
            raise PermissionDenied
        if release_form.is_valid():
            with transaction.atomic():
                release_form.save()
                release_form.save_barcode()
            messages.success(request, "Utgivelsesopplysningene er lagret.")
            return redirect(f"{reverse('gui_v2:release_detail', args=[release.pk])}?tab=details")
    elif request.method == "POST" and action == "rights":
        if not settings.GUI_V2_WRITES_ENABLED:
            raise PermissionDenied("GUI v2 er skrivebeskyttet utenfor den isolerte testdatabasen.")
        if not can_manage_rights:
            raise PermissionDenied
        if rights_form.is_valid():
            data = rights_form.cleaned_data.copy()
            recordings = data.pop("recordings")
            territories = data.pop("territories")
            try:
                claims = create_release_rights_claims(
                    release=release,
                    recordings=recordings,
                    territories=territories,
                    allow_managed_registration=request.user.is_superuser,
                    **data,
                )
            except ValidationError as error:
                rights_form.add_error(None, error)
            else:
                messages.success(request, f"{len(claims)} rettighetskrav ble registrert som ikke verifisert.")
                return redirect(f"{reverse('gui_v2:release_detail', args=[release.pk])}?tab=rights")
    elif request.method == "POST" and action == "tracks":
        if not settings.GUI_V2_WRITES_ENABLED:
            raise PermissionDenied("GUI v2 er skrivebeskyttet utenfor den isolerte testdatabasen.")
        if formset.is_valid():
            rows = [form.cleaned_data for form in formset.forms]
            _require_track_write_permissions(request.user, rows)
            try:
                save_release_track_rows(release=release, rows=rows)
            except (ValidationError, ValueError) as error:
                values = error.messages if hasattr(error, "messages") else [str(error)]
                formset._non_form_errors = formset.error_class(values)
            else:
                messages.success(request, "Sporlisten er lagret samlet.")
                query = request.POST.get("return_query", "")
                target = reverse("gui_v2:release_detail", args=[release.pk])
                query = f"{query}&grid_saved=1" if query else "grid_saved=1"
                return redirect(f"{target}?{query}")
    selected_track_id = request.GET.get("track")
    selected_track = next((item for item in tracks if str(item.pk) == selected_track_id), tracks[0] if tracks else None)
    selected_track_data = _track_initial(selected_track) if selected_track else None
    release_barcode = release.identifiers.filter(
        scheme__in=(ExternalIdentifier.Scheme.UPC, ExternalIdentifier.Scheme.EAN, ExternalIdentifier.Scheme.GTIN)
    ).first()
    release_artists = []
    for track_data in initial:
        artists = track_data["artists"]
        if artists:
            release_artists.extend(part.strip() for part in artists.split(";") if part.strip())
    release_artist_text = ", ".join(dict.fromkeys(release_artists)) or "Uavklart artist"
    release_cover = None
    if request.user.is_staff and request.user.has_perms(
        ("media_assets.view_fileasset", "media_assets.view_filelocation", "music_library.view_musiclibraryentry")
    ):
        release_cover = (
            release.file_assets.filter(
                role=FileAsset.Role.COVER_IMAGE,
                locations__storage_type=FileLocation.StorageType.NAS,
                locations__is_current=True,
                locations__status=FileLocation.Status.ACTIVE,
            )
            .distinct()
            .first()
        )
    release_files = list(
        release.file_assets.prefetch_related("locations").order_by("role", "filename")
    ) if request.user.has_perm("media_assets.view_fileasset") else []
    release_sources = list(
        MetadataAssertion.objects.filter(
            entity_type=MetadataAssertion.EntityType.RELEASE, entity_uuid=release.pk
        ).select_related("source_record__source_system")
    ) if request.user.has_perm("provenance.view_metadataassertion") else []
    recording_ids = tuple(dict.fromkeys(track.recording_id for track in tracks))
    rights_claims = []
    rights_summary = []
    if request.user.has_perm("rights.view_rightsclaim"):
        rights_claims = list(
            RightsClaim.objects.filter(recording_id__in=recording_ids)
            .select_related("recording", "rights_holder", "grantor", "agreement", "source_record__source_system")
            .prefetch_related("territories")
            .order_by("recording__title", "right_type", "status")
        )
        local_organization = get_local_organization()
        summaries = ownership_summaries_for_recordings(recording_ids, local_organization)
        rights_summary = [
            (OwnershipCategory.FULL.label, sum(item.category == OwnershipCategory.FULL for item in summaries.values())),
            (OwnershipCategory.PARTIAL.label, sum(item.category == OwnershipCategory.PARTIAL for item in summaries.values())),
            (OwnershipCategory.NOT_OWNED.label, sum(item.category == OwnershipCategory.NOT_OWNED for item in summaries.values())),
            (OwnershipCategory.UNRESOLVED.label, sum(item.category == OwnershipCategory.UNRESOLVED for item in summaries.values())),
            (OwnershipCategory.DISPUTED.label, sum(item.category == OwnershipCategory.DISPUTED for item in summaries.values())),
            ("Administrert av lokal organisasjon", len(local_confirmed_right_recording_ids(recording_ids, local_organization, RightsClaim.RightType.ADMINISTRATION))),
            ("Distribuert av lokal organisasjon", len(local_confirmed_right_recording_ids(recording_ids, local_organization, RightsClaim.RightType.DISTRIBUTION))),
        ]
    return render(
        request,
        "gui_v2/release_tracks.html",
        {
            "section": "releases", "release": release, "tracks": tracks, "formset": formset,
            "selected_track": selected_track, "selected_track_data": selected_track_data,
            "release_form": release_form, "release_barcode": release_barcode,
            "release_artist_text": release_artist_text,
            "release_cover": release_cover,
            "active_tab": active_tab,
            "release_files": release_files, "release_sources": release_sources,
            "rights_claims": rights_claims, "rights_summary": rights_summary,
            "rights_form": rights_form, "can_manage_rights": can_manage_rights,
            "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
            "return_query": request.GET.urlencode(),
            "return_url": return_url,
        },
    )


@require_GET
@login_required
@permission_required("catalogue.view_recording", raise_exception=True)
def recording_search(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})
    queryset = Recording.objects.filter(
        Q(title__icontains=q)
        | Q(identifiers__normalized_value__icontains=q)
        | Q(contributions__credited_as__icontains=q)
    ).prefetch_related("contributions", "identifiers").distinct()[:12]
    return JsonResponse({"results": [{
        "id": str(item.pk), "title": item.title, "artist": _artist_text(item),
        "isrc": next((identifier.normalized_value for identifier in item.identifiers.all() if identifier.scheme == ExternalIdentifier.Scheme.ISRC), ""),
    } for item in queryset]})


@require_GET
@login_required
@permission_required("catalogue.view_release", raise_exception=True)
def legacy_release_tracks(request):
    release = Release.objects.order_by("title").first()
    return redirect("gui_v2:release_detail", release_id=release.pk) if release else release_list(request)
