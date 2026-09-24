"""Preview/apply workflows for externally captured and edited music.

No audio writes. Capture membership and source lineage are separate from the
music-specific ReleaseTrack/Recording assignment and existing 4.5A selection.
"""

import json
import os
import re
from hashlib import sha256
from pathlib import PurePosixPath

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from catalogue.models import Recording, Release, ReleaseTrack
from catalogue.services import (
    resolve_release_track_recording,
    find_recording_candidates,
)
from music_library.models import MusicLibraryEntry
from .mastering import inspect_master, select_master
from .models import (
    DigitizationBatch,
    DigitizationDerivation,
    DigitizationFile,
    DigitizationPlan,
    FileAsset,
    FileChecksum,
    FileLocation,
    MediaAssetEvent,
    FileDerivation,
    RadioFlacGeneration,
    RecordingMediaSelection,
)
from .storage import resolve_storage_path


def require_operator(user):
    if not user.has_perms(
        (
            "media_assets.view_digitizationbatch",
            "media_assets.operate_digitization",
        )
    ):
        raise PermissionDenied(
            "Du har ikke tilgang til å endre digitaliseringer."
        )


def _relevant_recording_ids(batch, operation, payload, files):
    """Recordings whose catalogue data informed this concrete preview."""
    if operation == "select_master":
        selected = set(payload.get("assets", ()))
        return {
            item.asset.recording_id
            for item in files
            if str(item.asset_id) in selected and item.asset.recording_id
        }
    if operation not in {"recording_link", "link_masters"}:
        return set()
    rows = payload.get("rows", ())
    ids = {row.get("recording") for row in rows if row.get("recording")}
    track_ids = {row.get("track") for row in rows if row.get("track")}
    ids.update(
        batch.release.tracks.filter(pk__in=track_ids).values_list(
            "recording_id", flat=True
        )
    )
    selected_assets = {str(row.get("asset")) for row in rows}
    ids.update(
        item.asset.recording_id
        for item in files
        if str(item.asset_id) in selected_assets and item.asset.recording_id
    )
    return {pk for pk in ids if pk}


def _state(batch, operation=None, payload=None):
    """Include selections on this Release, even when edited in another batch."""
    files = list(batch.files.select_related("asset").order_by("pk"))
    recording_ids = set(
        batch.release.tracks.values_list("recording_id", flat=True)
    )
    recording_ids.update(
        item.asset.recording_id for item in files if item.asset.recording_id
    )
    relevant_recording_ids = _relevant_recording_ids(
        batch, operation, payload or {}, files
    )
    data = {
        "batch": batch.revision,
        "release": Release.objects.get(pk=batch.release_id).revision,
        "files": [
            (
                str(item.pk),
                item.revision,
                str(item.asset_id),
                item.asset.revision,
            )
            for item in files
        ],
        "tracks": list(
            batch.release.tracks.order_by("pk").values_list("pk", "revision")
        ),
        "selections": list(
            RecordingMediaSelection.objects.filter(
                recording_id__in=recording_ids
            )
            .order_by("pk")
            .values_list("pk", "revision")
        ),
        "sources": list(
            DigitizationDerivation.objects.filter(
                derived_asset_id__in=[f.asset_id for f in files]
            )
            .order_by("pk")
            .values_list("pk", "revision")
        ),
        "recordings": list(
            Recording.objects.filter(pk__in=relevant_recording_ids)
            .order_by("pk")
            .values_list("pk", "revision")
        ),
    }
    return sha256(
        json.dumps(data, default=str, sort_keys=True).encode()
    ).hexdigest()


def _assets(batch, ids):
    if not ids or len(ids) != len(set(ids)):
        raise ValidationError("Velg minst én fil, uten gjentatte rader.")
    assets = {
        str(item.asset_id): item.asset
        for item in batch.files.select_related("asset", "asset__recording")
    }
    if any(str(pk) not in assets for pk in ids):
        raise ValidationError("Alle valgte filer må tilhøre denne batchen.")
    return [assets[str(pk)] for pk in ids]


def _event(batch, asset, user, event_type, *, related=None, details=None):
    return MediaAssetEvent.objects.create(
        digitization_batch=batch,
        recording=asset.recording,
        asset=asset,
        related_asset=related,
        actor=user,
        event_type=event_type,
        details=details or {},
    )


def list_digitization_folder(*, root_key, relative_path=".", query=""):
    """Browse one configured folder without hashing or changing its contents."""
    folder = resolve_storage_path(
        relative_path or ".", root_key=root_key, require_root=True
    )
    if not folder.server_path.is_dir():
        raise ValidationError("Mappen finnes ikke under valgt lagringsrot.")
    query = query.strip().casefold()
    entries = []
    for path in sorted(
        folder.server_path.iterdir(), key=lambda item: item.name.casefold()
    ):
        if query and query not in path.name.casefold():
            continue
        logical = str(folder.logical_path / path.name)
        try:
            resolve_storage_path(logical, root_key=root_key)
        except ValidationError:
            continue
        if path.is_dir():
            entries.append(
                {"name": path.name, "path": logical, "folder": True}
            )
        elif path.is_file() and path.suffix.casefold() == ".wav":
            entries.append(
                {
                    "name": path.name,
                    "path": logical,
                    "folder": False,
                    "size_bytes": path.stat().st_size,
                }
            )
        if len(entries) >= 500:
            break
    return entries


def inspect_folder(*, root_key, relative_path, role, selected_names=None):
    if role not in (
        FileAsset.Role.RAW_DIGITIZATION,
        FileAsset.Role.EDITED_WAV_MASTER,
    ):
        raise ValidationError(
            "Velg rå digitalisering eller redigert WAV-master."
        )
    folder = resolve_storage_path(
        relative_path, root_key=root_key, require_root=True
    )
    if not folder.server_path.is_dir():
        raise ValidationError("Mappen finnes ikke under valgt lagringsrot.")
    paths = sorted(
        (
            path
            for path in folder.server_path.iterdir()
            if path.suffix.lower() == ".wav"
        ),
        key=lambda path: path.name.casefold(),
    )
    if selected_names is not None:
        wanted = set(selected_names)
        if (
            not wanted
            or len(wanted) != len(selected_names)
            or any(
                name in {".", ".."} or "/" in name or "\\" in name
                for name in wanted
            )
        ):
            raise ValidationError("Velg én eller flere gyldige WAV-filer.")
        paths = [path for path in paths if path.name in wanted]
        if {path.name for path in paths} != wanted:
            raise ValidationError("En valgt WAV-fil finnes ikke i mappen.")
    if not paths:
        raise ValidationError("Mappen inneholder ingen WAV-filer.")
    results = []
    for path in paths:
        logical = str(folder.logical_path / path.name)
        info = inspect_master(root_key=root_key, relative_path=logical)
        if info.format not in {"WAV", "WAVEX"}:
            raise ValidationError(f"{path.name}: filen er ikke en WAV.")
        results.append(
            {
                "root_key": root_key,
                "path": info.relative_path,
                "filename": info.filename,
                "sha256": info.sha256,
                "size": info.size_bytes,
                "technical": info.technical_metadata,
                "modified": info.source_modified_at.isoformat(),
                "role": role,
                "registration_key": sha256(
                    os.path.normcase(
                        str(
                            resolve_storage_path(
                                logical, root_key=root_key
                            ).server_path
                        )
                    ).encode()
                ).hexdigest(),
            }
        )
    return results


def _validate(batch, operation, payload):
    changes = []
    correction_operations = {
        "unlink_raw",
        "clear_master_selection",
        "unlink_recording",
    }
    if (
        batch.status != DigitizationBatch.Status.OPEN
        and operation not in correction_operations
    ):
        raise ValidationError("Digitaliseringen er avsluttet.")
    if operation in correction_operations:
        note = payload.get("note", "").strip()
        if not note:
            raise ValidationError(
                "Oppgi hvorfor koblingen fjernes. Begrunnelsen lagres i historikken."
            )
        if batch.status == DigitizationBatch.Status.COMPLETE:
            changes.append(
                "Digitaliseringen åpnes igjen fordi en ferdigstilt kobling korrigeres."
            )
    if operation == "link_masters":
        rows = payload.get("rows", [])
        _assets(batch, [row["asset"] for row in rows])
        changes.extend(_validate(batch, "recording_link", {"rows": rows}))
        for row in rows:
            changes.extend(
                _validate(
                    batch,
                    "raw_link",
                    {
                        "assets": [row["asset"]],
                        "source": row.get("source", ""),
                        "note": payload.get("note", ""),
                    },
                )
            )
    elif operation == "register":
        for item in payload["files"]:
            locations = list(
                FileLocation.objects.filter(
                    storage_root_key=item["root_key"],
                    relative_path=item["path"],
                    is_current=True,
                ).select_related("asset")
            )
            if locations:
                if (
                    len(locations) != 1
                    or not batch.files.filter(
                        asset=locations[0].asset
                    ).exists()
                    or locations[0].asset.role != item["role"]
                    or locations[0].asset.sha256 != item["sha256"]
                ):
                    raise ValidationError(
                        f"{item['filename']}: plasseringen er allerede registrert med annet innhold eller tilhørighet."
                    )
                changes.append(f"Behold registrert fil: {item['filename']}")
            else:
                changes.append(
                    f"Registrer {item['filename']} · {item['size']} byte · {item['sha256'][:12]}"
                )
    elif operation == "raw_link":
        raw = _assets(batch, [payload["source"]])[0]
        if raw.role != FileAsset.Role.RAW_DIGITIZATION:
            raise ValidationError("Velg en råfil som kilde.")
        for asset in _assets(batch, payload["assets"]):
            if asset.role != FileAsset.Role.EDITED_WAV_MASTER:
                raise ValidationError(
                    "Bare redigerte mastere kan kobles til råkilden."
                )
            old = list(asset.digitization_sources.filter(is_active=True))
            if (
                old
                and old[0].source_asset_id != raw.pk
                and not payload.get("note", "").strip()
            ):
                raise ValidationError(
                    "Beskriv hvorfor den tidligere råkoblingen korrigeres. Historikken beholdes."
                )
            changes.append(
                f"{asset.filename} → råkilde {raw.filename}"
                + (" · tidligere kobling bevares i historikk" if old else "")
            )
    elif operation == "recording_link":
        rows = payload["rows"]
        assets = _assets(batch, [row["asset"] for row in rows])
        for row, asset in zip(rows, assets):
            if asset.role != FileAsset.Role.EDITED_WAV_MASTER:
                raise ValidationError(
                    "Bare mastere kan kobles til innspilling."
                )
            track = (
                ReleaseTrack.objects.get(
                    pk=row["track"], release=batch.release
                )
                if row.get("track")
                else None
            )
            recording = (
                Recording.objects.get(pk=row["recording"])
                if row.get("recording")
                else (track.recording if track else None)
            )
            if track and recording.pk != track.recording_id:
                raise ValidationError(
                    "Valgt innspilling stemmer ikke med sporforekomsten."
                )
            title = row.get("new_title", "").strip()
            if (recording is None) == (not title):
                raise ValidationError(
                    "Velg innspilling/spor eller oppgi ny tittel."
                )
            if (
                title
                and not row.get("force_create")
                and find_recording_candidates(title=title)
            ):
                raise ValidationError(
                    "Tittelen har mulige eksisterende innspillinger. Velg en kandidat eller bekreft opprettelse av separat innspilling."
                )
            if asset.recording_id and (
                not recording or asset.recording_id != recording.pk
            ):
                raise ValidationError(
                    "En allerede koblet master kan ikke flyttes til en annen innspilling her."
                )
            if asset.release_track_id and (
                not track or asset.release_track_id != track.pk
            ):
                raise ValidationError(
                    "Eksisterende sporforekomst beholdes. Kontroller koblingen individuelt."
                )
            changes.append(
                f"{asset.filename} → {recording.title if recording else 'Ny innspilling: ' + title}"
                + (
                    f" · spor {track.sequence_number}"
                    if track
                    else " · uten sporforekomst"
                )
            )
        changes.append(
            "Innspillinger med én redigert master og uten tidligere "
            "mastervalg får denne masteren valgt automatisk."
        )
    elif operation == "select_master":
        assets = _assets(batch, payload["assets"])
        seen = set()
        release_counts = {
            row["recording_id"]: row["count"]
            for row in ReleaseTrack.objects.filter(
                recording_id__in=[asset.recording_id for asset in assets]
            )
            .values("recording_id")
            .annotate(count=Count("release_id", distinct=True))
        }
        for asset in assets:
            if (
                asset.role != FileAsset.Role.EDITED_WAV_MASTER
                or not asset.recording_id
            ):
                raise ValidationError(
                    "Alle mastere må først kobles til innspilling."
                )
            if asset.recording_id in seen:
                raise ValidationError("Velg bare én master per innspilling.")
            seen.add(asset.recording_id)
            selections = list(
                RecordingMediaSelection.objects.filter(
                    recording_id=asset.recording_id
                )
            )
            old = selections[0].selected_master if selections else None
            if old and old.pk != asset.pk:
                raise ValidationError(
                    f"{asset.recording.title} har allerede valgt master {old.filename}. Bruk individuelt mastervalg under Innspilling → Filer."
                )
            changes.append(
                f"{asset.recording.title} → valgt master {asset.filename} · "
                f"gjelder innspillingen globalt · forekommer på "
                f"{release_counts.get(asset.recording_id, 0)} utgivelser"
            )
    elif operation == "unlink_raw":
        for asset in _assets(batch, payload.get("assets", [])):
            if asset.role != FileAsset.Role.EDITED_WAV_MASTER:
                raise ValidationError(
                    "Bare redigerte mastere kan frakobles råkilde."
                )
            relation = asset.digitization_sources.filter(
                is_active=True
            ).first()
            if not relation:
                raise ValidationError(
                    f"{asset.filename} har ingen aktiv råkobling."
                )
            changes.append(
                f"Fjern aktiv RAW-kobling: {relation.source_asset.filename} "
                f"→ {asset.filename}. Historikken beholdes."
            )
    elif operation == "clear_master_selection":
        for asset in _assets(batch, payload.get("assets", [])):
            if not asset.recording_id:
                raise ValidationError(
                    f"{asset.filename} er ikke koblet til en innspilling."
                )
            selection = RecordingMediaSelection.objects.filter(
                recording_id=asset.recording_id
            ).first()
            if not selection or selection.selected_master_id != asset.pk:
                raise ValidationError(
                    f"{asset.filename} er ikke valgt master."
                )
            changes.append(
                f"Fjern mastervalget for {asset.recording.title}. "
                "Masterfilen og historikken beholdes."
            )
    elif operation == "unlink_recording":
        for asset in _assets(batch, payload.get("assets", [])):
            if (
                asset.role != FileAsset.Role.EDITED_WAV_MASTER
                or not asset.recording_id
            ):
                raise ValidationError(
                    "Velg en master som er koblet til en innspilling."
                )
            selection = RecordingMediaSelection.objects.filter(
                recording_id=asset.recording_id
            ).first()
            if selection and selection.selected_master_id == asset.pk:
                raise ValidationError(
                    "Fjern mastervalget før koblingen til innspillingen fjernes."
                )
            if (
                RadioFlacGeneration.objects.filter(master_asset=asset).exists()
                or FileDerivation.objects.filter(source_asset=asset).exists()
            ):
                raise ValidationError(
                    "Masteren har Radio-FLAC-/filavledningshistorikk og kan ikke frakobles. "
                    "Behold koblingen og korriger med en ny master."
                )
            changes.append(
                f"Fjern koblingen {asset.filename} → {asset.recording.title}. "
                "Innspillingen, sporforekomsten, filen og historikken beholdes."
            )
    else:
        raise ValidationError("Ukjent digitaliseringshandling.")
    if not changes:
        raise ValidationError("Ingen rader å behandle.")
    return changes


@transaction.atomic
def preview_operation(*, batch, operation, payload, user):
    require_operator(user)
    batch = DigitizationBatch.objects.select_for_update().get(pk=batch.pk)
    consequences = _validate(batch, operation, payload)
    return DigitizationPlan.objects.create(
        batch=batch,
        operation=operation,
        payload=payload,
        expected_state=_state(batch, operation, payload),
        consequences=consequences,
        created_by=user,
    )


def preview_registration(
    *, batch, root_key, relative_path, role, user, selected_names=None
):
    require_operator(user)
    files = inspect_folder(
        root_key=root_key,
        relative_path=relative_path,
        role=role,
        selected_names=selected_names,
    )
    return preview_operation(
        batch=batch, operation="register", payload={"files": files}, user=user
    )


@transaction.atomic
def delete_empty_batch(*, batch, user):
    """Remove an unused work context while preserving its Release."""
    require_operator(user)
    batch = DigitizationBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.files.exists() or batch.events.exists():
        raise ValidationError(
            "Digitaliseringen har registrerte filer eller historikk og kan "
            "ikke fjernes. Bevar eller avslutt den i stedet."
        )
    if batch.plans.filter(applied_at__isnull=False).exists():
        raise ValidationError(
            "Digitaliseringen har utførte arbeidsplaner og kan ikke fjernes."
        )
    # Unapplied previews are operational drafts, not protected catalogue data.
    batch.plans.all().delete()
    batch.delete()


@transaction.atomic
def complete_digitization(*, batch, user):
    """Finish capture without requiring a generated/current Radio-FLAC."""
    require_operator(user)
    Release.objects.select_for_update().get(pk=batch.release_id)
    batch = DigitizationBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.status == DigitizationBatch.Status.COMPLETE:
        return batch
    recording_ids = set(
        batch.release.tracks.values_list("recording_id", flat=True)
    )
    if not recording_ids:
        raise ValidationError("Registrer utgivelsens spor før avslutning.")
    members = list(batch.files.select_related("asset"))
    raw_ids = {
        item.asset_id
        for item in members
        if item.asset.role == FileAsset.Role.RAW_DIGITIZATION
    }
    masters = [
        item.asset
        for item in members
        if item.asset.role == FileAsset.Role.EDITED_WAV_MASTER
    ]
    if not raw_ids or not masters:
        raise ValidationError("Registrer RAW og redigerte mastere først.")
    if any(not asset.recording_id for asset in masters):
        raise ValidationError("Koble alle redigerte mastere til innspilling.")
    sourced_master_ids = set(
        DigitizationDerivation.objects.filter(
            derived_asset_id__in=[asset.pk for asset in masters],
            source_asset_id__in=raw_ids,
            is_active=True,
        ).values_list("derived_asset_id", flat=True)
    )
    if any(asset.pk not in sourced_master_ids for asset in masters):
        raise ValidationError("Koble RAW-kilde til alle redigerte mastere.")
    selected_recording_ids = set(
        RecordingMediaSelection.objects.filter(
            recording_id__in=recording_ids,
            selected_master__isnull=False,
        ).values_list("recording_id", flat=True)
    )
    if recording_ids - selected_recording_ids:
        raise ValidationError(
            "Velg master for alle innspillingene på utgivelsen."
        )
    batch.status = DigitizationBatch.Status.COMPLETE
    batch.save(update_fields=("status", "updated_at", "revision"))
    return batch


def apply_plan(*, plan, user):
    require_operator(user)
    plan = DigitizationPlan.objects.select_related("batch").get(pk=plan.pk)
    if plan.created_by_id != user.pk:
        raise PermissionDenied(
            "Bare den som forhåndsviste handlingen kan bekrefte den."
        )
    if plan.applied_at:
        return plan
    # Hash external files before holding catalogue locks. Nothing is copied or written.
    if plan.operation == "register":
        for item in plan.payload["files"]:
            info = inspect_master(
                root_key=item["root_key"], relative_path=item["path"]
            )
            if info.sha256 != item["sha256"]:
                raise ValidationError(
                    "En fil er endret siden forhåndsvisningen. Les mappen på nytt."
                )
            current_key = sha256(
                os.path.normcase(
                    str(
                        resolve_storage_path(
                            item["path"], root_key=item["root_key"]
                        ).server_path
                    )
                ).encode()
            ).hexdigest()
            if current_key != item["registration_key"]:
                raise ValidationError(
                    "Filplasseringen er endret siden forhåndsvisningen."
                )
    with transaction.atomic():
        Release.objects.select_for_update().get(pk=plan.batch.release_id)
        batch = DigitizationBatch.objects.select_for_update().get(
            pk=plan.batch_id
        )
        plan = DigitizationPlan.objects.select_for_update().get(pk=plan.pk)
        if plan.applied_at:
            return plan
        ids = set(batch.release.tracks.values_list("recording_id", flat=True))
        ids.update(
            batch.files.exclude(asset__recording=None).values_list(
                "asset__recording_id", flat=True
            )
        )
        ids.update(
            row["recording"]
            for row in plan.payload.get("rows", [])
            if row.get("recording")
        )
        list(
            Recording.objects.select_for_update()
            .filter(pk__in=ids)
            .order_by("pk")
        )
        list(
            FileAsset.objects.select_for_update()
            .filter(digitization_file__batch=batch)
            .order_by("pk")
        )
        if _state(batch, plan.operation, plan.payload) != plan.expected_state:
            raise ValidationError(
                "Arbeidsgrunnlaget er endret siden forhåndsvisningen. Lag en ny forhåndsvisning."
            )
        _validate(batch, plan.operation, plan.payload)
        if plan.operation == "register":
            _register(batch, plan.payload["files"], user)
        elif plan.operation == "link_masters":
            _apply_recording_links(batch, plan.payload, user, plan)
            for row in plan.payload["rows"]:
                _apply_raw_links(
                    batch,
                    {
                        "source": row["source"],
                        "assets": [row["asset"]],
                        "note": plan.payload.get("note", ""),
                    },
                    user,
                )
        elif plan.operation == "raw_link":
            _apply_raw_links(batch, plan.payload, user)
        elif plan.operation == "recording_link":
            _apply_recording_links(batch, plan.payload, user, plan)
        elif plan.operation == "select_master":
            for asset in sorted(
                _assets(batch, plan.payload["assets"]),
                key=lambda item: str(item.recording_id),
            ):
                select_master(
                    recording=asset.recording, asset=asset, user=user
                )
        elif plan.operation == "unlink_raw":
            _apply_unlink_raw(batch, plan.payload, user)
        elif plan.operation == "clear_master_selection":
            _apply_clear_master_selection(batch, plan.payload, user)
        elif plan.operation == "unlink_recording":
            _apply_unlink_recording(batch, plan.payload, user)
        if (
            plan.operation
            in {
                "unlink_raw",
                "clear_master_selection",
                "unlink_recording",
            }
            and batch.status == DigitizationBatch.Status.COMPLETE
        ):
            batch.status = DigitizationBatch.Status.OPEN
        batch.save()  # Invalidate every older preview from this batch.
        plan.applied_at = timezone.now()
        plan.save()
        return plan


def _apply_unlink_raw(batch, payload, user):
    for asset in _assets(batch, payload["assets"]):
        relation = asset.digitization_sources.get(is_active=True)
        relation.is_active = False
        relation.save()
        _event(
            batch,
            asset,
            user,
            MediaAssetEvent.EventType.RAW_UNLINKED,
            related=relation.source_asset,
            details={"note": payload["note"].strip()},
        )


def _apply_clear_master_selection(batch, payload, user):
    for asset in _assets(batch, payload["assets"]):
        selection = RecordingMediaSelection.objects.select_for_update().get(
            recording_id=asset.recording_id,
            selected_master_id=asset.pk,
        )
        _event(
            batch,
            asset,
            user,
            MediaAssetEvent.EventType.MASTER_SELECTION_CLEARED,
            details={"note": payload["note"].strip()},
        )
        selection.selected_master = None
        selection.selected_master_by = None
        selection.selected_master_at = None
        selection.save()


def _apply_unlink_recording(batch, payload, user):
    for asset in _assets(batch, payload["assets"]):
        _event(
            batch,
            asset,
            user,
            MediaAssetEvent.EventType.RECORDING_UNLINKED,
            details={
                "recording": str(asset.recording_id),
                "track": (
                    str(asset.release_track_id)
                    if asset.release_track_id
                    else None
                ),
                "note": payload["note"].strip(),
            },
        )
        asset.recording = None
        asset.release = batch.release
        asset.release_track = None
        asset.save()


def _apply_raw_links(batch, payload, user):
    raw = _assets(batch, [payload["source"]])[0]
    for asset in _assets(batch, payload["assets"]):
        old = list(asset.digitization_sources.filter(is_active=True))
        if old and old[0].source_asset_id == raw.pk:
            continue
        for relation in old:
            relation.is_active = False
            relation.save()
        DigitizationDerivation.objects.create(
            source_asset=raw,
            derived_asset=asset,
            created_by=user,
            note=payload.get("note", ""),
        )
        _event(
            batch,
            asset,
            user,
            MediaAssetEvent.EventType.RAW_LINKED,
            related=raw,
            details={
                "previous": [str(item.source_asset_id) for item in old],
                "note": payload.get("note", ""),
            },
        )


def _apply_recording_links(batch, payload, user, plan):
    affected_recordings = {}
    for row in payload["rows"]:
        asset = _assets(batch, [row["asset"]])[0]
        track = (
            ReleaseTrack.objects.get(pk=row["track"], release=batch.release)
            if row.get("track")
            else None
        )
        recording = (
            Recording.objects.get(pk=row["recording"])
            if row.get("recording")
            else (track.recording if track else None)
        )
        recording = resolve_release_track_recording(
            recording=recording,
            new_recording_title=row.get("new_title", ""),
            force_create=bool(row.get("force_create")),
            duration_ms=asset.technical_metadata.get("duration_ms"),
        )
        asset.recording = recording
        asset.release = None
        asset.release_track = track
        asset.save()
        MusicLibraryEntry.objects.get_or_create(recording=recording)
        _event(
            batch,
            asset,
            user,
            MediaAssetEvent.EventType.RECORDING_LINKED,
            details={
                "track": str(track.pk) if track else None,
                "plan": str(plan.pk),
            },
        )
        affected_recordings[recording.pk] = recording
    for recording in affected_recordings.values():
        selection = RecordingMediaSelection.objects.filter(
            recording=recording
        ).first()
        if selection and selection.selected_master_id:
            continue
        master_ids = list(
            FileAsset.objects.filter(
                recording=recording,
                role=FileAsset.Role.EDITED_WAV_MASTER,
            ).values_list("pk", flat=True)[:2]
        )
        if len(master_ids) == 1:
            select_master(
                recording=recording,
                asset=FileAsset.objects.get(pk=master_ids[0]),
                user=user,
            )


def _register(batch, files, user):
    from datetime import datetime

    for item in files:
        if FileLocation.objects.filter(
            storage_root_key=item["root_key"],
            relative_path=item["path"],
            is_current=True,
        ).exists():
            continue
        asset = FileAsset.objects.create(
            release=batch.release,
            filename=item["filename"],
            role=item["role"],
            mime_type="audio/wav",
            size_bytes=item["size"],
            sha256=item["sha256"],
            technical_metadata=item["technical"],
            metadata_read_at=timezone.now(),
            source_modified_at=datetime.fromisoformat(item["modified"]),
        )
        FileLocation.objects.create(
            asset=asset,
            storage_type=FileLocation.StorageType.LOCAL,
            storage_root_key=item["root_key"],
            relative_path=item["path"],
            is_current=True,
            status=FileLocation.Status.ACTIVE,
            verification_status=FileLocation.VerificationStatus.VERIFIED,
        )
        FileChecksum.objects.create(
            asset=asset,
            sha256=item["sha256"],
            reason=FileChecksum.Reason.VERIFICATION,
        )
        DigitizationFile.objects.create(
            batch=batch, asset=asset, registration_key=item["registration_key"]
        )
        _event(
            batch,
            asset,
            user,
            MediaAssetEvent.EventType.DIGITIZATION_REGISTERED,
        )


def suggest_tracks(asset, tracks):
    """Suggestions only. Conflicting title and position signals stay ambiguous."""
    stem = PurePosixPath(asset.filename).stem
    match = re.match(r"^(?:(\d+)[.-])?([A-Da-d]?)(\d+)[\s._-]*(.*)$", stem)
    candidates = {}
    title = match.group(4) if match else stem
    for track in tracks:
        signals = []
        if match:
            disc, side, number = match.group(1, 2, 3)
            if (
                side
                and track.side.casefold() == side.casefold()
                and track.track_number == int(number)
            ):
                signals.append("side/spornummer")
            elif (
                disc
                and track.disc_number == int(disc)
                and track.track_number == int(number)
            ):
                signals.append("disc/spornummer")
            elif (
                not side and not disc and track.sequence_number == int(number)
            ):
                signals.append("sekvensnummer")
        if title.strip().casefold() == track.display_title.strip().casefold():
            signals.append("tittel")
        if signals:
            candidates[track.pk] = {"track": track, "signals": signals}
    choices = list(candidates.values())
    return {
        "choices": choices,
        "status": (
            "Tvetydig"
            if len(choices) > 1
            else (
                "Entydig forslag"
                if choices and len(choices[0]["signals"]) > 1
                else "Sannsynlig forslag" if choices else "Ingen kandidat"
            )
        ),
    }
