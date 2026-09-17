from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core import signing
from django.core.exceptions import (
    ImproperlyConfigured,
    ObjectDoesNotExist,
    PermissionDenied,
    ValidationError,
)
from django.db import transaction
from django.utils import timezone
from mutagen.flac import FLAC

from catalogue.models import (
    ExternalIdentifier,
    Recording,
    RecordingContribution,
)
from media_assets.mastering import _pcm_digest
from media_assets.models import FileAsset, FileLocation
from media_assets.storage import (
    StorageFileUnavailable,
    validate_readable_location,
)

from .metadata_policy import external_radio_tags
from .models import Delivery, DeliveryArtifact, DeliveryItem, DeliveryProfile

PREVIEW_SALT = "p7.delivery.preview.v1"
PREVIEW_MAX_AGE = 60 * 60
logger = logging.getLogger(__name__)
WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class DeliveryPreviewStale(ValidationError):
    pass


@dataclass(frozen=True)
class SourceResolution:
    recording: Recording
    asset: FileAsset | None
    location: FileLocation | None
    status: str
    message: str


def _recordings(recording_ids):
    return list(
        Recording.objects.filter(pk__in=recording_ids)
        .select_related(
            "media_selection__current_radio",
            "music_library_entry__managed_recording",
        )
        .prefetch_related(
            "contributions__party",
            "contributions__artist_identity",
            "identifiers",
            "media_selection__current_radio__locations",
        )
        .order_by("title", "id")
    )


def _artist(recording):
    values = [
        item.display_credit
        for item in recording.contributions.all()
        if item.role
        in {
            RecordingContribution.Role.PRIMARY,
            RecordingContribution.Role.FEATURED,
        }
        and item.display_credit != "Uavklart"
    ]
    return ", ".join(dict.fromkeys(values))


def _isrc(recording):
    return next(
        (
            item.normalized_value
            for item in recording.identifiers.all()
            if item.scheme == ExternalIdentifier.Scheme.ISRC
        ),
        "",
    )


def resolve_delivery_source(recording, *, verify_file=True):
    try:
        asset = recording.media_selection.current_radio
    except (AttributeError, ObjectDoesNotExist):
        asset = None
    if asset is None:
        return SourceResolution(
            recording,
            None,
            None,
            "missing_current",
            "Mangler gjeldende radiofil",
        )
    if (
        asset.recording_id != recording.pk
        or asset.role != FileAsset.Role.RADIO_FLAC
        or asset.lifecycle_status != FileAsset.LifecycleStatus.CURRENT
    ):
        return SourceResolution(
            recording,
            None,
            None,
            "invalid_current",
            "Gjeldende radiofil har inkonsistent status",
        )
    locations = [
        item
        for item in asset.locations.all()
        if item.is_current
        and item.status == FileLocation.Status.ACTIVE
        and item.storage_type
        in {FileLocation.StorageType.NAS, FileLocation.StorageType.LOCAL}
    ]
    if len(locations) != 1:
        return SourceResolution(
            recording,
            asset,
            None,
            "ambiguous_location",
            "Radiofilen har ingen entydig aktiv plassering",
        )
    location = locations[0]
    if verify_file:
        try:
            validate_readable_location(location)
        except (
            ImproperlyConfigured,
            ValidationError,
            OSError,
            StorageFileUnavailable,
        ):
            return SourceResolution(
                recording,
                asset,
                location,
                "unavailable",
                "Kildefilen er ikke tilgjengelig",
            )
    return SourceResolution(recording, asset, location, "ready", "Klar")


def _safe_component(value, fallback):
    value = (
        re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or ""))
        .strip()
        .rstrip(". ")
    )
    value = re.sub(r"\s+", " ", value)[:180] or fallback
    if value.upper() in WINDOWS_RESERVED:
        value = f"_{value}"
    return value


def _output_names(rows):
    seen = {}
    for row in rows:
        base = _safe_component(
            f"{row['artist']} - {row['title']}".strip(" -"), "Innspilling"
        )
        number = seen.get(base.casefold(), 0) + 1
        seen[base.casefold()] = number
        row["output_filename"] = (
            f"{base}{f' ({number})' if number > 1 else ''}.flac"
        )


def build_preview(recording_ids, *, user, cleaned_data):
    ids = list(dict.fromkeys(str(value) for value in recording_ids if value))
    recordings = _recordings(ids)
    found = {str(item.pk) for item in recordings}
    if not ids or found != set(ids):
        raise ValidationError("Velg minst én gyldig innspilling.")
    rows = []
    for recording in recordings:
        source = resolve_delivery_source(recording)
        rows.append(
            {
                "recording_id": str(recording.pk),
                "recording_revision": recording.revision,
                "title": recording.title,
                "artist": _artist(recording),
                "isrc": _isrc(recording),
                "status": source.status,
                "message": source.message,
                "asset_id": str(source.asset.pk) if source.asset else None,
                "asset_revision": (
                    source.asset.revision if source.asset else None
                ),
                "location_id": (
                    str(source.location.pk) if source.location else None
                ),
                "location_revision": (
                    source.location.revision if source.location else None
                ),
            }
        )
    _output_names(rows)
    data = {
        "user_id": str(user.pk),
        "profile": cleaned_data["profile"],
        "profile_label": DeliveryProfile(cleaned_data["profile"]).label,
        "purpose": cleaned_data["purpose"],
        "purpose_label": Delivery.Purpose(cleaned_data["purpose"]).label,
        "purpose_description": cleaned_data.get("purpose_description", ""),
        "recipient_name": cleaned_data["recipient_name"],
        "recipient_organization": cleaned_data.get(
            "recipient_organization", ""
        ),
        "retain_for_future_use": bool(
            cleaned_data.get("retain_for_future_use")
        ),
        "other_use": bool(cleaned_data.get("other_use")),
        "other_use_description": cleaned_data.get("other_use_description", ""),
        "items": rows,
    }
    return {
        **data,
        "token": signing.dumps(data, salt=PREVIEW_SALT, compress=True),
    }


def _artifact_root():
    raw = getattr(settings, "P7_DELIVERY_ARTIFACT_ROOT", "")
    if not raw:
        raise ImproperlyConfigured(
            "P7_DELIVERY_ARTIFACT_ROOT er ikke konfigurert."
        )
    root = Path(raw).expanduser().resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    return root


def artifact_path(relative_path):
    root = _artifact_root()
    candidate = (root / relative_path).resolve(strict=False)
    if not candidate.is_relative_to(root):
        raise ValidationError("Ugyldig leveranseartefakt.")
    return candidate


def _external_tags(item):
    source = validate_readable_location(item.source_file_location).server_path
    audio = FLAC(source)
    tags = external_radio_tags(audio.tags or {})
    try:
        managed = item.recording.music_library_entry.managed_recording
    except (AttributeError, ObjectDoesNotExist):
        managed = None
    if managed:
        tags["TITLE"] = [item.title_snapshot]
        if item.artist_snapshot:
            tags["ARTIST"] = [item.artist_snapshot]
        if item.isrc_snapshot:
            tags["ISRC"] = [item.isrc_snapshot]
    return tags


def _source_metadata(location):
    source = validate_readable_location(location).server_path
    audio = FLAC(source)
    return {
        str(key).upper(): [str(value) for value in values]
        for key, values in (audio.tags or {}).items()
    }


def _external_copy(item, target):
    source = validate_readable_location(item.source_file_location).server_path
    with source.open("rb") as source_stream, target.open(
        "wb"
    ) as target_stream:
        shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
    tags = _external_tags(item)
    audio = FLAC(target)
    audio.clear()
    audio.clear_pictures()
    for key, values in tags.items():
        audio[key] = values
    audio.save()
    if _pcm_digest(source) != _pcm_digest(target):
        target.unlink(missing_ok=True)
        raise ValidationError("Leveransekopien endret lydinnholdet.")
    return tags


def _manifest_rows(items, external):
    for item in items:
        row = {
            "title": item.title_snapshot,
            "artist": item.artist_snapshot,
            "isrc": item.isrc_snapshot,
            "output_filename": item.output_filename,
        }
        if not external:
            row.update(
                {
                    "recording_uuid": str(item.recording_uuid_snapshot),
                    "file_asset_uuid": str(
                        item.source_asset_uuid_snapshot or ""
                    ),
                    "source_sha256": item.source_sha256_snapshot,
                    "profile": item.delivery.profile,
                }
            )
        yield row


def _write_manifest(directory, items, external):
    rows = list(_manifest_rows(items, external))
    fields = (
        list(rows[0])
        if rows
        else ["title", "artist", "isrc", "output_filename"]
    )
    csv_path = directory / "manifest.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (directory / "manifest.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _register_artifact(delivery, path, kind):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    ttl = int(getattr(settings, "P7_DELIVERY_ARTIFACT_TTL_HOURS", 24))
    return DeliveryArtifact.objects.create(
        delivery=delivery,
        kind=kind,
        relative_path=path.relative_to(_artifact_root()).as_posix(),
        filename=path.name,
        size_bytes=path.stat().st_size,
        sha256=digest.hexdigest(),
        expires_at=timezone.now() + timezone.timedelta(hours=ttl),
    )


def _generate_artifact(delivery):
    ready = list(
        delivery.items.filter(status=DeliveryItem.Status.READY).select_related(
            "recording", "source_file_location"
        )
    )
    external = delivery.profile == DeliveryProfile.EXTERNAL_RADIO
    work = artifact_path(str(delivery.pk))
    work.mkdir(parents=True, exist_ok=True)
    if len(ready) == 1 and external:
        target = work / ready[0].output_filename
        tags = _external_copy(ready[0], target)
        ready[0].delivered_metadata = tags
        ready[0].save(update_fields={"delivered_metadata"})
        return _register_artifact(
            delivery, target, DeliveryArtifact.Kind.TRANSFORMED_FLAC
        )
    if len(ready) > 1:
        payload = work / "payload"
        payload.mkdir(exist_ok=True)
        for item in ready:
            target = payload / item.output_filename
            if external:
                tags = _external_copy(item, target)
                item.delivered_metadata = tags
                item.save(update_fields={"delivered_metadata"})
            else:
                source = validate_readable_location(
                    item.source_file_location
                ).server_path
                with source.open("rb") as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
        _write_manifest(payload, ready, external)
        archive = work / f"P7-delivery-{delivery.pk}.zip"
        with zipfile.ZipFile(
            archive, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        ) as output:
            for path in sorted(
                payload.iterdir(), key=lambda value: value.name.casefold()
            ):
                output.write(path, path.name)
        shutil.rmtree(payload)
        return _register_artifact(delivery, archive, DeliveryArtifact.Kind.ZIP)
    return None


def _delivery_manifest(delivery):
    ready = list(
        delivery.items.filter(status=DeliveryItem.Status.READY).select_related(
            "recording"
        )
    )
    return list(
        _manifest_rows(
            ready, delivery.profile == DeliveryProfile.EXTERNAL_RADIO
        )
    )


def confirm_preview(token, *, user, allow_partial=False):
    try:
        data = signing.loads(token, salt=PREVIEW_SALT, max_age=PREVIEW_MAX_AGE)
    except signing.BadSignature as error:
        raise DeliveryPreviewStale(
            "Forhåndsvisningen er utløpt eller ugyldig."
        ) from error
    if data.get("user_id") != str(user.pk):
        raise PermissionDenied
    if data.get(
        "profile"
    ) == DeliveryProfile.EXTERNAL_RADIO and not user.has_perm(
        "delivery.create_external_delivery"
    ):
        raise PermissionDenied
    expected = {row["recording_id"]: row for row in data["items"]}
    recordings = _recordings(expected)
    resolved = []
    for recording in recordings:
        old = expected[str(recording.pk)]
        now = resolve_delivery_source(recording)
        now_asset = str(now.asset.pk) if now.asset else None
        now_location = str(now.location.pk) if now.location else None
        if (
            old["asset_id"] != now_asset
            or old["location_id"] != now_location
            or old["recording_revision"] != recording.revision
            or (now.asset and old["asset_revision"] != now.asset.revision)
            or (
                now.location
                and old.get("location_revision") != now.location.revision
            )
        ):
            raise DeliveryPreviewStale(
                "Gjeldende radiofil eller katalogdata er endret. Lag en ny forhåndsvisning."
            )
        if old["status"] == "ready" and now.status != "ready":
            raise DeliveryPreviewStale(
                "En kildefil er ikke lenger tilgjengelig. Lag en ny forhåndsvisning."
            )
        resolved.append((recording, now, old))
    ready_count = sum(source.status == "ready" for _, source, _ in resolved)
    if not ready_count:
        raise ValidationError("Ingen valgte innspillinger kan leveres.")
    if ready_count != len(resolved) and not allow_partial:
        raise ValidationError(
            "Leveransen inneholder elementer som ikke er klare. Bekreft delvis levering eksplisitt."
        )
    with transaction.atomic():
        delivery = Delivery.objects.create(
            created_by=user,
            status=Delivery.Status.PREPARING,
            profile=data["profile"],
            purpose=data["purpose"],
            purpose_description=data["purpose_description"],
            recipient_name=data["recipient_name"],
            recipient_organization=data["recipient_organization"],
            retain_for_future_use=data["retain_for_future_use"],
            other_use=data["other_use"],
            other_use_description=data["other_use_description"],
        )
        for recording, source, old in resolved:
            ready = source.status == "ready"
            DeliveryItem.objects.create(
                delivery=delivery,
                recording=recording,
                source_file_asset=source.asset if ready else None,
                source_file_location=source.location if ready else None,
                status=(
                    DeliveryItem.Status.READY
                    if ready
                    else DeliveryItem.Status.SKIPPED
                ),
                output_filename=old["output_filename"] if ready else "",
                recording_uuid_snapshot=recording.pk,
                title_snapshot=old["title"],
                artist_snapshot=old["artist"],
                isrc_snapshot=old["isrc"],
                source_asset_uuid_snapshot=source.asset.pk if ready else None,
                source_sha256_snapshot=source.asset.sha256 if ready else "",
                delivered_metadata=(
                    _source_metadata(source.location)
                    if ready
                    and data["profile"] == DeliveryProfile.INTERNAL_COMPLETE
                    else {}
                ),
                message="" if ready else source.message,
            )
    try:
        delivery.manifest_snapshot = _delivery_manifest(delivery)
        _generate_artifact(delivery)
        delivery.status = (
            Delivery.Status.READY
            if ready_count == len(resolved)
            else Delivery.Status.PARTIAL
        )
    except Exception as error:
        shutil.rmtree(artifact_path(str(delivery.pk)), ignore_errors=True)
        logger.exception(
            "Delivery artifact generation failed",
            extra={
                "delivery_id": str(delivery.pk),
                "profile": delivery.profile,
            },
        )
        delivery.status = Delivery.Status.FAILED
        delivery.save(update_fields={"status", "manifest_snapshot"})
        raise ValidationError(
            "Leveranseartefakten kunne ikke opprettes."
        ) from error
    delivery.save(update_fields={"status", "manifest_snapshot"})
    return delivery
