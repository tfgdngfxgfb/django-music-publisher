"""Configurable Release-based folder hints; paths remain inside source roots."""

import re
from string import Formatter
from pathlib import PurePosixPath

from django.conf import settings
from django.core.exceptions import ValidationError

from .models import FileAsset
from .storage import resolve_storage_path


def _component(value):
    return re.sub(r'[\\/:*?"<>|]', " ", str(value or "")).strip(" .")


def suggested_folder(release, role, root_key):
    catalogue = _component(release.catalogue_number)
    series = re.match(r"[^\d]+", catalogue)
    values = {
        "label": _component(release.label.name if release.label else ""),
        "series": series[0].strip(" .-_") if series else "",
        "catalogue_number": catalogue,
        "title": _component(release.title),
    }
    raw = role == FileAsset.Role.RAW_DIGITIZATION
    template = getattr(
        settings,
        "P7_RAW_FOLDER_TEMPLATE" if raw else "P7_MASTER_FOLDER_TEMPLATE",
        "{label}/{series}" if raw else "{label}/{catalogue_number} - {title}",
    )
    try:
        fields = [
            field for _, field, _, _ in Formatter().parse(template) if field
        ]
        if any(not values[field] for field in fields):
            return {
                "expected": "",
                "path": ".",
                "matched": False,
                "missing": True,
            }
        expected = template.format_map(values).strip("/")
    except (KeyError, ValueError) as exc:
        raise ValidationError(
            "Mappekonvensjonen i systemoppsettet er ugyldig."
        ) from exc
    expected = str(PurePosixPath(expected or "."))
    # Never search recursively. Try the expected folder, then its ancestors.
    path = PurePosixPath(expected)
    for candidate in (path, *path.parents):
        location = resolve_storage_path(
            str(candidate), root_key=root_key, require_root=True
        )
        if location.server_path.is_dir():
            return {
                "expected": expected,
                "path": str(candidate),
                "matched": candidate == path,
            }
    return {"expected": expected, "path": ".", "matched": False}


def folder_breadcrumbs(path):
    result = [{"name": "Lagringsområdet", "path": "."}]
    current = PurePosixPath()
    for part in PurePosixPath(path or ".").parts:
        current /= part
        result.append({"name": part, "path": str(current)})
    return result
