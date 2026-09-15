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


def _state(batch):
    """Include selections on this Release, even when edited in another batch."""
    files = list(batch.files.select_related("asset").order_by("pk"))
    recording_ids = set(
        batch.release.tracks.values_list("recording_id", flat=True)
    )
    recording_ids.update(
        item.asset.recording_id for item in files if item.asset.recording_id
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


def inspect_folder(*, root_key, relative_path, role):
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
    if batch.status != DigitizationBatch.Status.OPEN:
        raise ValidationError("Digitaliseringen er avsluttet.")
    if operation == "register":
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
    elif operation == "select_master":
        assets = _assets(batch, payload["assets"])
        seen = set()
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
                f"{asset.recording.title} → valgt master {asset.filename}"
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
        expected_state=_state(batch),
        consequences=consequences,
        created_by=user,
    )


def preview_registration(*, batch, root_key, relative_path, role, user):
    require_operator(user)
    files = inspect_folder(
        root_key=root_key, relative_path=relative_path, role=role
    )
    return preview_operation(
        batch=batch, operation="register", payload={"files": files}, user=user
    )


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
        if _state(batch) != plan.expected_state:
            raise ValidationError(
                "Arbeidsgrunnlaget er endret siden forhåndsvisningen. Lag en ny forhåndsvisning."
            )
        _validate(batch, plan.operation, plan.payload)
        if plan.operation == "register":
            _register(batch, plan.payload["files"], user)
        elif plan.operation == "raw_link":
            raw = _assets(batch, [plan.payload["source"]])[0]
            for asset in _assets(batch, plan.payload["assets"]):
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
                    note=plan.payload.get("note", ""),
                )
                _event(
                    batch,
                    asset,
                    user,
                    MediaAssetEvent.EventType.RAW_LINKED,
                    related=raw,
                    details={
                        "previous": [
                            str(item.source_asset_id) for item in old
                        ],
                        "note": plan.payload.get("note", ""),
                    },
                )
        elif plan.operation == "recording_link":
            for row in plan.payload["rows"]:
                asset = _assets(batch, [row["asset"]])[0]
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
        elif plan.operation == "select_master":
            for asset in sorted(
                _assets(batch, plan.payload["assets"]),
                key=lambda item: str(item.recording_id),
            ):
                select_master(
                    recording=asset.recording, asset=asset, user=user
                )
        batch.save()  # Invalidate every older preview from this batch.
        plan.applied_at = timezone.now()
        plan.save()
        return plan


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
