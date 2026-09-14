from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from catalogue.models import DuplicateCandidate, ExternalIdentifier, Recording, RecordingContribution, Release, ReleaseTrack
from flac_ingest.models import FlacIngestItem
from flac_ingest.services import apply_batch, resolve_music_path, scan_directory
from media_assets.models import FileAsset, FileLocation
from music_library.models import MusicLibraryEntry
from provenance.models import MetadataAssertion

from .forms import MusicLibraryFilterForm, TrackRowFormSet
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
    artist_prefetch = Prefetch(
        "recording__contributions",
        queryset=RecordingContribution.objects.select_related("artist_identity", "party").order_by("display_order"),
    )
    radio_assets = FileAsset.objects.filter(recording_id=OuterRef("recording_id"), role=FileAsset.Role.RADIO_FLAC)
    queryset = (
        MusicLibraryEntry.objects.select_related("recording")
        .prefetch_related(
            artist_prefetch, "channels", "target_audiences", "recording__identifiers",
            "recording__file_assets__locations", "recording__release_tracks__release",
        )
        .annotate(
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
    )
    if form.is_valid():
        data = form.cleaned_data
        term = (data.get("q") or "").strip()
        if term:
            queryset = queryset.filter(
                Q(recording__title__icontains=term)
                | Q(recording__contributions__credited_as__icontains=term)
                | Q(recording__identifiers__normalized_value__icontains=term)
            )
        for field in ("genre", "language", "energy", "gender"):
            if data.get(field) not in (None, ""):
                queryset = queryset.filter(**{field: data[field]})
        if data.get("managed") == "yes":
            queryset = queryset.filter(managed_recording__isnull=False)
        elif data.get("managed") == "no":
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
        file_status = data.get("file_status")
        radio = Q(recording__file_assets__role=FileAsset.Role.RADIO_FLAC)
        if file_status == "available":
            queryset = queryset.filter(radio, recording__file_assets__locations__status="active")
        elif file_status == "missing":
            queryset = queryset.filter(radio, recording__file_assets__locations__status="missing")
        elif file_status == "problem":
            queryset = queryset.filter(radio, recording__file_assets__sync_status__in=["conflict", "failed", "missing"])
        elif file_status == "none":
            queryset = queryset.exclude(radio)
        needs_follow_up = (
            Q(has_radio_file=False) | Q(has_active_radio_location=False) | Q(has_file_problem=True)
            | Q(has_release=False) | Q(has_duplicate_a=True) | Q(has_duplicate_b=True) | Q(has_ingest_issue=True)
        )
        if data.get("follow_up") == "yes":
            queryset = queryset.filter(needs_follow_up)
        elif data.get("follow_up") == "no":
            queryset = queryset.exclude(needs_follow_up)
        ordering = {
            "title": ("recording__title", "id"), "-title": ("-recording__title", "id"),
            "-updated": ("-updated_at", "id"), "updated": ("updated_at", "id"),
        }.get(data.get("ordering"), ("recording__title", "id"))
        queryset = queryset.order_by(*ordering)
        simple_labels = {
            "q": "Søk", "genre": "Sjanger", "language": "Språk", "energy": "Energy",
            "gender": "Vokal", "file_status": "Filstatus", "managed": "Forvaltning",
            "follow_up": "Oppfølging", "ordering": "Sortering",
        }
        for name, label in simple_labels.items():
            value = data.get(name)
            if value not in (None, ""):
                display = dict(form.fields[name].choices).get(value, value) if hasattr(form.fields[name], "choices") else value
                active_filters.append({"label": f"{label}: {display}", "query": _query_without(request, name, "page")})
        for name, mode, label in (("channels", "channel_mode", "Kanal"), ("target_audiences", "target_mode", "Målgruppe")):
            values = data.get(name) or []
            if values:
                qualifier = "alle" if data.get(mode) == "all" else "minst én"
                active_filters.append({"label": f"{label} ({qualifier}): {', '.join(str(value) for value in values)}", "query": _query_without(request, name, mode, "page")})
    queryset = queryset.distinct()
    page = Paginator(queryset, 40).get_page(request.GET.get("page"))
    for entry in page.object_list:
        entry.artist_text = _artist_text(entry.recording)
        entry.isrc = next((item.normalized_value for item in entry.recording.identifiers.all() if item.scheme == ExternalIdentifier.Scheme.ISRC), "")
        entry.duration_text = _duration(entry.recording.duration_ms)
        entry.radio_files = [item for item in entry.recording.file_assets.all() if item.role == FileAsset.Role.RADIO_FLAC]
        entry.is_managed = hasattr(entry, "managed_recording")
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
    if selected:
        selected.releases = list({track.release_id: track.release for track in selected.recording.release_tracks.all()}.values())
        selected.source_assertions = MetadataAssertion.objects.filter(
            entity_type=MetadataAssertion.EntityType.MUSIC_LIBRARY_ENTRY, entity_uuid=selected.pk
        ).select_related("source_record__source_system")[:10]
        for asset in selected.radio_files:
            asset.current_locations = [location for location in asset.locations.all() if location.is_current]
            for location in asset.current_locations:
                try:
                    location.onetagger_path = (
                        str(resolve_music_path(location.relative_path))
                        if location.storage_type == FileLocation.StorageType.NAS else ""
                    )
                except (ImproperlyConfigured, ValidationError, OSError):
                    location.onetagger_path = ""
    return render(
        request,
        "gui_v2/music_library.html",
        {
            "section": "music_library", "filter_form": form, "page": page, "selected": selected,
            "query_without_page": _query_without(request, "page"),
            "query_without_selected": _query_without(request, "selected"),
            "active_filters": active_filters,
            "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
        },
    )


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
            allow_uuid_recovery=True, user=request.user,
        )
        item = batch.items.first()
        if not item:
            messages.error(request, "Filen kunne ikke leses – prøv igjen.")
        elif item.action == FlacIngestItem.Action.UNCHANGED:
            messages.info(request, "Ingen endringer funnet.")
        elif item.can_apply:
            apply_batch(batch, user=request.user)
            entry.refresh_from_db()
            entry.recording.refresh_from_db()
            after_radio = {
                "radiosjanger": entry.genre, "radiospråk": entry.language, "Energy": entry.energy,
                "vokalklassifisering": entry.gender,
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


@require_GET
@login_required
@permission_required("catalogue.view_release", raise_exception=True)
def release_list(request):
    releases = Release.objects.select_related("label").annotate(track_count=Count("tracks"))
    q = request.GET.get("q", "").strip()
    if q:
        releases = releases.filter(Q(title__icontains=q) | Q(catalogue_number__icontains=q) | Q(label__name__icontains=q))
    page = Paginator(releases.order_by("title", "id"), 40).get_page(request.GET.get("page"))
    return render(request, "gui_v2/release_list.html", {"section": "releases", "page": page, "q": q})


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
    return_url = _safe_return(request, reverse("gui_v2:release_list"))
    tracks = list(
        release.tracks.select_related("recording")
        .prefetch_related("recording__contributions__artist_identity", "recording__contributions__party", "recording__identifiers", "file_assets")
        .order_by("sequence_number")
    )
    initial = [_track_initial(track) for track in tracks]
    formset = TrackRowFormSet(
        request.POST or None, initial=None if request.method == "POST" else initial,
        form_kwargs={"release": release}, prefix="tracks",
    )
    if request.method == "POST":
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
                return redirect(f"{target}?{query}" if query else target)
    selected_track_id = request.GET.get("track")
    selected_track = next((item for item in tracks if str(item.pk) == selected_track_id), tracks[0] if tracks else None)
    selected_track_data = _track_initial(selected_track) if selected_track else None
    return render(
        request,
        "gui_v2/release_tracks.html",
        {
            "section": "releases", "release": release, "tracks": tracks, "formset": formset,
            "selected_track": selected_track, "selected_track_data": selected_track_data,
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
