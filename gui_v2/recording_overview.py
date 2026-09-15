"""Read-only presentation data for the GUI v2 Recording overview."""

from django.db.models import Prefetch

from catalogue.models import (
    DuplicateCandidate,
    ExternalIdentifier,
    Recording,
    RecordingContribution,
    ReleaseTrack,
)
from flac_ingest.models import FlacIngestItem
from media_assets.models import FileAsset, FileLocation

from .presentation import radio_language_name


def _duration(value):
    if value is None:
        return ""
    seconds = round(value / 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def recording_release_tracks_with_covers_queryset():
    """Return release occurrences in the shared deterministic cover order."""
    return (
        ReleaseTrack.objects.select_related("release__label")
        .prefetch_related(
            Prefetch(
                "release__file_assets",
                queryset=FileAsset.objects.filter(
                    role=FileAsset.Role.COVER_IMAGE
                )
                .prefetch_related("locations")
                .order_by("filename", "id"),
            )
        )
        .order_by("release__release_year", "release__title", "sequence_number")
    )


def select_recording_cover(releases):
    """Select the first usable release cover without relying on database order."""
    cover = None
    for track in releases:
        track.cover_asset = None
        for asset in track.release.file_assets.all():
            if any(
                location.is_current
                and location.status == FileLocation.Status.ACTIVE
                and location.storage_type == FileLocation.StorageType.NAS
                for location in asset.locations.all()
            ):
                track.cover_asset = asset
                if cover is None:
                    cover = {"asset": asset, "release": track.release}
                break
    return cover


def recording_overview_queryset():
    contributions = RecordingContribution.objects.select_related(
        "party", "artist_identity", "source_record__source_system"
    ).order_by("display_order", "id")
    tracks = recording_release_tracks_with_covers_queryset()
    files = FileAsset.objects.prefetch_related(
        "locations", "checksum_history"
    ).order_by("role", "filename", "id")
    duplicate_cases = DuplicateCandidate.objects.select_related(
        "recording_a", "recording_b"
    )
    return Recording.objects.select_related(
        "music_library_entry__managed_recording",
        "media_selection__selected_master",
        "media_selection__current_radio",
    ).prefetch_related(
        Prefetch("contributions", queryset=contributions),
        "identifiers",
        "music_library_entry__channels",
        "music_library_entry__target_audiences",
        Prefetch("release_tracks", queryset=tracks),
        Prefetch("file_assets", queryset=files),
        Prefetch("duplicate_candidates_as_a", queryset=duplicate_cases),
        Prefetch("duplicate_candidates_as_b", queryset=duplicate_cases),
    )


def _credit_data(contribution):
    unresolved = bool(
        contribution.credited_as
        and not contribution.party_id
        and not contribution.artist_identity_id
    )
    return {
        "name": contribution.display_credit,
        "unresolved": unresolved,
        "role": contribution.get_role_display(),
    }


def _technical_file(asset):
    technical = asset.technical_metadata or {}
    current_locations = [
        location for location in asset.locations.all() if location.is_current
    ]
    active_locations = [
        location
        for location in current_locations
        if location.status == FileLocation.Status.ACTIVE
    ]
    sample_rate = technical.get("sample_rate")
    if sample_rate:
        sample_rate_text = f"{sample_rate / 1000:g} kHz"
    else:
        sample_rate_text = ""
    channel_count = technical.get("channels")
    channel_text = ""
    if channel_count:
        channel_text = str(channel_count)
        if channel_count == 1:
            channel_text += " (mono)"
        elif channel_count == 2:
            channel_text += " (stereo)"
    return {
        "object": asset,
        "filename": asset.filename,
        "format": (
            "FLAC"
            if asset.role == FileAsset.Role.RADIO_FLAC
            else (asset.mime_type or "")
        ),
        "sample_rate": sample_rate_text,
        "bit_depth": (
            f"{technical.get('bits_per_sample')} bit"
            if technical.get("bits_per_sample")
            else ""
        ),
        "channels": channel_text,
        "duration": _duration(technical.get("duration_ms")),
        "locations": current_locations,
        "available": bool(active_locations)
        and asset.sync_status
        not in {
            FileAsset.SyncStatus.MISSING,
            FileAsset.SyncStatus.FAILED,
        },
        "status": (
            "Tilgjengelig"
            if active_locations
            and asset.sync_status
            not in {FileAsset.SyncStatus.MISSING, FileAsset.SyncStatus.FAILED}
            else (
                asset.get_sync_status_display()
                if asset.sync_status
                in {
                    FileAsset.SyncStatus.MISSING,
                    FileAsset.SyncStatus.FAILED,
                    FileAsset.SyncStatus.CONFLICT,
                }
                else "Ikke tilgjengelig"
            )
        ),
        "last_read": asset.metadata_read_at,
    }


def build_recording_overview(recording, *, can_view_files, can_view_releases):
    contributions = list(recording.contributions.all())
    grouped = {}
    for contribution in contributions:
        grouped.setdefault(contribution.role, []).append(
            _credit_data(contribution)
        )

    artist_credits = grouped.get(
        RecordingContribution.Role.PRIMARY, []
    ) + grouped.get(RecordingContribution.Role.FEATURED, [])
    artist_text = (
        ", ".join(dict.fromkeys(item["name"] for item in artist_credits))
        or "Uavklart artist"
    )
    isrc = next(
        (
            identifier.normalized_value
            for identifier in recording.identifiers.all()
            if identifier.scheme == ExternalIdentifier.Scheme.ISRC
        ),
        "",
    )
    entry = getattr(recording, "music_library_entry", None)
    managed = getattr(entry, "managed_recording", None) if entry else None
    releases = (
        list(recording.release_tracks.all()) if can_view_releases else []
    )

    cover = select_recording_cover(releases) if can_view_files else None

    radio_assets = []
    if can_view_files:
        radio_assets = [
            _technical_file(asset)
            for asset in recording.file_assets.all()
            if asset.role == FileAsset.Role.RADIO_FLAC
        ]

    duplicate_cases = list(recording.duplicate_candidates_as_a.all()) + list(
        recording.duplicate_candidates_as_b.all()
    )
    known_isrc_collision = any(
        case.status == DuplicateCandidate.Status.DISMISSED
        and {"reported_isrc_collision", "manual_separate_recordings"}.issubset(
            set(case.signals or [])
        )
        for case in duplicate_cases
    )
    open_duplicate = any(
        case.status == DuplicateCandidate.Status.OPEN
        for case in duplicate_cases
    )
    unresolved_credits = [
        item
        for values in grouped.values()
        for item in values
        if item["unresolved"]
    ]

    follow_up = []
    if can_view_files:
        if not radio_assets:
            follow_up.append(
                {
                    "kind": "warning",
                    "title": "Ingen radiofil registrert",
                    "text": "Innspillingen har ingen koblet radio-FLAC.",
                }
            )
        elif len(radio_assets) > 1:
            follow_up.append(
                {
                    "kind": "warning",
                    "title": f"{len(radio_assets)} radiofiler registrert",
                    "text": "Systemet velger ikke én fil når koblingen er tvetydig.",
                }
            )
        elif not radio_assets[0]["available"]:
            follow_up.append(
                {
                    "kind": "error",
                    "title": "Radiofilen er ikke tilgjengelig",
                    "text": "Innspillingen og katalogdataene er fortsatt bevart.",
                }
            )
    if open_duplicate:
        follow_up.append(
            {
                "kind": "warning",
                "title": "Mulig dublett må vurderes",
                "text": "En åpen katalogsak berører denne innspillingen.",
            }
        )
    ingest_issue = recording.flac_ingest_items.filter(
        action__in=(
            FlacIngestItem.Action.CONFLICT,
            FlacIngestItem.Action.INVALID,
            FlacIngestItem.Action.RETRY,
        ),
        applied_at__isnull=True,
    ).exists()
    if ingest_issue:
        follow_up.append(
            {
                "kind": "warning",
                "title": "Uløst innlesingsavvik",
                "text": "Minst én filinnlesing krever kontroll.",
            }
        )

    years = [
        (
            track.release.release_date.year
            if track.release.release_date
            else track.release.release_year
        )
        for track in releases
        if track.release.release_date or track.release.release_year
    ]
    last_read = max(
        (item["last_read"] for item in radio_assets if item["last_read"]),
        default=None,
    )
    return {
        "artist_text": artist_text,
        "artist_credits": artist_credits,
        "isrc": isrc,
        "duration": _duration(recording.duration_ms),
        "entry": entry,
        "managed": managed,
        "cover": cover,
        "credits": {
            "composers": grouped.get(RecordingContribution.Role.COMPOSER, []),
            "lyricists": grouped.get(RecordingContribution.Role.LYRICIST, []),
            "arrangers": grouped.get(RecordingContribution.Role.ARRANGER, []),
        },
        "unresolved_credits": unresolved_credits,
        "language": radio_language_name(entry.language) if entry else "",
        "channels": list(entry.channels.all()) if entry else [],
        "targets": list(entry.target_audiences.all()) if entry else [],
        "releases": releases,
        "release_count": len(releases),
        "first_release_year": min(years) if years else None,
        "radio_files": radio_assets,
        "single_radio_file": (
            radio_assets[0] if len(radio_assets) == 1 else None
        ),
        "known_isrc_collision": known_isrc_collision,
        "follow_up": follow_up,
        "last_read": last_read,
    }
