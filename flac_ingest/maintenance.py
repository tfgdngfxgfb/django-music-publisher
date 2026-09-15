"""Conservative maintenance for the FLAC-derived part of Music Library."""

from pathlib import PurePosixPath

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from catalogue.models import (
    DuplicateCandidate,
    ExternalIdentifier,
    Recording,
    RecordingContribution,
    Release,
)
from managed_music.models import ManagedRecording
from media_assets.models import FileAsset, FileLocation
from music_library.models import MusicLibraryEntry
from provenance.models import AssertionDecision, MetadataAssertion, SourceRecord
from rights.models import RightsClaim

from .models import FlacIngestItem, FlacMaintenanceJob
from .services import (
    AUTOMATIC_FLAC_DECISION_NOTE,
    SOURCE_SYSTEM_NAME,
    SourceFileUnavailable,
    apply_item,
    music_root,
    resolve_music_path,
    scan_directory,
)

from media_assets.models import FileDerivation, RadioFlacGeneration, RecordingMediaSelection

def _within_scope(path, relative_root):
    root = PurePosixPath(relative_root or ".")
    value = PurePosixPath(path)
    return (
        root in {PurePosixPath("."), PurePosixPath("")}
        or value == root
        or root in value.parents
    )


def _protection_map(recording_ids):
    ids = set(recording_ids)
    reasons = {recording_id: [] for recording_id in ids}

    def add(values, reason):
        for recording_id in values:
            if recording_id in reasons and reason not in reasons[recording_id]:
                reasons[recording_id].append(reason)

    add(
        ManagedRecording.objects.filter(
            library_entry__recording_id__in=ids
        ).values_list("library_entry__recording_id", flat=True),
        "Innspillingen finnes i Forvaltet musikk.",
    )
    add(
        RightsClaim.objects.filter(recording_id__in=ids).values_list(
            "recording_id", flat=True
        ),
        "Innspillingen har registrert rettighetskunnskap.",
    )
    duplicate_ids = set(
        DuplicateCandidate.objects.filter(
            Q(recording_a_id__in=ids) | Q(recording_b_id__in=ids)
        ).values_list("recording_a_id", "recording_b_id")
    )
    add(
        (value for pair in duplicate_ids for value in pair if value in ids),
        "Innspillingen inngår i en bevart dublettavgjørelse.",
    )
    add(
        RecordingContribution.objects.filter(recording_id__in=ids)
        .filter(
            Q(source_record__isnull=True)
            | ~Q(source_record__source_system__name=SOURCE_SYSTEM_NAME)
        )
        .values_list("recording_id", flat=True),
        "Innspillingen har manuelt eller eksternt vurderte krediteringer.",
    )
    add(
        FileAsset.objects.filter(recording_id__in=ids)
        .exclude(role=FileAsset.Role.RADIO_FLAC)
        .values_list("recording_id", flat=True),
        "Innspillingen har andre filressurser enn radio-FLAC.",
    )
    add(
        RecordingMediaSelection.objects.filter(
            recording_id__in=ids
        ).values_list("recording_id", flat=True),
        "Innspillingen har beskyttede valg av master eller radiofil.",
    )
    add(
        FileDerivation.objects.filter(
            source_asset__recording_id__in=ids
        ).values_list("source_asset__recording_id", flat=True),
        "Innspillingen har dokumentert filavledning.",
    )
    add(
        RadioFlacGeneration.objects.filter(recording_id__in=ids).values_list(
            "recording_id", flat=True
        ),
        "Innspillingen har genererings- og verifikasjonshistorikk.",
    )
    manual_assertion_ids = (
        MetadataAssertion.objects.filter(
            entity_type=MetadataAssertion.EntityType.RECORDING,
            entity_uuid__in=ids,
        )
        .exclude(source_record__source_system__name=SOURCE_SYSTEM_NAME)
        .values_list("entity_uuid", flat=True)
    )
    add(
        manual_assertion_ids,
        "Innspillingen har en manuell eller ekstern kildepåstand.",
    )
    manual_entry_recordings = MusicLibraryEntry.objects.filter(
        recording_id__in=ids,
        id__in=(
            MetadataAssertion.objects.filter(
                entity_type=MetadataAssertion.EntityType.MUSIC_LIBRARY_ENTRY,
            )
            .exclude(source_record__source_system__name=SOURCE_SYSTEM_NAME)
            .values("entity_uuid")
        ),
    ).values_list("recording_id", flat=True)
    add(
        manual_entry_recordings,
        "Musikkarkivposten har en manuell eller ekstern kildepåstand.",
    )
    decided_recordings = (
        AssertionDecision.objects.filter(
            assertion__entity_type=MetadataAssertion.EntityType.RECORDING,
            assertion__entity_uuid__in=ids,
        )
        .exclude(note=AUTOMATIC_FLAC_DECISION_NOTE)
        .values_list("assertion__entity_uuid", flat=True)
    )
    add(
        decided_recordings,
        "En kildepåstand om innspillingen er vurdert av en bruker.",
    )
    decided_entries = MusicLibraryEntry.objects.filter(
        recording_id__in=ids,
        id__in=AssertionDecision.objects.filter(
            assertion__entity_type=MetadataAssertion.EntityType.MUSIC_LIBRARY_ENTRY,
        )
        .exclude(note=AUTOMATIC_FLAC_DECISION_NOTE)
        .values("assertion__entity_uuid"),
    ).values_list("recording_id", flat=True)
    add(
        decided_entries,
        "En kildepåstand om radiometadata er vurdert av en bruker.",
    )
    add(
        Recording.objects.filter(id__in=ids)
        .filter(
            Q(version_designation__gt="")
            | Q(metadata_status=Recording.Status.REVIEWED)
        )
        .values_list("id", flat=True),
        "Innspillingen har manuelt kataloginnhold eller er kontrollert.",
    )
    add(
        MusicLibraryEntry.objects.filter(recording_id__in=ids)
        .exclude(notes="")
        .values_list("recording_id", flat=True),
        "Musikkarkivposten har manuelle merknader.",
    )
    add(
        FlacIngestItem.objects.filter(
            recording_id__in=ids, reviewed_by__isnull=False
        ).values_list("recording_id", flat=True),
        "En tidligere ingestkonflikt er vurdert av en bruker.",
    )

    current_isrc = {
        recording_id: value
        for recording_id, value in ExternalIdentifier.objects.filter(
            recording_id__in=ids,
            scheme=ExternalIdentifier.Scheme.ISRC,
        ).values_list("recording_id", "normalized_value")
    }
    for recording_id, parsed in FlacIngestItem.objects.filter(
        recording_id__in=ids, applied_at__isnull=False
    ).values_list("recording_id", "parsed_metadata"):
        source_isrc = (parsed or {}).get("isrc")
        if (
            source_isrc
            and current_isrc.get(recording_id, "").replace("-", "")
            != str(source_isrc).replace("-", "").upper()
        ):
            add(
                [recording_id],
                "Et kjent ISRC-avvik må bevares for manuell kontroll.",
            )

    latest_ingest = {}
    for recording_id, applied_at in (
        FlacIngestItem.objects.filter(
            recording_id__in=ids, applied_at__isnull=False
        )
        .order_by("recording_id", "applied_at")
        .values_list("recording_id", "applied_at")
    ):
        latest_ingest[recording_id] = applied_at
    for recording_id, updated_at in Recording.objects.filter(
        id__in=ids
    ).values_list("id", "updated_at"):
        if (
            not latest_ingest.get(recording_id)
            or updated_at > latest_ingest[recording_id]
        ):
            add(
                [recording_id],
                "Innspillingen er endret etter siste FLAC-innlesing.",
            )
    for recording_id, updated_at in MusicLibraryEntry.objects.filter(
        recording_id__in=ids
    ).values_list("recording_id", "updated_at"):
        if (
            not latest_ingest.get(recording_id)
            or updated_at > latest_ingest[recording_id]
        ):
            add(
                [recording_id],
                "Radioposten er endret etter siste FLAC-innlesing.",
            )
    for recording_id, updated_at in RecordingContribution.objects.filter(
        recording_id__in=ids
    ).values_list("recording_id", "updated_at"):
        if (
            not latest_ingest.get(recording_id)
            or updated_at > latest_ingest[recording_id]
        ):
            add(
                [recording_id],
                "En kreditering er endret etter siste FLAC-innlesing.",
            )
    return reasons


def _radio_rows(relative_root=".", recording_id=None):
    recordings_query = Recording.objects.filter(
        Q(file_assets__role=FileAsset.Role.RADIO_FLAC)
        | Q(music_library_entry__isnull=False)
    )
    if recording_id:
        recordings_query = recordings_query.filter(pk=recording_id)
    prefix = str(PurePosixPath(relative_root or "."))
    if prefix not in {"", "."}:
        recordings_query = recordings_query.filter(
            Q(file_assets__locations__relative_path=prefix)
            | Q(file_assets__locations__relative_path__startswith=f"{prefix}/")
        )
    recordings = list(
        recordings_query.distinct()
        .select_related("music_library_entry")
        .prefetch_related("file_assets__locations")
    )
    protection = _protection_map(recording.pk for recording in recordings)
    root = music_root()
    rows = []
    for recording in recordings:
        assets = [
            asset
            for asset in recording.file_assets.all()
            if asset.role == FileAsset.Role.RADIO_FLAC
        ]
        locations = [
            location
            for asset in assets
            for location in asset.locations.all()
            if location.is_current
            and _within_scope(location.relative_path, relative_root)
        ]
        existing = []
        missing = []
        for location in locations:
            path = root.joinpath(*PurePosixPath(location.relative_path).parts)
            (existing if path.is_file() else missing).append(location.relative_path)
        rows.append(
            {
                "recording_id": str(recording.pk),
                "entry_id": (
                    str(recording.music_library_entry.pk)
                    if hasattr(recording, "music_library_entry")
                    else ""
                ),
                "title": recording.title,
                "existing_paths": existing,
                "missing_paths": missing,
                "protected_reasons": protection.get(recording.pk, []),
            }
        )
    return rows


def create_cleanup_preview(*, user, relative_root=".", recording_id=None):
    resolve_music_path(relative_root)
    rows = _radio_rows(relative_root, recording_id=recording_id)
    items = []
    for row in rows:
        if not recording_id and not row["missing_paths"] and row["existing_paths"]:
            continue
        removable = bool(
            row["entry_id"]
            and not row["protected_reasons"]
            and (recording_id or not row["existing_paths"])
        )
        items.append({**row, "removable": removable})
    stats = {
        "missing_locations": sum(len(item["missing_paths"]) for item in items),
        "assets_without_working_location": sum(
            not item["existing_paths"] for item in items
        ),
        "entries_without_active_file": sum(
            bool(item["entry_id"]) and not item["existing_paths"] for item in items
        ),
        "safe_to_remove": sum(item["removable"] for item in items),
        "protected": sum(bool(item["protected_reasons"]) for item in items),
        "orphan_flac_sources": SourceRecord.objects.filter(
            source_system__name=SOURCE_SYSTEM_NAME,
            flac_ingest_item__isnull=True,
        ).count(),
        "empty_releases": Release.objects.filter(tracks__isnull=True).count(),
    }
    return FlacMaintenanceJob.objects.create(
        kind=FlacMaintenanceJob.Kind.CLEANUP,
        relative_root=relative_root,
        plan={
            "stats": stats,
            "items": items,
            "recording_id": str(recording_id or ""),
        },
        created_by=user,
    )


def create_rebuild_preview(*, user, relative_root="."):
    _root, folder = resolve_music_path(relative_root)
    flac_count = sum(
        1
        for path in folder.rglob("*")
        if path.is_file() and path.suffix.casefold() == ".flac"
    )
    rows = _radio_rows(relative_root)
    regenerable = [
        row for row in rows if not row["protected_reasons"] and row["existing_paths"]
    ]
    protected = [row for row in rows if row["protected_reasons"]]
    stats = {
        "flac_files": flac_count,
        "regenerable_entries": sum(bool(row["entry_id"]) for row in regenerable),
        "recordings_reused": len(regenerable),
        "protected_recordings": len(protected),
        "managed_recordings_changed": 0,
    }
    return FlacMaintenanceJob.objects.create(
        kind=FlacMaintenanceJob.Kind.REBUILD,
        relative_root=relative_root,
        plan={"stats": stats, "regenerable": regenerable, "protected": protected},
        created_by=user,
    )


def _start(job, expected_kind, *, user):
    job = FlacMaintenanceJob.objects.select_for_update().get(pk=job.pk)
    if job.kind != expected_kind or job.status != FlacMaintenanceJob.Status.PREVIEW:
        raise ValidationError("Vedlikeholdsplanen kan ikke utføres i denne tilstanden.")
    job.status = FlacMaintenanceJob.Status.RUNNING
    job.executed_by = user
    job.started_at = timezone.now()
    job.error = ""
    job.save(update_fields=("status", "executed_by", "started_at", "error"))
    return job


def _mark_failed(job, error, **partial_result):
    job.status = FlacMaintenanceJob.Status.FAILED
    job.error = f"{type(error).__name__}: {error}"[:2000]
    job.result = partial_result
    job.completed_at = timezone.now()
    job.save(update_fields=("status", "error", "result", "completed_at"))


def execute_cleanup(job, *, user):
    with transaction.atomic():
        job = _start(job, FlacMaintenanceJob.Kind.CLEANUP, user=user)
    removed = blocked = 0
    for planned in job.plan.get("items", []):
        if not planned.get("removable"):
            blocked += 1
            continue
        try:
            with transaction.atomic():
                entry = (
                    MusicLibraryEntry.objects.select_for_update()
                    .select_related("recording")
                    .get(pk=planned["entry_id"])
                )
                reasons = _protection_map([entry.recording_id])[entry.recording_id]
                if (
                    reasons
                    or ManagedRecording.objects.filter(library_entry=entry).exists()
                ):
                    blocked += 1
                    continue
                targeted_removal = bool(job.plan.get("recording_id"))
                for asset in entry.recording.file_assets.filter(
                    role=FileAsset.Role.RADIO_FLAC
                ):
                    for location in asset.locations.filter(is_current=True):
                        _root, path = resolve_music_path(location.relative_path)
                        if path.is_file() and not targeted_removal:
                            raise ValidationError(
                                "Filen finnes igjen; posten ble ikke fjernet."
                            )
                        if targeted_removal:
                            location.status = FileLocation.Status.HISTORICAL
                            location.is_current = False
                            location.ended_at = timezone.now()
                            location.save(
                                update_fields=("status", "is_current", "ended_at")
                            )
                        else:
                            location.status = FileLocation.Status.MISSING
                            location.save(update_fields=("status",))
                entry.delete()
                removed += 1
        except (MusicLibraryEntry.DoesNotExist, ValidationError):
            blocked += 1
        except Exception as error:
            _mark_failed(
                job,
                error,
                removed_entries=removed,
                preserved_or_blocked=blocked,
            )
            raise
    job.status = (
        FlacMaintenanceJob.Status.COMPLETED
        if not blocked
        else FlacMaintenanceJob.Status.PARTIAL
    )
    job.result = {"removed_entries": removed, "preserved_or_blocked": blocked}
    job.completed_at = timezone.now()
    job.save(update_fields=("status", "result", "completed_at"))
    return job


def execute_rebuild(job, *, user):
    with transaction.atomic():
        job = _start(job, FlacMaintenanceJob.Kind.REBUILD, user=user)
    planned_ids = {row["recording_id"] for row in job.plan.get("regenerable", [])}
    try:
        batch = scan_directory(
            relative_root=job.relative_root,
            recursive=True,
            user=user,
            allow_uuid_recovery=False,
            force_read=True,
        )
    except Exception as error:
        _mark_failed(job, error, regenerated=0, new=0, preserved=0, failed=1)
        raise
    reset_ids = set()
    rebuilt = new = failed = skipped = 0
    for item in batch.items.select_related(
        "recording", "file_asset__recording"
    ).order_by("relative_path"):
        recording = item.recording or (
            item.file_asset.recording if item.file_asset_id else None
        )
        recording_id = str(recording.pk) if recording else ""
        if recording and recording_id not in planned_ids:
            skipped += 1
            continue
        if not item.can_apply:
            failed += item.action not in {FlacIngestItem.Action.UNCHANGED}
            continue
        try:
            with transaction.atomic():
                if recording and recording_id not in reset_ids:
                    reasons = _protection_map([recording.pk])[recording.pk]
                    if reasons:
                        skipped += 1
                        continue
                    MusicLibraryEntry.objects.filter(recording=recording).delete()
                    RecordingContribution.objects.filter(
                        recording=recording,
                        source_record__source_system__name=SOURCE_SYSTEM_NAME,
                    ).delete()
                    reset_ids.add(recording_id)
                was_new = recording is None
                apply_item(item, user=user)
                new += int(was_new)
                rebuilt += int(not was_new)
        except (ValidationError, IntegrityError, SourceFileUnavailable) as error:
            failed += 1
            item.refresh_from_db()
            item.action = FlacIngestItem.Action.CONFLICT
            item.messages = list(getattr(error, "messages", [str(error)]))
            item.save(update_fields=("action", "messages"))
        except Exception as error:
            _mark_failed(
                job,
                error,
                ingest_batch_id=str(batch.pk),
                regenerated=rebuilt,
                new=new,
                preserved=skipped,
                failed=failed + 1,
            )
            raise
    batch.refresh_from_db()
    job.status = (
        FlacMaintenanceJob.Status.COMPLETED
        if not failed
        else FlacMaintenanceJob.Status.PARTIAL
    )
    job.result = {
        "ingest_batch_id": str(batch.pk),
        "regenerated": rebuilt,
        "new": new,
        "preserved": skipped,
        "failed": failed,
    }
    job.completed_at = timezone.now()
    job.save(update_fields=("status", "result", "completed_at"))
    return job
