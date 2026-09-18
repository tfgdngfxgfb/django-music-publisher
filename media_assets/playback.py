"""Conservative resolution and streaming helpers for internal radio playback."""

from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured, ValidationError

from .models import FileAsset, FileLocation
from .storage import (
    ResolvedLocation,
    StorageFileUnavailable,
    resolve_location,
    validate_readable_location,
)

from django.core.exceptions import ObjectDoesNotExist


class RadioPlaybackStatus(StrEnum):
    AVAILABLE = "available"
    NO_RADIO_FILE = "no_radio_file"
    AMBIGUOUS = "ambiguous"
    FILE_UNAVAILABLE = "file_unavailable"


@dataclass(frozen=True)
class RadioPlaybackResolution:
    status: RadioPlaybackStatus
    asset: FileAsset | None = None
    location: FileLocation | None = None
    path: Path | None = None
    resolved_location: ResolvedLocation | None = None
    source: str = "current_radio"

    @property
    def message(self):
        if self.source == "selected_master":
            return {
                RadioPlaybackStatus.AVAILABLE: "Valgt master er klar for avspilling.",
                RadioPlaybackStatus.AMBIGUOUS: "Valgt master har flere aktive plasseringer. Kontroller Filer.",
            }.get(
                self.status,
                "Valgt master er ikke tilgjengelig fra registrert plassering.",
            )
        return {
            RadioPlaybackStatus.AVAILABLE: "Radiofilen er klar for avspilling.",
            RadioPlaybackStatus.NO_RADIO_FILE: "Ingen radiofil er registrert.",
            RadioPlaybackStatus.AMBIGUOUS: (
                "Flere mulige radiofiler er registrert. Kontroller Filer."
            ),
            RadioPlaybackStatus.FILE_UNAVAILABLE: (
                "Radiofilen er ikke tilgjengelig fra registrert plassering."
            ),
        }[self.status]


def resolve_current_radio_asset(recording, *, verify_file=False):
    """Resolve one active radio-FLAC without choosing arbitrarily.

    Historical/inactive locations do not compete with an active location. More
    than one active asset or more than one active location is ambiguous.
    """
    explicit_asset = None
    try:
        selection = recording.media_selection
    except ObjectDoesNotExist:
        selection = None
    if selection and selection.current_radio_id:
        explicit_asset = selection.current_radio

    radio_assets = [
        asset
        for asset in recording.file_assets.all()
        if asset.role == FileAsset.Role.RADIO_FLAC
        and asset.lifecycle_status
        not in {
            FileAsset.LifecycleStatus.CANDIDATE,
            FileAsset.LifecycleStatus.HISTORICAL,
        }
    ]
    if explicit_asset:
        radio_assets = [explicit_asset]
    if not radio_assets:
        return RadioPlaybackResolution(RadioPlaybackStatus.NO_RADIO_FILE)

    return _resolve_assets(radio_assets, verify_file=verify_file)


def resolve_recording_playback(recording, *, verify_file=False):
    """Normal listening prefers the explicitly selected master.

    An unavailable selection is reported, never silently replaced by different
    audio. Explicit radio operations continue to use resolve_current_radio_asset.
    """
    try:
        selection = recording.media_selection
    except ObjectDoesNotExist:
        selection = None
    if not selection or not selection.selected_master_id:
        return resolve_current_radio_asset(recording, verify_file=verify_file)
    asset = selection.selected_master
    if (
        asset.recording_id != recording.pk
        or asset.role != FileAsset.Role.EDITED_WAV_MASTER
    ):
        result = RadioPlaybackResolution(RadioPlaybackStatus.FILE_UNAVAILABLE)
    else:
        result = _resolve_assets([asset], verify_file=verify_file)
    return replace(result, source="selected_master")


def _resolve_assets(assets, *, verify_file):

    candidates = []
    location_ambiguity = False
    supported_storage = {
        FileLocation.StorageType.NAS,
        FileLocation.StorageType.LOCAL,
    }
    for asset in assets:
        if asset.sync_status in {
            FileAsset.SyncStatus.MISSING,
            FileAsset.SyncStatus.FAILED,
        }:
            continue
        locations = [
            location
            for location in asset.locations.all()
            if location.is_current
            and location.status == FileLocation.Status.ACTIVE
            and location.storage_type in supported_storage
        ]
        if len(locations) == 1:
            candidates.append((asset, locations[0]))
        elif len(locations) > 1:
            location_ambiguity = True

    if location_ambiguity or len(candidates) > 1:
        return RadioPlaybackResolution(RadioPlaybackStatus.AMBIGUOUS)
    if not candidates:
        return RadioPlaybackResolution(RadioPlaybackStatus.FILE_UNAVAILABLE)

    asset, location = candidates[0]
    path = None
    resolved_location = None
    if verify_file:
        try:
            resolved_location = validate_readable_location(
                resolve_location(location, require_root=True)
            )
            path = resolved_location.server_path
        except (
            ImproperlyConfigured,
            ValidationError,
            StorageFileUnavailable,
            OSError,
        ):
            return RadioPlaybackResolution(
                RadioPlaybackStatus.FILE_UNAVAILABLE,
                asset=asset,
                location=location,
            )
    return RadioPlaybackResolution(
        RadioPlaybackStatus.AVAILABLE,
        asset=asset,
        location=location,
        path=path,
        resolved_location=resolved_location,
    )


def iter_file_range(handle, *, length, chunk_size=64 * 1024):
    """Yield exactly ``length`` bytes and close the already-open file handle."""
    remaining = length
    try:
        while remaining > 0:
            data = handle.read(min(chunk_size, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data
    finally:
        handle.close()
