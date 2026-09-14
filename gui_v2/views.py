from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from catalogue.models import ExternalIdentifier, Recording, RecordingContribution, Release
from media_assets.models import FileAsset
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


@require_GET
@login_required
def home(request):
    return render(request, "gui_v2/home.html", {"section": "home", "writes_enabled": settings.GUI_V2_WRITES_ENABLED})


@require_GET
@login_required
@permission_required("music_library.view_musiclibraryentry", raise_exception=True)
def music_library(request):
    form = MusicLibraryFilterForm(request.GET or None)
    artist_prefetch = Prefetch(
        "recording__contributions",
        queryset=RecordingContribution.objects.select_related("artist_identity", "party").order_by("display_order"),
    )
    queryset = (
        MusicLibraryEntry.objects.select_related("recording")
        .prefetch_related(
            artist_prefetch, "channels", "target_audiences", "recording__identifiers",
            "recording__file_assets__locations", "recording__release_tracks__release",
        )
        .order_by("recording__title", "id")
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
    queryset = queryset.distinct()
    page = Paginator(queryset, 40).get_page(request.GET.get("page"))
    for entry in page.object_list:
        entry.artist_text = _artist_text(entry.recording)
        entry.isrc = next((item.normalized_value for item in entry.recording.identifiers.all() if item.scheme == ExternalIdentifier.Scheme.ISRC), "")
        entry.duration_text = _duration(entry.recording.duration_ms)
        entry.radio_files = [item for item in entry.recording.file_assets.all() if item.role == FileAsset.Role.RADIO_FLAC]
        entry.is_managed = hasattr(entry, "managed_recording")

    selected_id = request.GET.get("selected")
    selected = next((item for item in page.object_list if str(item.pk) == selected_id), None)
    if not selected and not selected_id and page.object_list:
        selected = page.object_list[0]
    if selected:
        selected.releases = list({track.release_id: track.release for track in selected.recording.release_tracks.all()}.values())
        selected.source_assertions = MetadataAssertion.objects.filter(
            entity_type=MetadataAssertion.EntityType.MUSIC_LIBRARY_ENTRY, entity_uuid=selected.pk
        ).select_related("source_record__source_system")[:10]
    return render(
        request,
        "gui_v2/music_library.html",
        {
            "section": "music_library", "filter_form": form, "page": page, "selected": selected,
            "query_without_page": _query_without(request, "page"),
            "query_without_selected": _query_without(request, "selected"),
            "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
        },
    )


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
        },
    )


@require_GET
@login_required
@permission_required("catalogue.view_recording", raise_exception=True)
def recording_search(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})
    queryset = Recording.objects.filter(Q(title__icontains=q) | Q(identifiers__normalized_value__icontains=q)).prefetch_related("contributions", "identifiers").distinct()[:12]
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
