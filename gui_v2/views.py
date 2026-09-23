import logging
import mimetypes
import os
import re
from collections import defaultdict
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from catalogue.authority import (
    annotate_recording_authority,
    protecting_releases,
)

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.staticfiles import finders
from django.core.exceptions import (
    ImproperlyConfigured,
    PermissionDenied,
    ValidationError,
)
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import (
    Case,
    CharField,
    Count,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Value,
    When,
    prefetch_related_objects,
)
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import (
    require_GET,
    require_http_methods,
    require_POST,
)

from catalogue.models import (
    DuplicateCandidate,
    ExternalIdentifier,
    Recording,
    RecordingContribution,
    Release,
    ReleaseTrack,
)
from catalogue.services import find_recording_candidates
from delivery.models import DeliveryArtifact, DeliveryItem, DeliveryProfile
from flac_ingest.models import FlacIngestItem
from flac_ingest.services import (
    apply_batch,
    preview_radio_file_split,
    scan_directory,
    split_radio_file_to_new_recording,
)
from managed_music.forms import ManagedReleaseForm
from managed_music.models import ManagedRecording, ManagedRelease
from managed_music.services import save_managed_release
from media_assets.models import FileAsset, FileLocation
from media_assets.playback import (
    RadioPlaybackStatus,
    iter_file_range,
    resolve_current_radio_asset,
    resolve_recording_playback,
)
from media_assets.pipeline_status import get_recording_media_pipeline_status
from media_assets.storage import get_client_folder, open_for_read
from music_library.models import (
    Channel,
    MusicLibraryChannel,
    MusicLibraryEntry,
    MusicLibraryTargetAudience,
    TargetAudience,
)
from provenance.models import MetadataAssertion

from .forms import MusicLibraryFilterForm, ReleaseMetadataForm, TrackRowFormSet
from .home_state import build_home_context, remember_object
from .presentation import compact_names, radio_language_name
from .recording_overview import (
    build_recording_overview,
    recording_overview_queryset,
    recording_release_tracks_with_covers_queryset,
    select_recording_cover,
)
from .recording_files import build_recording_files
from .services import save_release_track_rows
from workbench.forms import RadioMetadataForm

from media_assets.mastering import (
    activate_candidate,
    build_generation_preview,
    create_generation_plan,
    generate_candidate,
    inspect_master,
    register_master,
    select_master,
)
from media_assets.models import RadioFlacGeneration
from .forms import MasterRegistrationForm

logger = logging.getLogger(__name__)
PLAYBACK_PERMISSIONS = (
    "catalogue.view_recording",
    "media_assets.view_fileasset",
    "media_assets.view_filelocation",
)


def _artist_names(recording):
    names = [
        item.display_credit
        for item in recording.contributions.all()
        if item.role
        in {
            RecordingContribution.Role.PRIMARY,
            RecordingContribution.Role.FEATURED,
        }
    ]
    return list(dict.fromkeys(names))


def _artist_text(recording):
    return ", ".join(_artist_names(recording)) or "Uavklart artist"


def _duration(value):
    if value is None:
        return ""
    seconds = round(value / 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _playback_context(recording, user, *, radio_only=False):
    if not user.has_perms(PLAYBACK_PERMISSIONS):
        return {
            "status": "forbidden",
            "message": "Du har ikke tilgang til denne lydfilen.",
        }
    resolution = (
        resolve_current_radio_asset(recording)
        if radio_only
        else resolve_recording_playback(recording)
    )
    return {
        "status": resolution.status.value,
        "message": resolution.message,
        "source": resolution.source,
        "url": (
            reverse(
                (
                    "gui_v2:recording_radio_audio"
                    if radio_only
                    else "gui_v2:recording_audio"
                ),
                args=[recording.pk],
            )
            if resolution.status == RadioPlaybackStatus.AVAILABLE
            else ""
        ),
        "recording_id": str(recording.pk),
        "title": recording.title,
        "artist": _artist_text(recording),
    }


def _parse_byte_range(value, size):
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value or "")
    if not match or size <= 0:
        raise ValueError("invalid range")
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        raise ValueError("invalid range")
    if not start_text:
        suffix = int(end_text)
        if suffix <= 0:
            raise ValueError("invalid range")
        start = max(0, size - suffix)
        end = size - 1
    else:
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
        if start >= size or end < start:
            raise ValueError("invalid range")
        end = min(end, size - 1)
    return start, end


def _query_without(request, *names):
    query = request.GET.copy()
    for name in names:
        query.pop(name, None)
    return query.urlencode()


def _safe_return(request, default):
    value = request.POST.get("return") or request.GET.get("return") or ""
    if (
        value.startswith("/")
        and not value.startswith("//")
        and url_has_allowed_host_and_scheme(
            value,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
    ):
        return value
    return default


def _recording_workspace_context(request, recording, header, active_tab):
    """Use one return context and one set of tabs across Recording GUI v2."""
    return_url = _safe_return(request, reverse("gui_v2:music_library"))
    current_url = request.get_full_path()
    tab_urls = {
        name: f"{reverse(f'gui_v2:recording_{name}', args=[recording.pk])}?"
        f"{urlencode({'return': return_url})}"
        for name in ("files", "releases", "radio", "deliveries", "rights")
    }
    tab_urls["overview"] = (
        f"{reverse('gui_v2:recording_detail', args=[recording.pk])}?"
        f"{urlencode({'return': return_url})}"
    )
    workbench_url = reverse("workbench:recording", args=[recording.pk])
    for name in ("contributors", "sources"):
        tab_urls[name] = (
            f"{workbench_url}?{urlencode({'fane': name, 'return': current_url})}"
        )
    return {
        "section": "music_library",
        "recording": recording,
        "header": header,
        "active_tab": active_tab,
        "tab_urls": tab_urls,
        "return_url": return_url,
        "return_label": (
            "Tilbake til Musikkarkiv"
            if "/musikkarkiv/" in return_url
            else (
                "Tilbake til utgivelsen"
                if "/utgivelser/" in return_url
                else "Tilbake"
            )
        ),
    }


def _recording_header_recording(recording_id):
    contributions = RecordingContribution.objects.select_related(
        "party", "artist_identity"
    ).order_by("display_order", "id")
    return get_object_or_404(
        Recording.objects.prefetch_related(
            Prefetch("contributions", queryset=contributions), "identifiers"
        ),
        pk=recording_id,
    )


def _recording_header_data(request, recording, tracks=None):
    isrc = next(
        (
            identifier.normalized_value
            for identifier in recording.identifiers.all()
            if identifier.scheme == ExternalIdentifier.Scheme.ISRC
        ),
        "",
    )
    cover = None
    if request.user.is_staff and request.user.has_perms(
        ("media_assets.view_fileasset", "music_library.view_musiclibraryentry")
    ):
        if tracks is None:
            tracks = list(
                recording_release_tracks_with_covers_queryset().filter(
                    recording_id=recording.pk
                )
            )
        cover = select_recording_cover(tracks)
    return {
        "artist_text": _artist_text(recording),
        "isrc": isrc,
        "duration": _duration(recording.duration_ms),
        "cover": cover,
    }


def _entry_url(request, entry_id):
    query = request.GET.copy()
    query["selected"] = str(entry_id)
    return f"{reverse('gui_v2:music_library')}?{query.urlencode()}"


def _selected_entry_return(value, entry_id):
    parts = urlsplit(value)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["selected"] = str(entry_id)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            parts.fragment,
        )
    )


@require_GET
@login_required
def home(request):
    context = build_home_context(request)
    context["writes_enabled"] = settings.GUI_V2_WRITES_ENABLED
    return render(request, "gui_v2/home.html", context)


@require_GET
@login_required
@permission_required(
    "music_library.view_musiclibraryentry", raise_exception=True
)
def music_library(request):
    session_key = "gui_v2_music_library_query"
    if "reset" in request.GET:
        request.session.pop(session_key, None)
        return redirect("gui_v2:music_library")
    if not request.GET and (saved_query := request.session.get(session_key)):
        return redirect(f"{reverse('gui_v2:music_library')}?{saved_query}")
    form = MusicLibraryFilterForm(request.GET or None)
    if request.GET and form.is_valid():
        remembered = request.GET.copy()
        for name in list(remembered):
            if (
                name not in MusicLibraryFilterForm.base_fields
                and name != "page"
            ):
                remembered.pop(name)
        if remembered:
            request.session[session_key] = remembered.urlencode()
    selected_id = request.GET.get("selected")
    fragment_mode = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        and selected_id
    )
    active_filters = []
    saved_page_size = request.COOKIES.get("p7-v2-library-per-page", "40")
    page_size = (
        saved_page_size
        if saved_page_size in {"40", "100", "250", "500", "all"}
        else "40"
    )
    artist_prefetch = Prefetch(
        "recording__contributions",
        queryset=RecordingContribution.objects.select_related(
            "artist_identity", "party"
        ).order_by("display_order"),
    )
    radio_assets = FileAsset.objects.filter(
        recording_id=OuterRef("recording_id"), role=FileAsset.Role.RADIO_FLAC
    )
    radio_counts = (
        FileAsset.objects.filter(
            recording_id=OuterRef("recording_id"),
            role=FileAsset.Role.RADIO_FLAC,
        )
        .order_by()
        .values("recording_id")
        .annotate(total=Count("pk"))
        .values("total")[:1]
    )
    artist_sort = (
        RecordingContribution.objects.filter(
            recording_id=OuterRef("recording_id"),
            role__in=(
                RecordingContribution.Role.PRIMARY,
                RecordingContribution.Role.FEATURED,
            ),
        )
        .annotate(
            sort_name=Case(
                When(~Q(credited_as=""), then=F("credited_as")),
                When(
                    artist_identity__isnull=False,
                    then=F("artist_identity__display_name"),
                ),
                When(party__isnull=False, then=F("party__name")),
                default=Value(""),
                output_field=CharField(),
            )
        )
        .order_by("display_order", "id")
        .values("sort_name")[:1]
    )
    isrc_sort = ExternalIdentifier.objects.filter(
        recording_id=OuterRef("recording_id"),
        scheme=ExternalIdentifier.Scheme.ISRC,
    ).values("normalized_value")[:1]
    channel_sort = (
        MusicLibraryChannel.objects.filter(library_entry_id=OuterRef("pk"))
        .order_by("channel__name")
        .values("channel__name")[:1]
    )
    target_sort = (
        MusicLibraryTargetAudience.objects.filter(
            library_entry_id=OuterRef("pk")
        )
        .order_by("target_audience__name")
        .values("target_audience__name")[:1]
    )
    queryset = (
        annotate_recording_authority(
            MusicLibraryEntry.objects.all(), recording_ref="recording_id"
        )
        .select_related(
            "recording__media_selection__current_radio",
            "recording__media_selection__selected_master",
        )
        .prefetch_related(
            artist_prefetch,
            "channels",
            "target_audiences",
            "recording__identifiers",
            "recording__file_assets__locations",
            "recording__media_selection__current_radio__locations",
            "recording__media_selection__selected_master__locations",
            "recording__release_tracks__release",
        )
        .annotate(
            sort_artist=Subquery(artist_sort),
            sort_isrc=Subquery(isrc_sort),
            sort_channel=Subquery(channel_sort),
            sort_target=Subquery(target_sort),
            sort_managed=F("authority_managed"),
            radio_file_count=Coalesce(Subquery(radio_counts), Value(0)),
            has_radio_file=Exists(radio_assets),
            has_active_radio_location=Exists(
                FileLocation.objects.filter(
                    asset__recording_id=OuterRef("recording_id"),
                    asset__role=FileAsset.Role.RADIO_FLAC,
                    is_current=True,
                    status=FileLocation.Status.ACTIVE,
                )
            ),
            has_file_problem=Exists(
                radio_assets.filter(
                    sync_status__in=(
                        FileAsset.SyncStatus.MISSING,
                        FileAsset.SyncStatus.CONFLICT,
                        FileAsset.SyncStatus.FAILED,
                    )
                )
            ),
            has_release=Exists(
                ReleaseTrack.objects.filter(
                    recording_id=OuterRef("recording_id")
                )
            ),
            has_duplicate_a=Exists(
                DuplicateCandidate.objects.filter(
                    recording_a_id=OuterRef("recording_id"),
                    status=DuplicateCandidate.Status.OPEN,
                )
            ),
            has_duplicate_b=Exists(
                DuplicateCandidate.objects.filter(
                    recording_b_id=OuterRef("recording_id"),
                    status=DuplicateCandidate.Status.OPEN,
                )
            ),
            has_ingest_issue=Exists(
                FlacIngestItem.objects.filter(
                    recording_id=OuterRef("recording_id"),
                    applied_at__isnull=True,
                    action__in=(
                        FlacIngestItem.Action.CONFLICT,
                        FlacIngestItem.Action.RETRY,
                        FlacIngestItem.Action.INVALID,
                    ),
                )
            ),
        )
        .annotate(
            sort_file_status=Case(
                When(has_file_problem=True, then=Value(3)),
                When(has_active_radio_location=True, then=Value(2)),
                When(has_radio_file=True, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
            sort_follow_up=Case(
                When(
                    Q(has_radio_file=False)
                    | Q(has_active_radio_location=False)
                    | Q(has_file_problem=True)
                    | Q(has_release=False)
                    | Q(has_duplicate_a=True)
                    | Q(has_duplicate_b=True)
                    | Q(has_ingest_issue=True)
                    | (Q(radio_file_count__gt=1) & Q(sort_isrc__isnull=False)),
                    then=Value(1),
                ),
                default=Value(0),
                output_field=IntegerField(),
            ),
        )
    )
    needs_distinct = False
    if form.is_valid():
        data = form.cleaned_data
        page_size = data.get("per_page") or page_size
        term = (data.get("q") or "").strip()
        if term:
            matching_contributions = RecordingContribution.objects.filter(
                recording_id=OuterRef("recording_id")
            ).filter(
                Q(credited_as__icontains=term)
                | Q(artist_identity__display_name__icontains=term)
                | Q(party__name__icontains=term)
            )
            matching_identifiers = ExternalIdentifier.objects.filter(
                recording_id=OuterRef("recording_id"),
                normalized_value__icontains=term,
            )
            queryset = queryset.filter(
                Q(recording__title__icontains=term)
                | Q(Exists(matching_contributions))
                | Q(Exists(matching_identifiers))
            )
        selected_genres = [
            value for value in request.GET.getlist("genre") if value
        ]
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
        rotations = [
            value
            for value in request.GET.getlist("rotation_suitability")
            if value
        ]
        if rotations:
            needs_distinct = True
            rotation_query = Q(pk__in=[])
            for rotation in rotations:
                if rotation == MusicLibraryEntry.RotationSuitability.SUITABLE:
                    rotation_query |= (
                        Q(
                            rotation_suitability=MusicLibraryEntry.RotationSuitability.SUITABLE
                        )
                        | Q(channels__isnull=False)
                    ) & ~Q(
                        rotation_suitability__in=(
                            MusicLibraryEntry.RotationSuitability.NOT_SUITABLE,
                            MusicLibraryEntry.RotationSuitability.UNASSESSED,
                        )
                    )
                elif (
                    rotation
                    == MusicLibraryEntry.RotationSuitability.NOT_SUITABLE
                ):
                    rotation_query |= Q(rotation_suitability=rotation)
                elif (
                    rotation
                    == MusicLibraryEntry.RotationSuitability.UNASSESSED
                ):
                    rotation_query |= Q(rotation_suitability=rotation) | Q(
                        rotation_suitability="", channels__isnull=True
                    )
            queryset = queryset.filter(rotation_query)
        managed_values = set(request.GET.getlist("managed")) & {
            "yes",
            "no",
            "release_only",
        }
        if managed_values:
            managed_query = Q()
            if "yes" in managed_values:
                managed_query |= Q(authority_managed=True)
            if "no" in managed_values:
                managed_query |= Q(authority_managed=False)
            if "release_only" in managed_values:
                managed_query |= Q(authority_release_only=True)
            queryset = queryset.filter(managed_query)
        for relation, mode_name, chosen in (
            ("channels", "channel_mode", data.get("channels") or []),
            (
                "target_audiences",
                "target_mode",
                data.get("target_audiences") or [],
            ),
        ):
            if chosen and data.get(mode_name) == "all":
                needs_distinct = True
                for value in chosen:
                    queryset = queryset.filter(**{relation: value})
            elif chosen:
                needs_distinct = True
                queryset = queryset.filter(**{f"{relation}__in": chosen})
        file_statuses = set(request.GET.getlist("file_status")) & {
            "available",
            "missing",
            "problem",
            "none",
        }
        radio = Q(recording__file_assets__role=FileAsset.Role.RADIO_FLAC)
        if file_statuses:
            needs_distinct = True
            file_query = Q(pk__in=[])
            if "available" in file_statuses:
                file_query |= radio & Q(
                    recording__file_assets__locations__status="active"
                )
            if "missing" in file_statuses:
                file_query |= radio & Q(
                    recording__file_assets__locations__status="missing"
                )
            if "problem" in file_statuses:
                file_query |= radio & Q(
                    recording__file_assets__sync_status__in=[
                        "conflict",
                        "failed",
                        "missing",
                    ]
                )
            if "none" in file_statuses:
                file_query |= Q(has_radio_file=False)
            queryset = queryset.filter(file_query)
        needs_follow_up = (
            Q(has_radio_file=False)
            | Q(has_active_radio_location=False)
            | Q(has_file_problem=True)
            | Q(has_release=False)
            | Q(has_duplicate_a=True)
            | Q(has_duplicate_b=True)
            | Q(has_ingest_issue=True)
            | (Q(radio_file_count__gt=1) & Q(sort_isrc__isnull=False))
        )
        follow_up_values = set(request.GET.getlist("follow_up")) & {
            "yes",
            "no",
        }
        if follow_up_values == {"yes"}:
            queryset = queryset.filter(needs_follow_up)
        elif follow_up_values == {"no"}:
            queryset = queryset.exclude(needs_follow_up)
        if data.get("isrc_file_collision"):
            queryset = queryset.filter(
                radio_file_count__gt=1, sort_isrc__isnull=False
            )
        current_ordering = data.get("ordering") or "title"
        ordering = {
            "title": ("recording__title", "id"),
            "-title": ("-recording__title", "id"),
            "artist": ("sort_artist", "recording__title", "id"),
            "-artist": ("-sort_artist", "recording__title", "id"),
            "isrc": ("sort_isrc", "recording__title", "id"),
            "-isrc": ("-sort_isrc", "recording__title", "id"),
            "duration": ("recording__duration_ms", "recording__title", "id"),
            "-duration": ("-recording__duration_ms", "recording__title", "id"),
            "genre": ("genre", "recording__title", "id"),
            "-genre": ("-genre", "recording__title", "id"),
            "language": ("language", "recording__title", "id"),
            "-language": ("-language", "recording__title", "id"),
            "energy": ("energy", "recording__title", "id"),
            "-energy": ("-energy", "recording__title", "id"),
            "rotation": ("rotation_suitability", "recording__title", "id"),
            "-rotation": ("-rotation_suitability", "recording__title", "id"),
            "channels": ("sort_channel", "recording__title", "id"),
            "-channels": ("-sort_channel", "recording__title", "id"),
            "targets": ("sort_target", "recording__title", "id"),
            "-targets": ("-sort_target", "recording__title", "id"),
            "file_status": ("sort_file_status", "recording__title", "id"),
            "-file_status": ("-sort_file_status", "recording__title", "id"),
            "managed": ("sort_managed", "recording__title", "id"),
            "-managed": ("-sort_managed", "recording__title", "id"),
            "follow_up": ("sort_follow_up", "recording__title", "id"),
            "-follow_up": ("-sort_follow_up", "recording__title", "id"),
            "-updated": ("-updated_at", "id"),
            "updated": ("updated_at", "id"),
        }.get(current_ordering, ("recording__title", "id"))
        queryset = queryset.order_by(*ordering)
        simple_labels = {"q": "Søk", "gender": "Vokal"}
        for name, label in simple_labels.items():
            value = data.get(name)
            if value not in (None, ""):
                display = (
                    dict(form.fields[name].choices).get(value, value)
                    if hasattr(form.fields[name], "choices")
                    else value
                )
                active_filters.append(
                    {
                        "label": f"{label}: {display}",
                        "query": _query_without(request, name, "page"),
                    }
                )
        multi_labels = {
            "genre": "Sjanger",
            "language": "Språk",
            "energy": "Energy",
            "rotation_suitability": "Rotasjon",
            "file_status": "Filstatus",
            "managed": "Forvaltning",
            "follow_up": "Oppfølging",
        }
        for name, label in multi_labels.items():
            values = [value for value in request.GET.getlist(name) if value]
            if values:
                choices = {
                    str(key): str(value)
                    for key, value in form.fields[name].choices
                }
                display = ", ".join(
                    choices.get(str(value), str(value)) for value in values
                )
                active_filters.append(
                    {
                        "label": f"{label}: {display}",
                        "query": _query_without(
                            request, name, "page", "column_filter"
                        ),
                    }
                )
        if data.get("isrc_file_collision"):
            active_filters.append(
                {
                    "label": "Flere radio-FLAC med samme ISRC",
                    "query": _query_without(
                        request, "isrc_file_collision", "page"
                    ),
                }
            )
        for name, mode, label in (
            ("channels", "channel_mode", "Kanal"),
            ("target_audiences", "target_mode", "Målgruppe"),
        ):
            values = data.get(name) or []
            if values:
                qualifier = "alle" if data.get(mode) == "all" else "minst én"
                active_filters.append(
                    {
                        "label": f"{label} ({qualifier}): {', '.join(str(value) for value in values)}",
                        "query": _query_without(
                            request, name, mode, "page", "column_filter"
                        ),
                    }
                )
    else:
        current_ordering = "title"
        queryset = queryset.order_by("recording__title", "id")
    if fragment_mode:
        queryset = queryset.filter(pk=selected_id)
    if needs_distinct:
        queryset = queryset.distinct()
    paginator_size = (
        max(queryset.count(), 1) if page_size == "all" else int(page_size)
    )
    page = Paginator(queryset, paginator_size).get_page(
        request.GET.get("page")
    )
    can_serve_cover = request.user.is_staff and request.user.has_perms(
        (
            "media_assets.view_fileasset",
            "media_assets.view_filelocation",
        )
    )
    covers_by_recording = {}
    if can_serve_cover:
        tracks_by_recording = defaultdict(list)
        cover_tracks = recording_release_tracks_with_covers_queryset().filter(
            recording_id__in=[entry.recording_id for entry in page.object_list]
        )
        for track in cover_tracks:
            tracks_by_recording[track.recording_id].append(track)
        covers_by_recording = {
            recording_id: select_recording_cover(tracks)
            for recording_id, tracks in tracks_by_recording.items()
        }
    for entry in page.object_list:
        entry.cover = covers_by_recording.get(entry.recording_id)
        entry.artist_names = _artist_names(entry.recording)
        entry.artist_text = ", ".join(entry.artist_names) or "Uavklart artist"
        entry.playback = _playback_context(entry.recording, request.user)
        entry.isrc = next(
            (
                item.normalized_value
                for item in entry.recording.identifiers.all()
                if item.scheme == ExternalIdentifier.Scheme.ISRC
            ),
            "",
        )
        entry.duration_text = _duration(entry.recording.duration_ms)
        entry.radio_files = [
            item
            for item in entry.recording.file_assets.all()
            if item.role == FileAsset.Role.RADIO_FLAC
        ]
        entry.is_managed = entry.sort_managed
        entry.managed_release_only = entry.authority_release_only
        entry.language_display = radio_language_name(entry.language)
        channel_values = list(entry.channels.all())
        target_values = list(entry.target_audiences.all())
        entry.channel_summary = compact_names(channel_values)
        entry.channel_names = ", ".join(str(value) for value in channel_values)
        entry.target_summary = compact_names(target_values)
        entry.target_names = ", ".join(str(value) for value in target_values)
        if (
            entry.rotation_suitability
            == MusicLibraryEntry.RotationSuitability.NOT_SUITABLE
        ):
            entry.rotation_display = (
                MusicLibraryEntry.RotationSuitability.NOT_SUITABLE.label
            )
            entry.rotation_kind = "warning"
        elif (
            entry.rotation_suitability
            == MusicLibraryEntry.RotationSuitability.UNASSESSED
        ):
            entry.rotation_display = (
                MusicLibraryEntry.RotationSuitability.UNASSESSED.label
            )
            entry.rotation_kind = "muted"
        elif (
            entry.rotation_suitability
            == MusicLibraryEntry.RotationSuitability.SUITABLE
            or channel_values
        ):
            entry.rotation_display = (
                MusicLibraryEntry.RotationSuitability.SUITABLE.label
            )
            entry.rotation_kind = "ok"
        else:
            entry.rotation_display = "Ikke vurdert"
            entry.rotation_kind = "muted"
        entry.preview_url = _entry_url(request, entry.pk)
        entry.detail_url = f"{reverse('gui_v2:recording_detail', args=[entry.recording_id])}?{urlencode({'return': entry.preview_url})}"
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
        if entry.radio_file_count > 1 and entry.isrc:
            entry.follow_up_reasons.append("Flere radio-FLAC med samme ISRC")
        entry.last_read_at = max(
            (
                item.metadata_read_at
                for item in entry.radio_files
                if item.metadata_read_at
            ),
            default=None,
        )
        if not entry.radio_files:
            entry.file_status_text = "Ingen radiofil"
            entry.file_status_kind = "muted"
        elif any(
            location.status == "active"
            for item in entry.radio_files
            for location in item.locations.all()
        ):
            entry.file_status_text = "Tilgjengelig"
            entry.file_status_kind = "ok"
        elif any(
            item.sync_status in {"failed", "conflict", "missing"}
            for item in entry.radio_files
        ):
            entry.file_status_text = entry.radio_files[
                0
            ].get_sync_status_display()
            entry.file_status_kind = "error"
        else:
            entry.file_status_text = "Ikke kontrollert"
            entry.file_status_kind = "muted"

    selected = next(
        (item for item in page.object_list if str(item.pk) == selected_id),
        None,
    )
    if selected is None and page.object_list:
        selected = page.object_list[0]
    if selected:
        selected.protecting_releases = (
            list(protecting_releases(selected.recording))
            if selected.authority_via_release
            and request.user.has_perm("catalogue.view_release")
            and request.user.has_perm("managed_music.view_managedrelease")
            else []
        )
        selected.releases = list(
            {
                track.release_id: track.release
                for track in selected.recording.release_tracks.all()
            }.values()
        )
        selected.source_assertions = MetadataAssertion.objects.filter(
            entity_type=MetadataAssertion.EntityType.MUSIC_LIBRARY_ENTRY,
            entity_uuid=selected.pk,
        ).select_related("source_record__source_system")[:10]
        for asset in selected.radio_files:
            asset.current_locations = [
                location
                for location in asset.locations.all()
                if location.is_current
            ]
            asset.can_split = (
                len(selected.radio_files) > 1
                and not selected.is_managed
                and not selected.authority_via_release
                and request.user.is_staff
                and request.user.has_perms(
                    (
                        "catalogue.add_recording",
                        "catalogue.change_releasetrack",
                        "media_assets.change_fileasset",
                        "music_library.add_musiclibraryentry",
                        "flac_ingest.apply_flacingestbatch",
                    )
                )
            )
            for location in asset.current_locations:
                try:
                    if location.storage_type == FileLocation.StorageType.NAS:
                        location.onetagger_path = (
                            get_client_folder(location) or ""
                        )
                    else:
                        location.onetagger_path = ""
                except (ImproperlyConfigured, ValidationError, OSError):
                    location.onetagger_path = ""
    if fragment_mode:
        if selected is None:
            raise Http404
        return render(
            request,
            "gui_v2/includes/music_library_inspector.html",
            {
                "selected": selected,
                "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
            },
        )
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
    target_filter_options = list(
        TargetAudience.objects.filter(is_active=True).order_by("name")
    )
    for target in target_filter_options:
        target.is_filter_selected = str(target.pk) in selected_target_ids
    return render(
        request,
        "gui_v2/music_library.html",
        {
            "section": "music_library",
            "filter_form": form,
            "page": page,
            "selected": selected,
            "query_without_page": _query_without(request, "page"),
            "sort_query": _query_without(
                request, "ordering", "page", "selected"
            ),
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
@permission_required(
    "music_library.view_musiclibraryentry", raise_exception=True
)
def channel_logo(request, channel_id):
    channel = get_object_or_404(Channel, pk=channel_id)
    if not channel.logo:
        raise Http404("Kanalen har ingen egendefinert logo.")
    content_type = (
        mimetypes.guess_type(channel.logo.name)[0]
        or "application/octet-stream"
    )
    response = FileResponse(
        channel.logo.open("rb"),
        content_type=content_type,
        filename=PurePosixPath(channel.logo.name).name,
    )
    response["Cache-Control"] = "private, max-age=300"
    return response


@require_GET
@login_required
@permission_required("catalogue.view_recording", raise_exception=True)
def recording_detail(request, recording_id):
    """Render the read-only GUI v2 overview for one canonical Recording."""
    recording = get_object_or_404(
        recording_overview_queryset(), pk=recording_id
    )
    remember_object(request, "recording", recording.pk)
    current_url = request.get_full_path()
    can_view_releases = request.user.has_perm("catalogue.view_release")
    can_view_files = request.user.has_perms(
        ("media_assets.view_fileasset", "media_assets.view_filelocation")
    )
    can_serve_cover = (
        can_view_files
        and request.user.is_staff
        and request.user.has_perm("music_library.view_musiclibraryentry")
    )
    overview = build_recording_overview(
        recording,
        can_view_files=can_view_files,
        can_view_releases=can_view_releases,
    )
    overview["playback"] = _playback_context(recording, request.user)
    if overview["cover"] and not can_serve_cover:
        overview["cover"] = None
    workspace = _recording_workspace_context(
        request, recording, overview, "overview"
    )
    for track in overview["releases"]:
        track.gui_v2_url = (
            f"{reverse('gui_v2:release_detail', args=[track.release_id])}?"
            f"{urlencode({'tab': 'tracks', 'track': track.pk, 'return': current_url})}"
        )
    return render(
        request,
        "gui_v2/recording_detail.html",
        {
            **workspace,
            "overview": overview,
            "can_view_files": can_view_files,
            "can_view_releases": can_view_releases,
        },
    )


@require_GET
@login_required
@permission_required(
    (
        "catalogue.view_recording",
        "media_assets.view_fileasset",
        "media_assets.view_filelocation",
    ),
    raise_exception=True,
)
def recording_files(request, recording_id):
    """Render the read-only GUI v2 media inventory for one Recording."""
    recording = get_object_or_404(
        recording_overview_queryset(), pk=recording_id
    )
    overview = build_recording_overview(
        recording,
        can_view_files=True,
        can_view_releases=request.user.has_perm("catalogue.view_release"),
    )
    overview["playback"] = _playback_context(recording, request.user)
    if overview["cover"] and not (
        request.user.is_staff
        and request.user.has_perm("music_library.view_musiclibraryentry")
    ):
        overview["cover"] = None
    file_view = build_recording_files(
        recording, selected_asset_id=request.GET.get("selected_file")
    )
    workspace = _recording_workspace_context(
        request, recording, overview, "files"
    )
    return render(
        request,
        "gui_v2/recording_files.html",
        {
            **workspace,
            "overview": overview,
            "file_view": file_view,
            "radio_playback": _playback_context(
                recording, request.user, radio_only=True
            ),
            "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
            "can_select_master": settings.GUI_V2_WRITES_ENABLED
            and request.user.has_perms(MASTER_CHANGE_PERMISSIONS),
        },
    )


@require_GET
@login_required
@permission_required(
    ("catalogue.view_recording", "catalogue.view_release"),
    raise_exception=True,
)
def recording_releases(request, recording_id):
    recording = _recording_header_recording(recording_id)
    remember_object(request, "recording", recording.pk)
    tracks = list(
        recording_release_tracks_with_covers_queryset()
        .filter(recording_id=recording.pk)
        .select_related("release__managed_release")
        .annotate(release_track_count=Count("release__tracks", distinct=True))
    )
    can_view_management = request.user.has_perm(
        "managed_music.view_managedrelease"
    )
    for track in tracks:
        position = []
        if track.disc_number:
            position.append(f"Disc {track.disc_number}")
        if track.side:
            position.append(f"Side {track.side}")
        position.append(f"Spor {track.track_number or track.sequence_number}")
        track.position_text = " · ".join(position)
        track.duration_text = _duration(track.duration_ms)
        track.release_url = (
            f"{reverse('gui_v2:release_detail', args=[track.release_id])}?"
            f"{urlencode({'tab': 'tracks', 'track': track.pk, 'return': request.get_full_path()})}"
        )
        try:
            track.management = (
                track.release.managed_release if can_view_management else None
            )
        except ManagedRelease.DoesNotExist:
            track.management = None
    selected = next(
        (
            track
            for track in tracks
            if str(track.pk) == request.GET.get("track")
        ),
        tracks[0] if tracks else None,
    )
    context = _recording_workspace_context(
        request,
        recording,
        _recording_header_data(request, recording, tracks),
        "releases",
    )
    context.update(
        {
            "tracks": tracks,
            "selected_track": selected,
            "can_view_management": can_view_management,
        }
    )
    return render(request, "gui_v2/recording_releases.html", context)


@login_required
@permission_required(
    ("catalogue.view_recording", "music_library.view_musiclibraryentry"),
    raise_exception=True,
)
@require_http_methods(["GET", "POST"])
def recording_radio(request, recording_id):
    recording = _recording_header_recording(recording_id)
    remember_object(request, "recording", recording.pk)
    entry = (
        MusicLibraryEntry.objects.filter(recording_id=recording.pk)
        .prefetch_related("channels", "target_audiences")
        .first()
    )
    can_edit = settings.GUI_V2_WRITES_ENABLED and request.user.has_perm(
        "music_library.change_musiclibraryentry"
    )
    if request.method == "POST" and not can_edit:
        raise PermissionDenied
    if request.method == "POST" and entry is None:
        raise Http404("Innspillingen har ingen musikkarkivpost.")
    context = _recording_workspace_context(
        request, recording, _recording_header_data(request, recording), "radio"
    )
    form = (
        RadioMetadataForm(request.POST or None, instance=entry)
        if entry
        else None
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
        messages.success(request, "Radiometadata ble lagret.")
        return redirect(context["tab_urls"]["radio"])
    playback = _playback_context(recording, request.user, radio_only=True)
    resolution = resolve_current_radio_asset(recording)
    context.update(
        {
            "entry": entry,
            "form": form,
            "can_edit_radio": can_edit,
            "playback": playback,
            "radio_asset": (
                resolution.asset
                if request.user.has_perms(PLAYBACK_PERMISSIONS)
                else None
            ),
            "channels": list(entry.channels.all()) if entry else [],
            "target_audiences": (
                list(entry.target_audiences.all()) if entry else []
            ),
            "radio_language": (
                radio_language_name(entry.language) if entry else ""
            ),
        }
    )
    return render(request, "gui_v2/recording_radio.html", context)


@require_GET
@login_required
@permission_required(
    ("catalogue.view_recording", "delivery.view_delivery"),
    raise_exception=True,
)
def recording_deliveries(request, recording_id):
    recording = _recording_header_recording(recording_id)
    remember_object(request, "recording", recording.pk)
    items = list(
        DeliveryItem.objects.filter(recording_id=recording.pk)
        .select_related("delivery__created_by", "source_file_asset")
        .order_by("-delivery__created_at", "-created_at")
    )
    selected = next(
        (
            item
            for item in items
            if str(item.delivery_id) == request.GET.get("delivery")
        ),
        items[0] if items else None,
    )
    artifact = None
    active_artifact = None
    download_events = []
    can_download = False
    if selected:
        artifact = (
            DeliveryArtifact.objects.filter(delivery=selected.delivery)
            .order_by("-created_at", "-id")
            .first()
        )
        active_artifact = (
            DeliveryArtifact.objects.filter(
                delivery=selected.delivery, removed_at__isnull=True
            )
            .order_by("-created_at", "-id")
            .first()
        )
        download_events = list(
            selected.delivery.download_events.select_related("actor").order_by(
                "-created_at"
            )
        )
        ready_artifact = (
            active_artifact and active_artifact.expires_at > timezone.now()
        )
        direct_internal = (
            selected.delivery.profile == DeliveryProfile.INTERNAL_COMPLETE
            and selected.status == DeliveryItem.Status.READY
            and active_artifact is None
            and selected.delivery.items.filter(
                status=DeliveryItem.Status.READY
            ).count()
            == 1
        )
        can_download = (
            request.user.has_perm("delivery.download_delivery")
            and selected.delivery.status
            not in {
                selected.delivery.Status.FAILED,
                selected.delivery.Status.EXPIRED,
            }
            and bool(ready_artifact or direct_internal)
        )
    context = _recording_workspace_context(
        request,
        recording,
        _recording_header_data(request, recording),
        "deliveries",
    )
    context.update(
        {
            "items": items,
            "selected_item": selected,
            "artifact": artifact,
            "now": timezone.now(),
            "download_events": download_events,
            "can_download": can_download,
        }
    )
    return render(request, "gui_v2/recording_deliveries.html", context)


MASTER_VIEW_PERMISSIONS = (
    "catalogue.view_recording",
    "media_assets.view_fileasset",
    "media_assets.view_filelocation",
)

MASTER_CHANGE_PERMISSIONS = (
    *MASTER_VIEW_PERMISSIONS,
    "media_assets.add_fileasset",
    "media_assets.add_filelocation",
    "media_assets.change_fileasset",
    "media_assets.add_recordingmediaselection",
    "media_assets.change_recordingmediaselection",
    "media_assets.add_mediaassetevent",
)

GENERATION_CHANGE_PERMISSIONS = (
    *MASTER_CHANGE_PERMISSIONS,
    "media_assets.add_filechecksum",
    "media_assets.add_filederivation",
    "media_assets.add_radioflacgeneration",
    "media_assets.change_radioflacgeneration",
)


def _require_gui_media_writes():
    if not settings.GUI_V2_WRITES_ENABLED:
        raise PermissionDenied(
            "Medieendringer er deaktivert i dette GUI-v2-oppsettet."
        )


@login_required
@permission_required(MASTER_VIEW_PERMISSIONS, raise_exception=True)
@require_http_methods(["GET", "POST"])
def recording_master_register(request, recording_id):
    recording = get_object_or_404(
        recording_overview_queryset(), pk=recording_id
    )
    form = MasterRegistrationForm(request.POST or None)
    inspection = None
    if request.method == "POST" and form.is_valid():
        try:
            inspection = inspect_master(**form.cleaned_data)
            if request.POST.get("action") == "confirm":
                _require_gui_media_writes()
                if not request.user.has_perms(MASTER_CHANGE_PERMISSIONS):
                    raise PermissionDenied
                asset = register_master(
                    recording=recording,
                    user=request.user,
                    **form.cleaned_data,
                )
                messages.success(
                    request,
                    "Masterfilen er registrert uten å endre kildefilen.",
                )
                return redirect(
                    f"{reverse('gui_v2:recording_files', args=[recording.pk])}"
                    f"?selected_file={asset.pk}"
                )
        except (ImproperlyConfigured, OSError, ValidationError) as error:
            form.add_error(None, error)
    return render(
        request,
        "gui_v2/recording_master_register.html",
        {
            "section": "music_library",
            "recording": recording,
            "form": form,
            "inspection": inspection,
            "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
            "return_url": reverse(
                "gui_v2:recording_files", args=[recording.pk]
            ),
        },
    )


@require_POST
@login_required
@permission_required(MASTER_CHANGE_PERMISSIONS, raise_exception=True)
def recording_master_select(request, recording_id, asset_id):
    _require_gui_media_writes()
    recording = get_object_or_404(Recording, pk=recording_id)
    asset = get_object_or_404(FileAsset, pk=asset_id, recording=recording)
    try:
        select_master(recording=recording, asset=asset, user=request.user)
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
    else:
        messages.success(
            request, f"{asset.filename} er valgt som autoritativ master."
        )
    return redirect(
        f"{reverse('gui_v2:recording_files', args=[recording.pk])}"
        f"?selected_file={asset.pk}"
    )


@login_required
@permission_required(MASTER_VIEW_PERMISSIONS, raise_exception=True)
@require_http_methods(["GET", "POST"])
def recording_generation_preview(request, recording_id):
    recording = get_object_or_404(
        recording_overview_queryset(), pk=recording_id
    )
    generation = None
    preview = None
    error_message = ""
    can_plan = settings.GUI_V2_WRITES_ENABLED and request.user.has_perms(
        GENERATION_CHANGE_PERMISSIONS
    )
    generation_blockers = []
    if not settings.GUI_V2_WRITES_ENABLED:
        generation_blockers.append(
            "GUI-v2-skriving er deaktivert. En administrator må aktivere GUI_V2_WRITES_ENABLED."
        )
    if not request.user.has_perms(GENERATION_CHANGE_PERMISSIONS):
        generation_blockers.append(
            "Kontoen mangler nødvendige tillatelser til å opprette og generere radiofiler."
        )
    if not settings.P7_ALLOW_FILE_WRITES:
        generation_blockers.append(
            "Fysisk filskriving er deaktivert (P7_ALLOW_FILE_WRITES). En administrator må aktivere dette før generering."
        )
    target_root_key = getattr(
        settings, "P7_GENERATED_MEDIA_ROOT_KEY", "generated_media"
    )
    target_root = (getattr(settings, "P7_STORAGE_ROOTS", {}) or {}).get(
        target_root_key
    )
    if not isinstance(target_root, dict) or not target_root.get("server_root"):
        generation_blockers.append(
            f"Målområdet {target_root_key} er ikke konfigurert. En administrator må angi P7_GENERATED_MEDIA_ROOT."
        )
    elif target_root.get("read_only", True):
        generation_blockers.append(
            f"Målområdet {target_root_key} er konfigurert som skrivebeskyttet."
        )
    generation_id = request.GET.get("generation")
    if generation_id:
        generation = get_object_or_404(
            RadioFlacGeneration.objects.select_related(
                "master_asset", "radio_metadata_source", "candidate_asset"
            ),
            pk=generation_id,
            recording=recording,
        )
    else:
        try:
            preview = build_generation_preview(
                recording=recording,
                target_relative_path=request.GET.get("target_relative_path") or None,
                allow_existing_target=True,
            )
        except (ImproperlyConfigured, OSError, ValidationError) as error:
            error_message = "; ".join(getattr(error, "messages", [str(error)]))
    if request.method == "POST":
        _require_gui_media_writes()
        if not request.user.has_perms(GENERATION_CHANGE_PERMISSIONS):
            raise PermissionDenied
        try:
            generation = create_generation_plan(
                recording=recording,
                user=request.user,
                target_relative_path=request.POST.get("target_relative_path"),
                database_metadata_confirmed=request.POST.get(
                    "database_metadata_confirmed"
                )
                == "yes",
                expected_metadata_digest=request.POST.get("metadata_digest"),
            )
        except (ImproperlyConfigured, OSError, ValidationError) as error:
            error_message = "; ".join(getattr(error, "messages", [str(error)]))
        else:
            return redirect(
                f"{reverse('gui_v2:recording_generation_preview', args=[recording.pk])}"
                f"?generation={generation.pk}"
                f"&return={quote(_safe_return(request, reverse('gui_v2:recording_files', args=[recording.pk])), safe='')}"
            )
    alternate_target = ""
    if preview and preview["target_exists"]:
        target = PurePosixPath(preview["target_relative_path"])
        alternate_target = str(
            target.with_name(f"{target.stem}-ny{target.suffix}")
        )
    return render(
        request,
        "gui_v2/recording_generation_preview.html",
        {
            "section": "music_library",
            "recording": recording,
            "pipeline_status": get_recording_media_pipeline_status(recording),
            "preview": preview,
            "generation": generation,
            "error_message": error_message,
            "generation_blockers": generation_blockers,
            "alternate_target": alternate_target,
            "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
            "file_writes_enabled": settings.P7_ALLOW_FILE_WRITES,
            "can_generate": can_plan,
            "return_url": _safe_return(
                request,
                reverse("gui_v2:recording_files", args=[recording.pk]),
            ),
        },
    )


@require_POST
@login_required
@permission_required(GENERATION_CHANGE_PERMISSIONS, raise_exception=True)
def recording_generate_candidate(request, recording_id, generation_id):
    _require_gui_media_writes()
    generation = get_object_or_404(
        RadioFlacGeneration, pk=generation_id, recording_id=recording_id
    )
    try:
        generate_candidate(generation=generation, user=request.user)
    except (
        ImproperlyConfigured,
        OSError,
        PermissionDenied,
        ValidationError,
    ) as error:
        messages.error(
            request, "; ".join(getattr(error, "messages", [str(error)]))
        )
    else:
        messages.success(
            request, "Radio-FLAC-kandidaten er generert og verifisert."
        )
    return redirect(
        f"{reverse('gui_v2:recording_generation_preview', args=[recording_id])}"
        f"?generation={generation.pk}"
        f"&return={quote(_safe_return(request, reverse('gui_v2:recording_files', args=[recording_id])), safe='')}"
    )


@require_POST
@login_required
@permission_required(GENERATION_CHANGE_PERMISSIONS, raise_exception=True)
def recording_activate_candidate(request, recording_id, generation_id):
    _require_gui_media_writes()
    generation = get_object_or_404(
        RadioFlacGeneration, pk=generation_id, recording_id=recording_id
    )
    try:
        candidate = activate_candidate(
            generation=generation, user=request.user
        )
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
    else:
        messages.success(
            request, "Kandidaten er aktivert som gjeldende radiofil."
        )
        return redirect(
            _safe_return(
                request,
                f"{reverse('gui_v2:recording_files', args=[recording_id])}"
                f"?selected_file={candidate.pk}",
            )
        )
    return redirect(
        f"{reverse('gui_v2:recording_generation_preview', args=[recording_id])}"
        f"?generation={generation.pk}"
        f"&return={quote(_safe_return(request, reverse('gui_v2:recording_files', args=[recording_id])), safe='')}"
    )


@require_http_methods(["GET", "HEAD"])
@login_required
@permission_required(PLAYBACK_PERMISSIONS, raise_exception=True)
def recording_audio(request, recording_id, radio_only=False):
    """Stream selected master, falling back to radio only without a selection."""
    recording = get_object_or_404(
        Recording.objects.prefetch_related("file_assets__locations"),
        pk=recording_id,
    )
    resolver = (
        resolve_current_radio_asset
        if radio_only
        else resolve_recording_playback
    )
    resolution = resolver(recording, verify_file=True)
    if resolution.status != RadioPlaybackStatus.AVAILABLE:
        status_code = (
            409 if resolution.status == RadioPlaybackStatus.AMBIGUOUS else 404
        )
        logger.warning(
            "Recording playback unavailable",
            extra={
                "recording_uuid": str(recording.pk),
                "file_asset_id": (
                    str(resolution.asset.pk) if resolution.asset else ""
                ),
                "file_location_id": (
                    str(resolution.location.pk) if resolution.location else ""
                ),
                "playback_error": resolution.status.value,
            },
        )
        return HttpResponse(
            resolution.message,
            status=status_code,
            content_type="text/plain; charset=utf-8",
        )

    handle = None
    try:
        handle = open_for_read(resolution.resolved_location)
        size = os.fstat(handle.fileno()).st_size
    except (OSError, ValidationError, ImproperlyConfigured):
        if handle is not None:
            handle.close()
        logger.warning(
            "Recording playback file could not be opened",
            extra={
                "recording_uuid": str(recording.pk),
                "file_asset_id": str(resolution.asset.pk),
                "file_location_id": str(resolution.location.pk),
                "playback_error": "open_failed",
            },
        )
        return HttpResponse(
            "Lydfilen er ikke tilgjengelig fra registrert plassering.",
            status=404,
            content_type="text/plain; charset=utf-8",
        )

    range_header = request.headers.get("Range")
    status_code = 200
    start, end = 0, max(0, size - 1)
    if range_header:
        try:
            start, end = _parse_byte_range(range_header, size)
        except (TypeError, ValueError):
            handle.close()
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{size}"
            response["Accept-Ranges"] = "bytes"
            return response
        status_code = 206
    length = max(0, end - start + 1) if size else 0
    handle.seek(start)
    content_type = (
        "audio/wav" if resolution.source == "selected_master" else "audio/flac"
    )
    if request.method == "HEAD":
        handle.close()
        response = HttpResponse(status=status_code, content_type=content_type)
    else:
        response = StreamingHttpResponse(
            iter_file_range(handle, length=length),
            status=status_code,
            content_type=content_type,
        )
    response["Accept-Ranges"] = "bytes"
    response["Content-Length"] = str(length)
    response["Content-Disposition"] = "inline; filename*=UTF-8''" + quote(
        resolution.asset.filename, safe=""
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    if status_code == 206:
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
    return response


@require_POST
@login_required
@permission_required(
    (
        "music_library.view_musiclibraryentry",
        "flac_ingest.add_flacingestbatch",
        "flac_ingest.apply_flacingestbatch",
    ),
    raise_exception=True,
)
def rescan_library_file(request, entry_id, asset_id):
    if not settings.GUI_V2_WRITES_ENABLED:
        raise PermissionDenied("Ny innlesing er deaktivert i dette oppsettet.")
    entry = get_object_or_404(
        MusicLibraryEntry.objects.select_related("recording"), pk=entry_id
    )
    asset = get_object_or_404(
        FileAsset,
        pk=asset_id,
        recording=entry.recording,
        role=FileAsset.Role.RADIO_FLAC,
    )
    location = get_object_or_404(
        asset.locations,
        is_current=True,
        storage_type=FileLocation.StorageType.NAS,
    )
    return_url = _safe_return(request, _entry_url(request, entry.pk))
    before_radio = {
        "radiosjanger": entry.genre,
        "radiospråk": entry.language,
        "Energy": entry.energy,
        "vokalklassifisering": entry.gender,
        "rotasjonsvurdering": entry.rotation_suitability,
        "kanaler": tuple(
            entry.channels.values_list("name", flat=True).order_by("name")
        ),
        "målgrupper": tuple(
            entry.target_audiences.values_list("name", flat=True).order_by(
                "name"
            )
        ),
    }
    before_catalogue = (entry.recording.title, entry.recording.duration_ms)
    try:
        # This explicit re-read follows an already registered FileAsset. P7UUID may
        # therefore recover that known link; the ingest service still rejects UUID/
        # ISRC conflicts instead of silently attaching another Recording.
        batch = scan_directory(
            relative_root=".",
            recursive=False,
            relative_paths=[location.relative_path],
            allow_uuid_recovery=True,
            force_read=True,
            user=request.user,
        )
        item = batch.items.first()
        if not item:
            messages.error(request, "Filen kunne ikke leses – prøv igjen.")
        elif item.can_apply:
            apply_batch(batch, user=request.user)
            entry.refresh_from_db()
            entry.recording.refresh_from_db()
            after_radio = {
                "radiosjanger": entry.genre,
                "radiospråk": entry.language,
                "Energy": entry.energy,
                "vokalklassifisering": entry.gender,
                "rotasjonsvurdering": entry.rotation_suitability,
                "kanaler": tuple(
                    entry.channels.values_list("name", flat=True).order_by(
                        "name"
                    )
                ),
                "målgrupper": tuple(
                    entry.target_audiences.values_list(
                        "name", flat=True
                    ).order_by("name")
                ),
            }
            changed = [
                label
                for label, value in after_radio.items()
                if value != before_radio[label]
            ]
            catalogue_changed = before_catalogue != (
                entry.recording.title,
                entry.recording.duration_ms,
            )
            if changed:
                text = f"Radiometadata oppdatert: {', '.join(changed)}."
                if catalogue_changed:
                    text += " Katalogmetadata ble også oppdatert fra filen."
                messages.success(request, text)
            elif catalogue_changed:
                messages.success(
                    request,
                    "Katalogmetadata ble oppdatert fra filen. Ingen radiometadata ble endret.",
                )
            else:
                messages.info(
                    request,
                    "Filmetadata lest inn på nytt. Ingen katalogverdier ble endret.",
                )
        else:
            explanation = "; ".join(
                str(value) for value in (item.messages or [])
            )
            messages.error(
                request,
                explanation or "Filen krever kontroll og ble ikke brukt.",
            )
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
        raise PermissionDenied("Utskilling er deaktivert i dette oppsettet.")
    entry = get_object_or_404(
        MusicLibraryEntry.objects.select_related("recording"), pk=entry_id
    )
    asset = get_object_or_404(
        FileAsset,
        pk=asset_id,
        recording=entry.recording,
        role=FileAsset.Role.RADIO_FLAC,
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
                recording, old_recording, remaining_assets = (
                    split_radio_file_to_new_recording(
                        asset_id=asset.pk, user=request.user
                    )
                )
            except (OSError, ValidationError) as error:
                messages.error(request, str(error))
            else:
                # Restore the former Recording from its one remaining authoritative
                # FLAC. Several remaining files require explicit human review.
                refreshed = False
                if len(remaining_assets) == 1:
                    remaining_location = (
                        remaining_assets[0]
                        .locations.filter(
                            is_current=True,
                            storage_type=FileLocation.StorageType.NAS,
                            status=FileLocation.Status.ACTIVE,
                        )
                        .first()
                    )
                    if remaining_location:
                        batch = scan_directory(
                            relative_root=".",
                            recursive=False,
                            relative_paths=[remaining_location.relative_path],
                            force_read=True,
                            user=request.user,
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
                return redirect(
                    _selected_entry_return(return_url, new_entry.pk)
                )
    return render(
        request,
        "gui_v2/split_library_file.html",
        {
            "section": "music_library",
            "entry": entry,
            "asset": asset,
            "preview": preview,
            "return_url": return_url,
            "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
        },
    )


@require_http_methods(["GET", "POST"])
@login_required
@permission_required("catalogue.view_release", raise_exception=True)
def release_list(request):
    create_form = ReleaseMetadataForm(request.POST or None, prefix="release")
    if request.method == "POST":
        if not settings.GUI_V2_WRITES_ENABLED:
            raise PermissionDenied(
                "GUI v2 er skrivebeskyttet i dette oppsettet."
            )
        if not request.user.has_perm("catalogue.add_release"):
            raise PermissionDenied
        if (
            request.POST.get("release-barcode") or ""
        ).strip() and not request.user.has_perm(
            "catalogue.add_externalidentifier"
        ):
            raise PermissionDenied
        if create_form.is_valid():
            with transaction.atomic():
                release = create_form.save()
                create_form.save_barcode()
            messages.success(
                request, "Utgivelsen er opprettet. Du kan nå registrere spor."
            )
            return redirect("gui_v2:release_detail", release_id=release.pk)
    releases = Release.objects.select_related("label").annotate(
        track_count=Count("tracks")
    )
    q = request.GET.get("q", "").strip()
    if q:
        releases = releases.filter(
            Q(title__icontains=q)
            | Q(catalogue_number__icontains=q)
            | Q(label__name__icontains=q)
        )
    page = Paginator(releases.order_by("title", "id"), 40).get_page(
        request.GET.get("page")
    )
    return render(
        request,
        "gui_v2/release_list.html",
        {
            "section": "releases",
            "page": page,
            "q": q,
            "create_form": create_form,
            "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
        },
    )


def _track_initial(track):
    contributions = list(track.recording.contributions.all())

    def credits(role):
        return "; ".join(
            item.display_credit for item in contributions if item.role == role
        )

    isrc = next(
        (
            item.normalized_value
            for item in track.recording.identifiers.all()
            if item.scheme == ExternalIdentifier.Scheme.ISRC
        ),
        "",
    )
    return {
        "track_id": track.pk,
        "recording_id": track.recording_id,
        "sequence_number": track.sequence_number,
        "disc_number": track.disc_number,
        "side": track.side,
        "track_number": track.track_number,
        "title_override": track.title_override,
        "recording_title": track.recording.title,
        "artists": credits(RecordingContribution.Role.PRIMARY),
        "primary_artist_identity_name": next(
            (
                item.artist_identity.display_name
                for item in contributions
                if item.role == RecordingContribution.Role.PRIMARY
                and item.artist_identity_id
            ),
            "",
        ),
        "composers": credits(RecordingContribution.Role.COMPOSER),
        "lyricists": credits(RecordingContribution.Role.LYRICIST),
        "arrangers": credits(RecordingContribution.Role.ARRANGER),
        "duration": _duration(
            track.duration_ms or track.recording.duration_ms
        ),
        "isrc": isrc,
    }


def _require_track_write_permissions(user, rows):
    required = {
        "catalogue.change_release",
        "catalogue.add_releasetrack",
        "catalogue.change_releasetrack",
    }
    retained = [row for row in rows if not row.get("remove")]
    if any(not row.get("recording_id") for row in retained):
        required.add("catalogue.add_recording")
    if any(row.get("remove") for row in rows):
        required.add("catalogue.delete_releasetrack")
    if any(
        row.get("update_shared_recording") or not row.get("recording_id")
        for row in retained
    ):
        required.update(
            {
                "catalogue.add_recordingcontribution",
                "catalogue.add_externalidentifier",
            }
        )
    if any(
        row.get("recording_id") and row.get("update_shared_recording")
        for row in retained
    ):
        required.update(
            {
                "catalogue.change_recording",
                "catalogue.delete_recordingcontribution",
                "catalogue.change_externalidentifier",
                "catalogue.delete_externalidentifier",
            }
        )
    if not user.has_perms(required):
        raise PermissionDenied


@require_http_methods(["GET", "POST"])
@login_required
@permission_required("catalogue.view_release", raise_exception=True)
def release_detail(request, release_id):
    if request.method == "POST" and request.POST.get("action") == "rights":
        raise PermissionDenied(
            "Bruk registreringsdialogen i Rettigheter-fanen."
        )
    if request.method == "GET" and request.GET.get("tab") == "rights":
        from gui_v2.release_rights_views import overview

        return overview(request, release_id)
    release = get_object_or_404(
        Release.objects.select_related("label"), pk=release_id
    )
    if request.method == "GET":
        remember_object(request, "release", release.pk)
    action = (
        request.POST.get("action", "tracks")
        if request.method == "POST"
        else ""
    )
    active_tab = request.POST.get("tab") or request.GET.get("tab", "tracks")
    if active_tab not in {"tracks", "details", "files", "rights"}:
        active_tab = "tracks"
    return_url = _safe_return(request, reverse("gui_v2:release_list"))
    tracks = list(
        release.tracks.select_related(
            "recording__media_selection__selected_master",
            "recording__media_selection__current_radio",
        )
        .prefetch_related(
            "recording__contributions__artist_identity",
            "recording__contributions__party",
            "recording__identifiers",
            "recording__file_assets__locations",
            "recording__media_selection__selected_master__locations",
            "recording__media_selection__current_radio__locations",
            "file_assets",
        )
        .order_by("sequence_number")
    )
    recordings_by_id = {
        str(track.recording_id): track.recording for track in tracks
    }
    for track in tracks:
        track.playback = _playback_context(track.recording, request.user)
    initial = [_track_initial(track) for track in tracks]
    formset = TrackRowFormSet(
        request.POST if action == "tracks" else None,
        initial=None if action == "tracks" else initial,
        form_kwargs={"release": release},
        prefix="tracks",
    )
    track_has_files = {
        str(track.pk): bool(track.file_assets.all()) for track in tracks
    }
    for row_form in formset.forms:
        row_form.has_files = track_has_files.get(
            str(row_form["track_id"].value() or ""), False
        )
        recording = recordings_by_id.get(
            str(row_form["recording_id"].value() or "")
        )
        row_form.playback = (
            _playback_context(recording, request.user)
            if recording
            else {
                "status": RadioPlaybackStatus.NO_RADIO_FILE.value,
                "message": "Sporet er ikke koblet til en innspilling med radiofil.",
            }
        )
    release_form = ReleaseMetadataForm(
        request.POST if action == "release" else None,
        instance=release,
        prefix="release",
    )
    managed_release = ManagedRelease.objects.filter(release=release).first()
    can_view_release_management = request.user.has_perm(
        "managed_music.view_managedrelease"
    )
    managed_permission = (
        "managed_music.change_managedrelease"
        if managed_release
        else "managed_music.add_managedrelease"
    )
    can_edit_release_management = request.user.has_perm(managed_permission)
    managed_release_form = ManagedReleaseForm(
        request.POST if action == "managed_release" else None,
        instance=managed_release,
        prefix="managed-release",
    )
    if request.method == "POST" and action == "release":
        if not settings.GUI_V2_WRITES_ENABLED:
            raise PermissionDenied(
                "GUI v2 er skrivebeskyttet i dette oppsettet."
            )
        if not request.user.has_perm("catalogue.change_release"):
            raise PermissionDenied
        if release_form.is_valid():
            with transaction.atomic():
                release_form.save()
                release_form.save_barcode()
            messages.success(request, "Utgivelsesopplysningene er lagret.")
            return_suffix = (
                f"&return={quote(return_url, safe='')}"
                if request.GET.get("return")
                else ""
            )
            return redirect(
                f"{reverse('gui_v2:release_detail', args=[release.pk])}"
                f"?tab=details{return_suffix}"
            )
    elif request.method == "POST" and action == "managed_release":
        if not settings.GUI_V2_WRITES_ENABLED:
            raise PermissionDenied(
                "GUI v2 er skrivebeskyttet i dette oppsettet."
            )
        if not can_edit_release_management:
            raise PermissionDenied
        if managed_release_form.is_valid():
            save_managed_release(
                release=release, **managed_release_form.cleaned_data
            )
            messages.success(
                request,
                "Utgivelsens katalogforvaltning er lagret uten å endre "
                "rettigheter for innspillingene.",
            )
            return redirect(
                f"{reverse('gui_v2:release_detail', args=[release.pk])}?tab=details"
            )
    elif request.method == "POST" and action == "tracks":
        if not settings.GUI_V2_WRITES_ENABLED:
            raise PermissionDenied(
                "GUI v2 er skrivebeskyttet i dette oppsettet."
            )
        if formset.is_valid():
            rows = [form.cleaned_data for form in formset.forms]
            _require_track_write_permissions(request.user, rows)
            try:
                save_release_track_rows(release=release, rows=rows)
            except (ValidationError, ValueError) as error:
                values = (
                    error.messages
                    if hasattr(error, "messages")
                    else [str(error)]
                )
                formset._non_form_errors = formset.error_class(values)
            else:
                messages.success(request, "Sporlisten er lagret samlet.")
                query = request.POST.get("return_query", "")
                target = reverse("gui_v2:release_detail", args=[release.pk])
                query = f"{query}&grid_saved=1" if query else "grid_saved=1"
                return redirect(f"{target}?{query}")
    selected_track_id = request.GET.get("track")
    selected_track = next(
        (item for item in tracks if str(item.pk) == selected_track_id),
        tracks[0] if tracks else None,
    )
    selected_track_data = (
        _track_initial(selected_track) if selected_track else None
    )
    release_barcode = release.identifiers.filter(
        scheme__in=(
            ExternalIdentifier.Scheme.UPC,
            ExternalIdentifier.Scheme.EAN,
            ExternalIdentifier.Scheme.GTIN,
        )
    ).first()
    release_artists = []
    for track_data in initial:
        artists = track_data["artists"]
        if artists:
            release_artists.extend(
                part.strip() for part in artists.split(";") if part.strip()
            )
    release_artist_text = (
        ", ".join(dict.fromkeys(release_artists)) or "Uavklart artist"
    )
    release_cover = None
    if request.user.is_staff and request.user.has_perms(
        (
            "media_assets.view_fileasset",
            "media_assets.view_filelocation",
            "music_library.view_musiclibraryentry",
        )
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
    release_files = (
        list(
            release.file_assets.prefetch_related("locations").order_by(
                "role", "filename"
            )
        )
        if request.user.has_perm("media_assets.view_fileasset")
        else []
    )
    release_sources = (
        list(
            MetadataAssertion.objects.filter(
                entity_type=MetadataAssertion.EntityType.RELEASE,
                entity_uuid=release.pk,
            ).select_related("source_record__source_system")
        )
        if request.user.has_perm("provenance.view_metadataassertion")
        else []
    )
    return render(
        request,
        "gui_v2/release_tracks.html",
        {
            "section": "releases",
            "release": release,
            "tracks": tracks,
            "formset": formset,
            "selected_track": selected_track,
            "selected_track_data": selected_track_data,
            "release_form": release_form,
            "managed_release": managed_release,
            "managed_release_form": managed_release_form,
            "can_view_release_management": can_view_release_management,
            "can_edit_release_management": can_edit_release_management,
            "release_barcode": release_barcode,
            "release_artist_text": release_artist_text,
            "release_cover": release_cover,
            "active_tab": active_tab,
            "release_files": release_files,
            "release_sources": release_sources,
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
    if not q or (len(q) < 2 and request.GET.get("match") != "exact"):
        return JsonResponse({"results": []})
    if request.GET.get("match") == "exact":
        isrc = re.sub(r"[\s-]", "", request.GET.get("isrc", ""))
        if not re.fullmatch(r"[A-Za-z]{2}[A-Za-z0-9]{3}\d{7}", isrc):
            isrc = ""
        duration = request.GET.get("duration", "")
        parts = duration.split(":")
        duration_ms = None
        if len(parts) == 2 and all(part.isdigit() for part in parts):
            minutes, seconds = (int(part) for part in parts)
            if seconds < 60:
                duration_ms = (minutes * 60 + seconds) * 1000
        matches = find_recording_candidates(
            title=q, isrc=isrc, duration_ms=duration_ms
        )[:12]
        prefetch_related_objects(
            [match.recording for match in matches],
            "contributions__artist_identity",
            "contributions__party",
        )
        return JsonResponse(
            {
                "results": [
                    {
                        "id": str(match.recording.pk),
                        "title": match.recording.title,
                        "artist": _artist_text(match.recording),
                        "signals": match.signals,
                        "blocking": "samme ISRC" in match.signals,
                        "url": reverse(
                            "gui_v2:recording_detail",
                            args=[match.recording.pk],
                        ),
                    }
                    for match in matches
                ]
            }
        )
    queryset = (
        Recording.objects.filter(
            Q(title__icontains=q)
            | Q(identifiers__normalized_value__icontains=q)
            | Q(contributions__credited_as__icontains=q)
        )
        .prefetch_related("contributions", "identifiers")
        .distinct()[:12]
    )
    return JsonResponse(
        {
            "results": [
                {
                    "id": str(item.pk),
                    "title": item.title,
                    "artist": _artist_text(item),
                    "isrc": next(
                        (
                            identifier.normalized_value
                            for identifier in item.identifiers.all()
                            if identifier.scheme
                            == ExternalIdentifier.Scheme.ISRC
                        ),
                        "",
                    ),
                }
                for item in queryset
            ]
        }
    )


@require_GET
@login_required
@permission_required("catalogue.view_release", raise_exception=True)
def legacy_release_tracks(request):
    release = Release.objects.order_by("title").first()
    return (
        redirect("gui_v2:release_detail", release_id=release.pk)
        if release
        else release_list(request)
    )
