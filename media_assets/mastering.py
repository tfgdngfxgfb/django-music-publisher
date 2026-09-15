"""Controlled, explicit master-to-radio-FLAC lifecycle operations."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath

import numpy as np
import soundfile as sf
from django.conf import settings
from django.core.exceptions import (
    ImproperlyConfigured,
    PermissionDenied,
    ValidationError,
)
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from mutagen.flac import FLAC

from catalogue.models import (
    ExternalIdentifier,
    Recording,
    RecordingContribution,
)
from flac_ingest.adapter import CATALOGUE_WRITE_TAGS, RADIO_WRITE_TAGS
from managed_music.models import ManagedRecording

from .models import (
    FileAsset,
    FileChecksum,
    FileDerivation,
    FileLocation,
    MediaAssetEvent,
    RadioFlacGeneration,
    RecordingMediaSelection,
)
from .playback import RadioPlaybackStatus, resolve_current_radio_asset
from .storage import (
    MUSIC_LIBRARY_ROOT,
    StorageFileUnavailable,
    get_storage_root,
    open_for_read,
    resolve_location,
    resolve_storage_path,
    validate_readable_location,
)

SUPPORTED_MASTER_SUBTYPES = {"PCM_16": 16, "PCM_24": 24}
SUPPORTED_MASTER_FORMATS = {"WAV", "WAVEX"}
GENERATED_TAGS = CATALOGUE_WRITE_TAGS | RADIO_WRITE_TAGS


@dataclass(frozen=True)
class MasterInspection:
    root_key: str
    relative_path: str
    filename: str
    format: str
    subtype: str
    sample_rate: int
    bit_depth: int | None
    channels: int
    frames: int
    duration_ms: int
    size_bytes: int
    sha256: str
    source_modified_at: datetime
    supported: bool
    message: str

    @property
    def technical_metadata(self):
        return {
            "container": self.format,
            "codec": self.subtype,
            "sample_format": self.subtype,
            "sample_rate": self.sample_rate,
            "bits_per_sample": self.bit_depth,
            "channels": self.channels,
            "frames": self.frames,
            "duration_ms": self.duration_ms,
        }


def _stream_sha256(handle):
    digest = sha256()
    try:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
        return digest.hexdigest()
    finally:
        handle.close()


def inspect_master(*, root_key, relative_path):
    resolved = validate_readable_location(
        resolve_storage_path(
            relative_path, root_key=root_key, require_root=True
        )
    )
    try:
        with open_for_read(resolved) as handle:
            info = sf.info(handle)
        with open_for_read(resolved) as handle:
            digest = _stream_sha256(handle)
        stat = resolved.server_path.stat()
    except (OSError, RuntimeError, ValueError) as error:
        raise ValidationError(
            f"Masterfilen kunne ikke inspiseres: {error}"
        ) from error
    bit_depth = SUPPORTED_MASTER_SUBTYPES.get(info.subtype)
    supported = (
        info.format in SUPPORTED_MASTER_FORMATS and bit_depth is not None
    )
    message = (
        "Masteren kan konverteres tapsfritt til FLAC uten DSP."
        if supported
        else (
            "Denne masteren kan ikke konverteres til FLAC uten endring av "
            "lydrepresentasjonen. Automatisk generering er blokkert."
        )
    )
    return MasterInspection(
        root_key=root_key,
        relative_path=str(PurePosixPath(relative_path.replace("\\", "/"))),
        filename=PurePosixPath(relative_path.replace("\\", "/")).name,
        format=info.format,
        subtype=info.subtype,
        sample_rate=info.samplerate,
        bit_depth=bit_depth,
        channels=info.channels,
        frames=info.frames,
        duration_ms=round(info.duration * 1000),
        size_bytes=stat.st_size,
        sha256=digest,
        source_modified_at=datetime.fromtimestamp(
            stat.st_mtime, tz=datetime_timezone.utc
        ),
        supported=supported,
        message=message,
    )


def register_master(*, recording, root_key, relative_path, user):
    inspection = inspect_master(root_key=root_key, relative_path=relative_path)
    with transaction.atomic():
        existing_location = (
            FileLocation.objects.select_related("asset")
            .filter(
                storage_root_key=root_key,
                relative_path=inspection.relative_path,
                is_current=True,
            )
            .first()
        )
        if existing_location:
            asset = existing_location.asset
            if asset.recording_id != recording.pk:
                raise ValidationError(
                    "Filen er allerede knyttet til en annen innspilling."
                )
            if asset.role != FileAsset.Role.EDITED_WAV_MASTER:
                raise ValidationError(
                    "Filen er allerede registrert med en annen rolle."
                )
            return asset
        asset = FileAsset(
            recording=recording,
            filename=inspection.filename,
            role=FileAsset.Role.EDITED_WAV_MASTER,
            mime_type="audio/wav",
            size_bytes=inspection.size_bytes,
            sha256=inspection.sha256,
            technical_metadata=inspection.technical_metadata,
            metadata_read_at=timezone.now(),
            source_modified_at=inspection.source_modified_at,
            sync_status=FileAsset.SyncStatus.NOT_APPLICABLE,
        )
        asset.full_clean()
        asset.save()
        location = FileLocation(
            asset=asset,
            storage_type=(
                FileLocation.StorageType.NAS
                if root_key == MUSIC_LIBRARY_ROOT
                else FileLocation.StorageType.LOCAL
            ),
            storage_root_key=root_key,
            relative_path=inspection.relative_path,
            status=FileLocation.Status.ACTIVE,
            is_current=True,
            verification_status=FileLocation.VerificationStatus.VERIFIED,
        )
        location.full_clean()
        location.save()
        FileChecksum.objects.create(
            asset=asset,
            sha256=inspection.sha256,
            reason=FileChecksum.Reason.VERIFICATION,
        )
        MediaAssetEvent.objects.create(
            recording=recording,
            asset=asset,
            event_type=MediaAssetEvent.EventType.MASTER_REGISTERED,
            actor=user,
            details={
                "root_key": root_key,
                "relative_path": inspection.relative_path,
            },
        )
    return asset


@transaction.atomic
def select_master(*, recording, asset, user):
    recording = Recording.objects.select_for_update().get(pk=recording.pk)
    asset = FileAsset.objects.select_for_update().get(pk=asset.pk)
    if (
        asset.recording_id != recording.pk
        or asset.role != FileAsset.Role.EDITED_WAV_MASTER
    ):
        raise ValidationError(
            "Valgt fil er ikke en master for denne innspillingen."
        )
    (
        selection,
        _,
    ) = RecordingMediaSelection.objects.select_for_update().get_or_create(
        recording=recording
    )
    selection.selected_master = asset
    selection.selected_master_by = user
    selection.selected_master_at = timezone.now()
    selection.full_clean()
    selection.save()
    MediaAssetEvent.objects.create(
        recording=recording,
        asset=asset,
        event_type=MediaAssetEvent.EventType.MASTER_SELECTED,
        actor=user,
    )
    return selection


def _credit_values(recording, roles):
    return list(
        dict.fromkeys(
            contribution.display_credit
            for contribution in recording.contributions.all()
            if contribution.role in roles
            and contribution.display_credit != "Uavklart"
        )
    )


def _clean_tags(tags):
    cleaned = {}
    for key, value in tags.items():
        if value in (None, "", []):
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        values = [str(item) for item in values if str(item).strip()]
        if values:
            cleaned[key] = values
    return cleaned


def _db_catalogue_tags(recording):
    identifier = next(
        (
            item
            for item in recording.identifiers.all()
            if item.scheme == ExternalIdentifier.Scheme.ISRC
        ),
        None,
    )
    return _clean_tags(
        {
            "TITLE": recording.title,
            "ARTIST": _credit_values(
                recording,
                {
                    RecordingContribution.Role.PRIMARY,
                    RecordingContribution.Role.FEATURED,
                },
            ),
            "COMPOSER": _credit_values(
                recording, {RecordingContribution.Role.COMPOSER}
            ),
            "LYRICIST": _credit_values(
                recording, {RecordingContribution.Role.LYRICIST}
            ),
            "ARRANGER": _credit_values(
                recording, {RecordingContribution.Role.ARRANGER}
            ),
            "ISRC": identifier.normalized_value if identifier else "",
            "P7UUID": str(recording.pk),
        }
    )


def _snapshot_catalogue_tags(snapshot, recording):
    parsed = snapshot.parsed
    return _clean_tags(
        {
            "TITLE": parsed.get("title") or recording.title,
            "ARTIST": parsed.get("artists"),
            "COMPOSER": parsed.get("composers"),
            "LYRICIST": parsed.get("lyricists"),
            "ARRANGER": parsed.get("arrangers"),
            "ISRC": parsed.get("isrc"),
            "ALBUM": parsed.get("album"),
            "ALBUMARTIST": parsed.get("album_artist"),
            "TRACKNUMBER": parsed.get("track_number"),
            "DISCNUMBER": parsed.get("disc_number"),
            "DATE": parsed.get("date"),
            "BARCODE": parsed.get("barcode"),
            "CATALOGNUMBER": parsed.get("catalogue_number"),
            "P7UUID": str(recording.pk),
        }
    )


def _radio_tags(snapshot):
    parsed = snapshot.parsed
    rotation = {
        "suitable": "Rotasjonsverdig",
        "not_suitable": "Ikke Rotasjonsverdig",
        "unassessed": "Ikke vurdert",
    }.get(parsed.get("rotation_suitability"), "")
    return _clean_tags(
        {
            "GENRE": parsed.get("genre_values") or parsed.get("genre"),
            "LANGUAGE": parsed.get("language"),
            "RATING": parsed.get("energy"),
            "KANAL": parsed.get("channels"),
            "TARGET": parsed.get("target_audiences"),
            "GENDER": parsed.get("gender"),
            "ROTATION": rotation,
        }
    )


def _read_radio_source(recording):
    from flac_ingest.adapter import read_flac

    resolution = resolve_current_radio_asset(recording, verify_file=True)
    if resolution.status == RadioPlaybackStatus.AMBIGUOUS:
        raise ValidationError(
            "Ingen entydig gjeldende radiofil. Kontroller filene før generering."
        )
    if resolution.status != RadioPlaybackStatus.AVAILABLE:
        raise ValidationError(
            "En tilgjengelig gjeldende radiofil kreves som metadatakilde."
        )
    return resolution.asset, read_flac(resolution.path)


def _metadata_diff(snapshot, expected):
    existing = {
        str(key).upper(): [str(item) for item in values]
        for key, values in snapshot.raw_tags.items()
    }
    return [
        {"tag": key, "old": existing.get(key, []), "new": value}
        for key, value in expected.items()
        if existing.get(key, []) != value
    ]


def _target_relative_path(recording, *, inspection, expected_tags):
    folder = str(
        getattr(settings, "P7_GENERATED_MEDIA_RELATIVE_ROOT", "P7-generert")
    )
    stem = slugify(recording.title, allow_unicode=True) or "innspilling"
    tag_fingerprint = repr(
        sorted((key, tuple(values)) for key, values in expected_tags.items())
    )
    fingerprint = sha256(
        (f"{recording.pk}:{inspection.sha256}:{tag_fingerprint}").encode(
            "utf-8"
        )
    ).hexdigest()[:16]
    return str(
        PurePosixPath(folder, str(recording.pk), f"{stem}-{fingerprint}.flac")
    )


def _validated_target_relative_path(recording, value=None):
    if not value:
        raise ValidationError("Målfil mangler fra genereringsplanen.")
    proposed = str(value).replace("\\", "/")
    target = PurePosixPath(proposed)
    folder = str(
        getattr(settings, "P7_GENERATED_MEDIA_RELATIVE_ROOT", "P7-generert")
    )
    expected_parent = PurePosixPath(folder, str(recording.pk))
    if target.parent != expected_parent or target.suffix.casefold() != ".flac":
        raise ValidationError(
            "Målfilen må ligge i innspillingens genererte FLAC-mappe."
        )
    return str(target)


def build_generation_preview(
    *, recording, target_relative_path=None, allow_existing_target=False
):
    selection = (
        RecordingMediaSelection.objects.select_related("selected_master")
        .filter(recording=recording)
        .first()
    )
    if not selection or not selection.selected_master_id:
        raise ValidationError(
            "Velg en autoritativ master før radio-FLAC forberedes."
        )
    master = selection.selected_master
    locations = list(
        master.locations.filter(
            is_current=True, status=FileLocation.Status.ACTIVE
        )
    )
    if not locations:
        raise ValidationError("Valgt master har ingen aktiv filplassering.")
    if len(locations) > 1:
        raise ValidationError(
            "Valgt master har flere aktive plasseringer. Avklar plasseringen først."
        )
    location = locations[0]
    inspection = inspect_master(
        root_key=location.storage_root_key or MUSIC_LIBRARY_ROOT,
        relative_path=location.relative_path,
    )
    if not inspection.supported:
        raise ValidationError(inspection.message)
    radio_asset, snapshot = _read_radio_source(recording)
    is_managed = ManagedRecording.objects.filter(
        library_entry__recording=recording
    ).exists()
    catalogue = (
        _db_catalogue_tags(recording)
        if is_managed
        else _snapshot_catalogue_tags(snapshot, recording)
    )
    expected = {
        **catalogue,
        **_radio_tags(snapshot),
        "P7UUID": [str(recording.pk)],
    }
    target_root_key = getattr(
        settings, "P7_GENERATED_MEDIA_ROOT_KEY", "generated_media"
    )
    target_relative_path = _validated_target_relative_path(
        recording,
        target_relative_path
        or _target_relative_path(
            recording,
            inspection=inspection,
            expected_tags=expected,
        ),
    )
    target = resolve_storage_path(
        target_relative_path, root_key=target_root_key
    )
    target_exists = target.server_path.exists()
    if target_exists and not allow_existing_target:
        raise ValidationError("Planlagt målfil finnes allerede.")
    return {
        "master": master,
        "master_location": location,
        "inspection": inspection,
        "radio_source": radio_asset,
        "expected_tags": expected,
        "metadata_diff": _metadata_diff(snapshot, expected),
        "target_root_key": target_root_key,
        "target_relative_path": target_relative_path,
        "target_client_path": target.client_path,
        "target_root_read_only": target.root.read_only,
        "target_exists": target_exists,
        "is_managed": is_managed,
    }


def create_generation_plan(*, recording, user, target_relative_path=None):
    preview = build_generation_preview(
        recording=recording,
        target_relative_path=target_relative_path,
        allow_existing_target=True,
    )
    lookup = {
        "target_root_key": preview["target_root_key"],
        "target_relative_path": preview["target_relative_path"],
    }
    try:
        plan, created = RadioFlacGeneration.objects.get_or_create(
            **lookup,
            defaults={
                "recording": recording,
                "master_asset": preview["master"],
                "radio_metadata_source": preview["radio_source"],
                "technical_plan": preview["inspection"].technical_metadata,
                "expected_tags": preview["expected_tags"],
                "metadata_diff": preview["metadata_diff"],
                "status": RadioFlacGeneration.Status.PLANNED,
                "created_by": user,
            },
        )
    except ValidationError:
        # CanonicalModel validates uniqueness before INSERT. A concurrent
        # transaction may therefore surface model validation rather than the
        # IntegrityError that get_or_create normally retries. Re-read the
        # deterministic target and apply the same semantic equality check.
        plan = RadioFlacGeneration.objects.filter(**lookup).first()
        if plan is None:
            raise
        created = False
    if not created:
        matches = (
            plan.recording_id == recording.pk
            and plan.master_asset_id == preview["master"].pk
            and plan.radio_metadata_source_id == preview["radio_source"].pk
            and plan.technical_plan == preview["inspection"].technical_metadata
            and plan.expected_tags == preview["expected_tags"]
        )
        if not matches:
            raise ValidationError(
                "Målfilen tilhører en annen genereringsplan."
            )
        return plan
    if preview["target_exists"]:
        plan.delete()
        raise ValidationError(
            "Planlagt målfil finnes uten en registrert kandidat. Kontroller "
            "genereringsplanen før ny kjøring."
        )
    return plan


def _pcm_digest(path):
    digest = sha256()
    with sf.SoundFile(path, mode="r") as audio:
        while True:
            block = audio.read(65536, dtype="int32", always_2d=True)
            if not len(block):
                break
            digest.update(np.asarray(block, dtype="<i4").tobytes(order="C"))
    return digest.hexdigest()


def _encode_lossless(source_path, target_path, subtype):
    with sf.SoundFile(source_path, mode="r") as source, sf.SoundFile(
        target_path,
        mode="w",
        samplerate=source.samplerate,
        channels=source.channels,
        format="FLAC",
        subtype=subtype,
    ) as target:
        while True:
            block = source.read(65536, dtype="int32", always_2d=True)
            if not len(block):
                break
            target.write(block)


def _write_and_verify_tags(path, expected):
    if not getattr(settings, "P7_ALLOW_FILE_WRITES", False):
        raise PermissionDenied(
            "Filskriving er deaktivert av P7_ALLOW_FILE_WRITES-policyen."
        )
    audio = FLAC(path)
    if audio.tags:
        audio.clear()
    for tag, values in expected.items():
        if tag not in GENERATED_TAGS:
            raise ValidationError(
                f"Taggen {tag} er ikke tillatt i generert radio-FLAC."
            )
        audio[tag] = [str(value) for value in values]
    audio.save()
    # Verify through the same adapter that ingest/re-scan uses.  Comparing the
    # raw allow-listed values still catches writer loss while the adapter read
    # also proves that the generated file remains valid input to the catalogue.
    from flac_ingest.adapter import read_flac

    roundtrip = read_flac(path)
    actual = {
        str(key).upper(): [str(item) for item in values]
        for key, values in roundtrip.raw_tags.items()
    }
    expected_normalized = {
        key: [str(item) for item in values] for key, values in expected.items()
    }
    if actual != expected_normalized:
        raise ValidationError(
            "Metadata round-trip stemmer ikke med forhåndsvisningen."
        )
    return actual


def generate_candidate(*, generation, user):
    if not getattr(settings, "P7_ALLOW_FILE_WRITES", False):
        raise PermissionDenied(
            "Filskriving er deaktivert av P7_ALLOW_FILE_WRITES-policyen."
        )
    generation = RadioFlacGeneration.objects.select_related(
        "recording", "master_asset", "radio_metadata_source"
    ).get(pk=generation.pk)
    if generation.status in {
        RadioFlacGeneration.Status.VERIFIED,
        RadioFlacGeneration.Status.ACTIVATED,
    }:
        return generation
    if generation.status != RadioFlacGeneration.Status.PLANNED:
        raise ValidationError("Bare en planlagt generering kan kjøres.")
    selection = RecordingMediaSelection.objects.filter(
        recording=generation.recording
    ).first()
    if (
        not selection
        or selection.selected_master_id != generation.master_asset_id
    ):
        raise ValidationError(
            "Valgt master er endret. Lag en ny forhåndsvisning."
        )
    master_locations = list(
        generation.master_asset.locations.filter(
            is_current=True, status=FileLocation.Status.ACTIVE
        )
    )
    if not master_locations:
        raise StorageFileUnavailable("Valgt master er ikke tilgjengelig.")
    if len(master_locations) > 1:
        raise ValidationError("Valgt master har flere aktive plasseringer.")
    master_location = master_locations[0]
    master_resolved = validate_readable_location(
        resolve_location(master_location)
    )
    before_master_hash = generation.master_asset.sha256 or _stream_sha256(
        open_for_read(master_resolved)
    )
    target = resolve_storage_path(
        generation.target_relative_path,
        root_key=generation.target_root_key,
        require_root=True,
    )
    if target.root.read_only:
        raise PermissionDenied("Målroten er konfigurert som read-only.")
    if target.server_path.exists():
        raise ValidationError("Målfilen finnes allerede. Lag en ny plan.")
    target.server_path.parent.mkdir(parents=True, exist_ok=True)
    # Resolve once more after creating the parent. This catches a configured
    # path that has become a symlink escape before the first write is opened.
    target = resolve_storage_path(
        generation.target_relative_path,
        root_key=generation.target_root_key,
        require_root=True,
    )
    if target.server_path.exists():
        raise ValidationError("Målfilen finnes allerede. Lag en ny plan.")
    # Claim the plan in a short transaction. Canonical models deliberately
    # reject QuerySet.update(), so use the normal validated save path while
    # holding a row lock. The expensive encoding happens after the lock is
    # released; other callers then observe GENERATING instead of duplicating it.
    with transaction.atomic():
        claimed_generation = (
            RadioFlacGeneration.objects.select_for_update().get(
                pk=generation.pk
            )
        )
        if claimed_generation.status in {
            RadioFlacGeneration.Status.VERIFIED,
            RadioFlacGeneration.Status.ACTIVATED,
        }:
            return claimed_generation
        if claimed_generation.status != RadioFlacGeneration.Status.PLANNED:
            raise ValidationError(
                "Genereringen er allerede startet eller kan ikke kjøres på nytt."
            )
        claimed_generation.status = RadioFlacGeneration.Status.GENERATING
        claimed_generation.save(update_fields=["status", "updated_at"])
    generation.status = RadioFlacGeneration.Status.GENERATING
    temporary = None
    try:
        fd, temporary_name = tempfile.mkstemp(
            prefix=".p7-radio-", suffix=".flac", dir=target.server_path.parent
        )
        os.close(fd)
        temporary = Path(temporary_name)
        subtype = generation.technical_plan["sample_format"]
        _encode_lossless(master_resolved.server_path, temporary, subtype)
        source_pcm = _pcm_digest(master_resolved.server_path)
        candidate_pcm = _pcm_digest(temporary)
        if source_pcm != candidate_pcm:
            raise ValidationError(
                "PCM-verifisering feilet; kandidaten ble ikke registrert."
            )
        roundtrip = _write_and_verify_tags(temporary, generation.expected_tags)
        info = sf.info(temporary)
        plan = generation.technical_plan
        if (
            info.samplerate != plan["sample_rate"]
            or info.channels != plan["channels"]
            or info.frames != plan["frames"]
            or SUPPORTED_MASTER_SUBTYPES.get(info.subtype)
            != plan["bits_per_sample"]
        ):
            raise ValidationError("Teknisk verifisering av kandidaten feilet.")
        with temporary.open("rb") as candidate_handle:
            candidate_hash = _stream_sha256(candidate_handle)
        if before_master_hash != _stream_sha256(
            open_for_read(master_resolved)
        ):
            raise ValidationError("Masterfilen ble endret under genereringen.")
        os.replace(temporary, target.server_path)
        try:
            with transaction.atomic():
                asset = FileAsset(
                    recording=generation.recording,
                    filename=target.server_path.name,
                    role=FileAsset.Role.RADIO_FLAC,
                    lifecycle_status=FileAsset.LifecycleStatus.CANDIDATE,
                    mime_type="audio/flac",
                    size_bytes=target.server_path.stat().st_size,
                    sha256=candidate_hash,
                    technical_metadata={
                        "container": "FLAC",
                        "codec": "FLAC",
                        "sample_rate": info.samplerate,
                        "bits_per_sample": SUPPORTED_MASTER_SUBTYPES.get(
                            info.subtype
                        ),
                        "channels": info.channels,
                        "frames": info.frames,
                        "duration_ms": round(info.duration * 1000),
                        "audio_md5": format(
                            FLAC(target.server_path).info.md5_signature, "032x"
                        ),
                    },
                    metadata_read_at=timezone.now(),
                    source_modified_at=datetime.fromtimestamp(
                        target.server_path.stat().st_mtime,
                        tz=datetime_timezone.utc,
                    ),
                    sync_status=FileAsset.SyncStatus.SYNCED,
                )
                asset.full_clean()
                asset.save()
                location = FileLocation(
                    asset=asset,
                    storage_type=FileLocation.StorageType.NAS,
                    storage_root_key=generation.target_root_key,
                    relative_path=generation.target_relative_path,
                    status=FileLocation.Status.ACTIVE,
                    is_current=True,
                    verification_status=FileLocation.VerificationStatus.VERIFIED,
                )
                location.full_clean()
                location.save()
                FileChecksum.objects.create(
                    asset=asset,
                    sha256=candidate_hash,
                    reason=FileChecksum.Reason.VERIFICATION,
                )
                verification = {
                    "audio": "pcm_sha256_match",
                    "source_pcm_sha256": source_pcm,
                    "candidate_pcm_sha256": candidate_pcm,
                    "metadata": "roundtrip_match",
                    "technical": "match",
                    "tags": roundtrip,
                    "master_sha256_before": before_master_hash,
                    "master_sha256_after": before_master_hash,
                }
                derivation = FileDerivation(
                    source_asset=generation.master_asset,
                    derived_asset=asset,
                    created_by=user,
                    tool_name="libsndfile",
                    tool_version=getattr(sf, "__libsndfile_version__", ""),
                    parameters={
                        "format": "FLAC",
                        "subtype": subtype,
                        "dsp": False,
                    },
                    verification=verification,
                )
                derivation.save()
                generation.candidate_asset = asset
                generation.verification = verification
                generation.status = RadioFlacGeneration.Status.VERIFIED
                generation.save()
                for event_type in (
                    MediaAssetEvent.EventType.CANDIDATE_GENERATED,
                    MediaAssetEvent.EventType.AUDIO_VERIFIED,
                    MediaAssetEvent.EventType.METADATA_VERIFIED,
                ):
                    MediaAssetEvent.objects.create(
                        recording=generation.recording,
                        asset=asset,
                        related_asset=generation.master_asset,
                        event_type=event_type,
                        actor=user,
                        details=(
                            verification
                            if event_type
                            != MediaAssetEvent.EventType.CANDIDATE_GENERATED
                            else {}
                        ),
                    )
        except Exception:
            target.server_path.unlink(missing_ok=True)
            raise
    except Exception as error:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        generation.status = RadioFlacGeneration.Status.FAILED
        generation.failure_message = str(error)
        generation.save(update_fields=("status", "failure_message"))
        raise
    return generation


@transaction.atomic
def activate_candidate(*, generation, user):
    generation = (
        RadioFlacGeneration.objects.select_for_update(of=("self",))
        .select_related("recording", "candidate_asset")
        .get(pk=generation.pk)
    )
    if generation.status != RadioFlacGeneration.Status.VERIFIED:
        raise ValidationError("Bare en verifisert kandidat kan aktiveres.")
    recording = Recording.objects.select_for_update().get(
        pk=generation.recording_id
    )
    (
        selection,
        _,
    ) = RecordingMediaSelection.objects.select_for_update().get_or_create(
        recording=recording
    )
    candidate = FileAsset.objects.select_for_update().get(
        pk=generation.candidate_asset_id
    )
    if (
        candidate.recording_id != recording.pk
        or candidate.role != FileAsset.Role.RADIO_FLAC
        or candidate.lifecycle_status != FileAsset.LifecycleStatus.CANDIDATE
    ):
        raise ValidationError(
            "Genereringskandidaten er ikke en gyldig kandidat for innspillingen."
        )
    if selection.selected_master_id != generation.master_asset_id:
        raise ValidationError(
            "Valgt master er endret etter genereringen. Kandidaten kan ikke aktiveres."
        )
    previous = selection.current_radio
    if previous and previous.pk != candidate.pk:
        previous.lifecycle_status = FileAsset.LifecycleStatus.HISTORICAL
        previous.save(update_fields=("lifecycle_status",))
        MediaAssetEvent.objects.create(
            recording=recording,
            asset=previous,
            related_asset=candidate,
            event_type=MediaAssetEvent.EventType.RADIO_SUPERSEDED,
            actor=user,
        )
    else:
        legacy = [
            asset
            for asset in recording.file_assets.select_for_update().filter(
                role=FileAsset.Role.RADIO_FLAC
            )
            if asset.pk != candidate.pk
            and asset.lifecycle_status != FileAsset.LifecycleStatus.CANDIDATE
        ]
        for asset in legacy:
            asset.lifecycle_status = FileAsset.LifecycleStatus.HISTORICAL
            asset.save(update_fields=("lifecycle_status",))
            MediaAssetEvent.objects.create(
                recording=recording,
                asset=asset,
                related_asset=candidate,
                event_type=MediaAssetEvent.EventType.RADIO_SUPERSEDED,
                actor=user,
            )
    candidate.lifecycle_status = FileAsset.LifecycleStatus.CURRENT
    candidate.save(update_fields=("lifecycle_status",))
    selection.current_radio = candidate
    selection.current_radio_by = user
    selection.current_radio_at = timezone.now()
    selection.full_clean()
    selection.save()
    current_ids = list(
        FileAsset.objects.filter(
            recording=recording,
            role=FileAsset.Role.RADIO_FLAC,
            lifecycle_status=FileAsset.LifecycleStatus.CURRENT,
        ).values_list("pk", flat=True)
    )
    if (
        current_ids != [candidate.pk]
        or selection.current_radio_id != candidate.pk
    ):
        raise ValidationError(
            "Aktiveringen ga inkonsistent gjeldende radiofil og ble rullet tilbake."
        )
    generation.status = RadioFlacGeneration.Status.ACTIVATED
    generation.activated_by = user
    generation.activated_at = timezone.now()
    generation.save(update_fields=("status", "activated_by", "activated_at"))
    MediaAssetEvent.objects.create(
        recording=recording,
        asset=candidate,
        related_asset=previous,
        event_type=MediaAssetEvent.EventType.RADIO_ACTIVATED,
        actor=user,
        details={"generation_id": str(generation.pk)},
    )
    return candidate
