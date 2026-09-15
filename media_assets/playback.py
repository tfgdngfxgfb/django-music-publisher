"""Conservative resolution and streaming helpers for internal radio playback."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured, ValidationError

from .models import FileAsset, FileLocation


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

    @property
    def message(self):
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


def _resolved_path(location):
    # Ingest owns the configured-root policy. Importing here avoids duplicating it
    # while keeping media_assets.models independent of the ingest application.
    from flac_ingest.services import resolve_music_path

    return resolve_music_path(location.relative_path)[1]


def resolve_current_radio_asset(recording, *, verify_file=False):
    """Resolve one active radio-FLAC without choosing arbitrarily.

    Historical/inactive locations do not compete with an active location. More
    than one active asset or more than one active location is ambiguous.
    """
    radio_assets = [
        asset
        for asset in recording.file_assets.all()
        if asset.role == FileAsset.Role.RADIO_FLAC
    ]
    if not radio_assets:
        return RadioPlaybackResolution(RadioPlaybackStatus.NO_RADIO_FILE)

    candidates = []
    location_ambiguity = False
    supported_storage = {FileLocation.StorageType.NAS, FileLocation.StorageType.LOCAL}
    for asset in radio_assets:
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
    if verify_file:
        try:
            path = _resolved_path(location)
        except (ImproperlyConfigured, ValidationError, OSError):
            return RadioPlaybackResolution(
                RadioPlaybackStatus.FILE_UNAVAILABLE, asset=asset, location=location
            )
        if not path.is_file():
            return RadioPlaybackResolution(
                RadioPlaybackStatus.FILE_UNAVAILABLE,
                asset=asset,
                location=location,
                path=path,
            )
    return RadioPlaybackResolution(
        RadioPlaybackStatus.AVAILABLE,
        asset=asset,
        location=location,
        path=path,
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
