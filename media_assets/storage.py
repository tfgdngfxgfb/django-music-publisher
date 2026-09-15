"""Safe, read-only filesystem access for registered media locations.

The database stores portable relative paths.  This module is the boundary that
maps those paths to an installation's server filesystem and, when configured,
to a Windows/client path intended for an internal user's clipboard.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError


MUSIC_LIBRARY_ROOT = "music_library"


class StoragePathError(ValidationError):
    """A logical path is unsafe or incompatible with its configured root."""


class StorageFileUnavailable(OSError):
    """A registered path cannot currently be opened for reading."""


@dataclass(frozen=True)
class StorageRoot:
    key: str
    server_root: Path
    client_root: str | None = None
    backend: str = "filesystem"
    read_only: bool = True


@dataclass(frozen=True)
class ResolvedLocation:
    root: StorageRoot
    logical_path: PurePosixPath
    server_path: Path
    client_path: str | None

    @property
    def client_folder(self):
        if not self.client_path:
            return None
        return str(PureWindowsPath(self.client_path).parent)


def _configured_root_values(root_key):
    configured_roots = getattr(settings, "P7_STORAGE_ROOTS", {}) or {}
    configured = configured_roots.get(root_key)
    if configured is not None:
        if not isinstance(configured, dict):
            raise ImproperlyConfigured(
                f"P7_STORAGE_ROOTS[{root_key!r}] må være en mapping."
            )
        return {
            "server_root": configured.get("server_root", ""),
            "client_root": configured.get("client_root", ""),
            "backend": configured.get("backend", "filesystem"),
            "read_only": configured.get("read_only", True),
        }
    if root_key != MUSIC_LIBRARY_ROOT:
        raise ImproperlyConfigured(f"Ukjent storage-root: {root_key}.")
    return {
        "server_root": (
            getattr(settings, "P7_MUSIC_ROOT", "")
            or getattr(settings, "P7_NAS_ROOT", "")
        ),
        "client_root": getattr(settings, "P7_MUSIC_CLIENT_ROOT", ""),
        "backend": "filesystem",
        "read_only": True,
    }


def get_storage_root(root_key=MUSIC_LIBRARY_ROOT, *, require_directory=False):
    """Return one approved installation root without exposing it to HTTP callers."""
    values = _configured_root_values(root_key)
    configured = str(values["server_root"] or "").strip()
    if not configured:
        raise ImproperlyConfigured(
            f"Serversti for storage-root {root_key!r} er ikke konfigurert."
        )
    if values["backend"] != "filesystem":
        raise ImproperlyConfigured(
            f"Storage-root {root_key!r} bruker en backend som ikke støttes her."
        )
    server_root = Path(configured).expanduser().resolve(strict=False)
    if require_directory and not server_root.is_dir():
        raise ImproperlyConfigured("Den konfigurerte storage-roten finnes ikke.")
    client_root = str(values["client_root"] or "").strip() or None
    if client_root and not PureWindowsPath(client_root).is_absolute():
        raise ImproperlyConfigured(
            f"Client-root for storage-root {root_key!r} må være en absolutt Windows-sti."
        )
    return StorageRoot(
        key=root_key,
        server_root=server_root,
        client_root=client_root,
        backend=values["backend"],
        read_only=bool(values["read_only"]),
    )


def _logical_path(value):
    raw = str(value or ".").strip()
    normalized = raw.replace("\\", "/") or "."
    logical = PurePosixPath(normalized)
    windows = PureWindowsPath(raw)
    if (
        logical.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in logical.parts
    ):
        raise StoragePathError(
            "Bruk en logisk relativ sti innenfor den konfigurerte storage-roten."
        )
    return logical


def resolve_storage_path(
    relative_path=".", *, root_key=MUSIC_LIBRARY_ROOT, require_root=False
):
    """Resolve and confine a logical path to an approved canonical server root."""
    root = get_storage_root(root_key, require_directory=require_root)
    logical = _logical_path(relative_path)
    candidate = root.server_root.joinpath(*logical.parts).resolve(strict=False)
    if not candidate.is_relative_to(root.server_root):
        raise StoragePathError(
            "Den kanoniske filstien ligger utenfor godkjent storage-root."
        )
    client_path = None
    if root.client_root:
        client_path = str(PureWindowsPath(root.client_root, *logical.parts))
    return ResolvedLocation(root, logical, candidate, client_path)


def root_key_for_location(location):
    mapping = getattr(settings, "P7_STORAGE_TYPE_ROOTS", {}) or {}
    root_key = mapping.get(str(location.storage_type))
    if root_key:
        return root_key
    if str(location.storage_type) in {"nas", "local"}:
        return MUSIC_LIBRARY_ROOT
    raise StoragePathError(
        f"Lagringstypen {location.storage_type!r} har ingen lokal filesystem-root."
    )


def resolve_location(location, *, require_root=False):
    return resolve_storage_path(
        location.relative_path,
        root_key=root_key_for_location(location),
        require_root=require_root,
    )


def location_exists(location):
    try:
        return resolve_location(location, require_root=True).server_path.is_file()
    except (ImproperlyConfigured, StoragePathError, OSError):
        return False


def location_is_readable(location):
    try:
        path = resolve_location(location, require_root=True).server_path
        return path.is_file() and os.access(path, os.R_OK)
    except (ImproperlyConfigured, StoragePathError, OSError):
        return False


def stat_location(location):
    """Return current filesystem stat without mutating FileLocation state."""
    resolved = resolve_location(location, require_root=True)
    return resolved.server_path.stat()


def validate_readable_location(location_or_resolution):
    """Return a canonical location only when it currently names a readable file."""
    if isinstance(location_or_resolution, ResolvedLocation):
        # Treat ResolvedLocation as a description, not as proof that a path is
        # still confined. Re-resolve immediately before access so a stale or
        # manually constructed object cannot bypass the configured root.
        resolved = resolve_storage_path(
            location_or_resolution.logical_path,
            root_key=location_or_resolution.root.key,
            require_root=True,
        )
    else:
        resolved = resolve_location(location_or_resolution, require_root=True)
    if not resolved.server_path.is_file() or not os.access(
        resolved.server_path, os.R_OK
    ):
        raise StorageFileUnavailable("Den registrerte filen er ikke lesbar.")
    return resolved


def open_for_read(location_or_resolution) -> BinaryIO:
    """Open one confined file in binary read-only mode."""
    resolved = validate_readable_location(location_or_resolution)
    try:
        return resolved.server_path.open("rb")
    except OSError as error:
        raise StorageFileUnavailable("Den registrerte filen kunne ikke åpnes.") from error


def get_client_path(location):
    return resolve_location(location).client_path


def get_client_folder(location):
    return resolve_location(location).client_folder
