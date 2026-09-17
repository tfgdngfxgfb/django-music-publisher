"""Presentation data for the GUI v2 file workspace of one Recording."""

from pathlib import PurePosixPath

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db.models import Q

from media_assets.models import FileAsset, FileLocation
from media_assets.playback import (
    RadioPlaybackStatus,
    resolve_current_radio_asset,
)
from media_assets.pipeline_status import get_recording_media_pipeline_status
from media_assets.storage import (
    get_client_folder,
    get_client_path,
    root_key_for_location,
)

from django.core.exceptions import ObjectDoesNotExist
from media_assets.models import (
    MediaAssetEvent,
    RadioFlacGeneration,
    DigitizationDerivation,
)


def _duration(value):
    if value is None:
        return ""
    seconds = round(value / 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _size(value):
    if value is None:
        return ""
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            decimals = 0 if unit in {"B", "KB"} else 1
            return f"{amount:.{decimals}f}".replace(".", ",") + f" {unit}"
        amount /= 1024
    return ""


def _format(asset):
    suffix = PurePosixPath(asset.filename).suffix.removeprefix(".").upper()
    if suffix:
        return suffix
    if asset.mime_type:
        return asset.mime_type.rsplit("/", 1)[-1].upper()
    return ""


def _technical(asset):
    technical = asset.technical_metadata or {}
    sample_rate = technical.get("sample_rate")
    channels = technical.get("channels")
    channel_text = str(channels) if channels else ""
    if channels == 1:
        channel_text += " (mono)"
    elif channels == 2:
        channel_text += " (stereo)"
    return {
        "format": _format(asset),
        "sample_rate": f"{sample_rate / 1000:g} kHz" if sample_rate else "",
        "bit_depth": (
            f"{technical.get('bits_per_sample')} bit"
            if technical.get("bits_per_sample")
            else ""
        ),
        "channels": channel_text,
        "duration": _duration(technical.get("duration_ms")),
        "audio_md5": technical.get("audio_md5", ""),
    }


def _location_data(location):
    client_path = None
    client_folder = None
    root_key = ""
    try:
        root_key = root_key_for_location(location)
        client_path = get_client_path(location)
        client_folder = get_client_folder(location)
    except (ImproperlyConfigured, ValidationError, OSError):
        pass
    return {
        "object": location,
        "storage": location.get_storage_type_display(),
        "root_key": root_key,
        "client_path": client_path,
        "client_folder": client_folder,
        "display_path": client_path or location.relative_path,
        "path_kind": "Klientsti" if client_path else "Logisk sti",
        "status": location.get_status_display(),
        "status_kind": (
            "ok"
            if location.status == FileLocation.Status.ACTIVE
            and location.is_current
            else (
                "error"
                if location.status == FileLocation.Status.MISSING
                else "neutral"
            )
        ),
    }


def _asset_data(asset, current_asset_id, selected_master_id):
    locations = [_location_data(item) for item in asset.locations.all()]
    current_locations = [
        item for item in locations if item["object"].is_current
    ]
    active_locations = [
        item
        for item in current_locations
        if item["object"].status == FileLocation.Status.ACTIVE
    ]
    active_client_locations = [
        item for item in active_locations if item["client_path"]
    ]
    technical = _technical(asset)
    is_current_radio = asset.pk == current_asset_id
    is_selected_master = asset.pk == selected_master_id
    if is_current_radio:
        status, status_kind = "Gjeldende", "ok"
    elif is_selected_master:
        status, status_kind = "Valgt master", "ok"
    elif asset.lifecycle_status == FileAsset.LifecycleStatus.CANDIDATE:
        status, status_kind = "Verifisert kandidat", "warning"
    elif asset.lifecycle_status == FileAsset.LifecycleStatus.HISTORICAL:
        status, status_kind = "Historisk", "neutral"
    elif asset.sync_status in {
        FileAsset.SyncStatus.MISSING,
        FileAsset.SyncStatus.FAILED,
    } or any(
        item["object"].status == FileLocation.Status.MISSING
        for item in current_locations
    ):
        status, status_kind = asset.get_sync_status_display(), "error"
    elif active_locations:
        status, status_kind = "Aktiv", "neutral"
    elif any(
        item["object"].status
        in {
            FileLocation.Status.HISTORICAL,
            FileLocation.Status.MOVED,
        }
        for item in locations
    ):
        status, status_kind = "Historisk", "neutral"
    else:
        status, status_kind = asset.get_sync_status_display(), "neutral"

    observed = [
        item["object"].observed_at
        for item in locations
        if item["object"].observed_at
    ]
    if asset.metadata_read_at:
        observed.append(asset.metadata_read_at)
    last_seen = max(observed, default=None)
    summary = " / ".join(
        value
        for value in (
            technical["bit_depth"],
            technical["sample_rate"],
            technical["channels"],
            technical["duration"],
        )
        if value
    )
    return {
        "object": asset,
        "technical": technical,
        "technical_summary": summary,
        "size": _size(asset.size_bytes),
        "status": status,
        "status_kind": status_kind,
        "is_current_radio": is_current_radio,
        "is_selected_master": is_selected_master,
        "is_candidate": asset.lifecycle_status
        == FileAsset.LifecycleStatus.CANDIDATE,
        "locations": locations,
        "current_locations": current_locations,
        "last_seen": last_seen,
        "has_client_path": any(item["client_path"] for item in locations),
        "primary_client_location": (
            active_client_locations[0]
            if len(active_client_locations) == 1
            else None
        ),
        "client_location_ambiguous": len(active_client_locations) > 1,
        "can_rescan": any(
            item["object"].is_current
            and item["object"].storage_type == FileLocation.StorageType.NAS
            for item in locations
        ),
        "latest_checksum": next(iter(asset.checksum_history.all()), None),
    }


def _history(file_data, lifecycle_events=()):
    asset = file_data["object"]
    events = [
        {"when": asset.created_at, "text": "Filressurs registrert"},
    ]
    if asset.metadata_read_at:
        events.append(
            {"when": asset.metadata_read_at, "text": "Filmetadata lest"}
        )
    for location in asset.locations.all():
        events.append(
            {
                "when": location.observed_at,
                "text": f"Plassering registrert · {location.get_storage_type_display()}",
            }
        )
    for checksum in asset.checksum_history.all():
        events.append(
            {
                "when": checksum.observed_at,
                "text": f"Kontrollsum registrert · {checksum.get_reason_display()}",
            }
        )
    for event in lifecycle_events:
        events.append(
            {"when": event.created_at, "text": event.get_event_type_display()}
        )
    return sorted(events, key=lambda item: item["when"], reverse=True)[:5]


def build_recording_files(recording, *, selected_asset_id=None):
    """Build one read-only file inventory using the playback resolver as authority."""
    resolution = resolve_current_radio_asset(recording)
    current_asset_id = resolution.asset.pk if resolution.asset else None
    try:
        selection = recording.media_selection
    except ObjectDoesNotExist:
        selection = None
    selected_master_id = selection.selected_master_id if selection else None
    files = [
        _asset_data(asset, current_asset_id, selected_master_id)
        for asset in recording.file_assets.all()
    ]
    generation_rows = list(
        RadioFlacGeneration.objects.filter(recording=recording)
        .select_related("master_asset", "candidate_asset")
        .order_by("-created_at", "id")
    )
    generations = {
        item.candidate_asset_id: item
        for item in generation_rows
        if item.candidate_asset_id
    }
    events = {}
    timeline = list(
        MediaAssetEvent.objects.filter(
            Q(recording=recording)
            | Q(asset_id__in=[file["object"].pk for file in files])
            | Q(related_asset_id__in=[file["object"].pk for file in files])
        ).select_related(
            "actor", "asset", "related_asset", "digitization_batch"
        )[
            :50
        ]
    )
    for event in timeline:
        events.setdefault(event.asset_id, []).append(event)
    role_order = {
        FileAsset.Role.RADIO_FLAC: 0,
        FileAsset.Role.EDITED_WAV_MASTER: 1,
        FileAsset.Role.RAW_DIGITIZATION: 2,
        FileAsset.Role.DISTRIBUTION: 3,
        FileAsset.Role.COVER_IMAGE: 4,
        FileAsset.Role.DOCUMENT: 5,
        FileAsset.Role.OTHER: 6,
    }
    files.sort(
        key=lambda item: (
            not item["is_current_radio"],
            role_order.get(item["object"].role, 99),
            item["object"].filename.casefold(),
            str(item["object"].pk),
        )
    )
    for item in files:
        item["generation"] = generations.get(item["object"].pk)
        item["history"] = _history(item, events.get(item["object"].pk, ()))
    lineage = {}
    master_ids = {file["object"].pk for file in files}
    master_ids.update(
        generation.master_asset_id for generation in generations.values()
    )
    for relation in (
        DigitizationDerivation.objects.filter(derived_asset_id__in=master_ids)
        .select_related(
            "source_asset", "derived_asset__digitization_file__batch__release"
        )
        .order_by("-created_at")
    ):
        lineage.setdefault(relation.derived_asset_id, []).append(relation)
    for item in files:
        generation = item["generation"]
        item["digitization_lineage"] = lineage.get(
            generation.master_asset_id if generation else item["object"].pk, []
        )
    selected = next(
        (
            item
            for item in files
            if selected_asset_id
            and str(item["object"].pk) == str(selected_asset_id)
        ),
        None,
    )
    if selected is None:
        selected = next(
            (item for item in files if item["is_current_radio"]), None
        )
    if selected is None and files:
        selected = files[0]

    radio_count = sum(
        item["object"].role == FileAsset.Role.RADIO_FLAC for item in files
    )
    if resolution.status == RadioPlaybackStatus.AVAILABLE:
        radio_status = {
            "kind": "ok",
            "title": "Gjeldende radiofil",
            "text": "Denne filen brukes som radiofil for innspillingen.",
        }
    elif resolution.status == RadioPlaybackStatus.AMBIGUOUS:
        radio_status = {
            "kind": "warning",
            "title": "Ingen entydig gjeldende radiofil",
            "text": f"{radio_count} aktive radiofiler er registrert. Kontroller filene nedenfor.",
        }
    elif resolution.status == RadioPlaybackStatus.FILE_UNAVAILABLE:
        radio_status = {
            "kind": "error",
            "title": "Radiofil sist registrert utilgjengelig",
            "text": "Filreferansen er bevart, men ingen lesbar aktiv plassering er registrert.",
        }
    else:
        radio_status = {
            "kind": "neutral",
            "title": "Ingen radiofil registrert",
            "text": "Denne innspillingen har ingen registrert radio-FLAC.",
        }
    current = next((item for item in files if item["is_current_radio"]), None)
    return {
        "files": files,
        "selected": selected,
        "radio_status": radio_status,
        "current_radio": current,
        "radio_count": radio_count,
        "resolution": resolution,
        "selection": selection,
        "pipeline_status": get_recording_media_pipeline_status(
            recording,
            selection=selection,
            generations=generation_rows,
        ),
        "master_versions": [
            item
            for item in files
            if item["object"].role == FileAsset.Role.EDITED_WAV_MASTER
        ],
        "radio_versions": [
            item
            for item in files
            if item["object"].role == FileAsset.Role.RADIO_FLAC
        ],
        "timeline": timeline[:20],
    }
