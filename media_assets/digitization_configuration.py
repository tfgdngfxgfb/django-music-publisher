"""Installation defaults for source pickers, without remapping stored files."""

from dataclasses import dataclass
from string import Formatter

from django.conf import settings
from django.core.exceptions import ValidationError

from .storage import _logical_path, resolve_storage_path


def source_root_choices():
    roots = getattr(settings, "P7_STORAGE_ROOTS", {}) or {}
    choices = []
    if (
        roots.get("music_library")
        or getattr(settings, "P7_MUSIC_ROOT", "")
        or getattr(settings, "P7_NAS_ROOT", "")
    ):
        choices.append(("music_library", "Musikkarkiv · felles kildeområde"))
    for key, label in (
        ("raw_sources", "Rå digitalisering"),
        ("edited_masters", "Redigerte mastere"),
    ):
        if key in roots:
            choices.append((key, label))
    return choices


def validate_folder_template(value):
    try:
        for _, field, spec, conversion in Formatter().parse(value):
            if field is not None and (
                field not in {"label", "series", "catalogue_number", "title"}
                or spec
                or conversion
            ):
                raise ValueError
        sample = value.format_map(
            dict.fromkeys(
                ("label", "series", "catalogue_number", "title"), "Eksempel"
            )
        )
        _logical_path(sample)
        if any(ord(char) < 32 for char in value):
            raise ValueError
    except (ValueError, KeyError, ValidationError) as exc:
        raise ValidationError(
            "Bruk en relativ mappe med {label}, {series}, {catalogue_number} og/eller {title}. Absolutte stier og «..» er ikke tillatt."
        ) from exc


@dataclass(frozen=True)
class PickerDefaults:
    root_key: str
    base_path: str
    folder_template: str


def picker_defaults(role, configuration=None):
    from .models import DigitizationConfiguration, FileAsset

    if configuration is None:
        configuration = DigitizationConfiguration.objects.filter(pk=1).first()
    raw = role == FileAsset.Role.RAW_DIGITIZATION
    prefix = "raw" if raw else "master"
    keys = dict(source_root_choices())
    preferred = "raw_sources" if raw else "edited_masters"
    root = getattr(configuration, f"{prefix}_root_key", "") or (
        preferred if preferred in keys else "music_library"
    )
    return PickerDefaults(
        root,
        getattr(configuration, f"{prefix}_base_path", "."),
        (
            getattr(configuration, f"{prefix}_folder_template", None)
            if configuration is not None
            else getattr(
                settings,
                (
                    "P7_RAW_FOLDER_TEMPLATE"
                    if raw
                    else "P7_MASTER_FOLDER_TEMPLATE"
                ),
                (
                    "{label}/{series}"
                    if raw
                    else "{label}/{catalogue_number} - {title}"
                ),
            )
        ),
    )


def validate_source_folder(root_key, path):
    if root_key not in dict(source_root_choices()):
        raise ValidationError("Velg et konfigurert kildeområde.")
    resolved = resolve_storage_path(path, root_key=root_key, require_root=True)
    if not resolved.server_path.is_dir():
        raise ValidationError(
            "Startmappen finnes ikke i det valgte lagringsområdet."
        )
    return str(resolved.logical_path)
