import json
from datetime import datetime
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from catalogue.models import (
    ExternalIdentifier,
    Recording,
    RecordingContribution,
    Release,
    ReleaseTrack,
)
from catalogue.services import create_release_track, find_recording_candidates
from catalogue.validators import (
    normalize_isrc,
    normalize_trade_item_number,
    validate_language,
)
from managed_music.models import ManagedRecording
from media_assets.models import FileAsset, FileChecksum, FileLocation
from music_library.models import (
    Channel,
    MusicLibraryChannel,
    MusicLibraryEntry,
    MusicLibraryTargetAudience,
    TargetAudience,
)
from parties.models import ArtistIdentity, Party
from provenance.models import (
    AppliedMetadataChange,
    ImportBatch,
    MetadataAssertion,
    SourceRecord,
    SourceSystem,
)
from rights.models import RightsClaim, RightsConfiguration
from rights_core.models import VerificationStatus

from .adapter import FlacReadError, file_sha256, read_flac, write_catalogue_tags
from .models import FlacIngestBatch, FlacIngestItem, FlacSyncLog

SOURCE_SYSTEM_NAME = "P7 radio-FLAC"


def music_root():
    configured = str(getattr(settings, "P7_MUSIC_ROOT", "") or "").strip()
    if not configured:
        raise ImproperlyConfigured("P7_MUSIC_ROOT må konfigureres før FLAC-innlesing.")
    root = Path(configured).expanduser().resolve()
    if not root.is_dir():
        raise ImproperlyConfigured("Den konfigurerte musikkroten finnes ikke.")
    return root


def resolve_music_path(relative_path="."):
    root = music_root()
    value = str(relative_path or ".").strip().replace("\\", "/")
    logical = PurePosixPath(value)
    if logical.is_absolute() or ".." in logical.parts:
        raise ValidationError("Velg en mappe innenfor den konfigurerte musikkroten.")
    resolved = root.joinpath(*logical.parts).resolve()
    if not resolved.is_relative_to(root):
        raise ValidationError(
            "Valgt mappe ligger utenfor den konfigurerte musikkroten."
        )
    return root, resolved


def _source_system():
    source, _ = SourceSystem.objects.get_or_create(
        name=SOURCE_SYSTEM_NAME,
        defaults={
            "kind": SourceSystem.Kind.IMPORT,
            "description": "Vorbis Comments og tekniske metadata lest lokalt fra radio-FLAC.",
        },
    )
    return source


def database_is_catalogue_authority(recording):
    if ManagedRecording.objects.filter(
        library_entry__recording_id=recording.pk
    ).exists():
        return True
    local_id = RightsConfiguration.objects.values_list(
        "local_organization_id", flat=True
    ).first()
    if not local_id:
        return False
    today = timezone.localdate()
    return (
        RightsClaim.objects.filter(
            recording=recording,
            right_type=RightsClaim.RightType.OWNERSHIP,
            rights_holder_id=local_id,
            status=VerificationStatus.CONFIRMED,
        )
        .filter(
            Q(valid_from__isnull=True) | Q(valid_from__lte=today),
            Q(valid_until__isnull=True) | Q(valid_until__gte=today),
        )
        .exists()
    )


def mark_recording_for_sync(recording_id):
    recording = Recording.objects.filter(pk=recording_id).first()
    if not recording or not database_is_catalogue_authority(recording):
        return 0
    count = 0
    for asset in FileAsset.objects.filter(
        recording=recording, role=FileAsset.Role.RADIO_FLAC
    ):
        asset.sync_status = FileAsset.SyncStatus.PENDING
        asset.sync_requested_at = timezone.now()
        asset.sync_error = ""
        asset.save(update_fields=("sync_status", "sync_requested_at", "sync_error"))
        count += 1
    return count


def _artist_identity(name):
    if not name:
        return None
    matches = list(ArtistIdentity.objects.filter(display_name__iexact=name)[:2])
    return matches[0] if len(matches) == 1 else None


def _recording_isrc(recording):
    return (
        recording.identifiers.filter(scheme=ExternalIdentifier.Scheme.ISRC)
        .values_list("normalized_value", flat=True)
        .first()
        or ""
    )


def _candidate_payload(matches):
    return [
        {
            "recording_uuid": str(match.recording.pk),
            "title": match.recording.title,
            "signals": list(match.signals),
            "score": match.score,
        }
        for match in matches[:5]
    ]


def _match_recording(parsed, technical):
    messages = []
    p7uuid = parsed.get("p7uuid")
    if parsed.get("p7uuid_invalid"):
        return None, "", [], ["P7UUID er ugyldig."], True
    file_isrc = parsed.get("isrc", "")
    if file_isrc:
        try:
            file_isrc = normalize_isrc(file_isrc)
        except ValidationError:
            return None, "", [], ["ISRC har ugyldig format."], True
    if p7uuid:
        recording = Recording.objects.filter(pk=p7uuid).first()
        if not recording:
            return None, "", [], ["P7UUID peker ikke til en innspilling."], True
        existing_isrc = _recording_isrc(recording)
        if file_isrc and existing_isrc and file_isrc != existing_isrc:
            return (
                recording,
                "p7uuid",
                [],
                ["P7UUID og ISRC peker mot ulike katalogidentiteter."],
                True,
            )
        other_isrc = ExternalIdentifier.objects.filter(
            scheme=ExternalIdentifier.Scheme.ISRC,
            normalized_value=file_isrc,
        ).exclude(recording=recording)
        if file_isrc and other_isrc.exists():
            return (
                recording,
                "p7uuid",
                [],
                ["P7UUID og ISRC peker mot ulike innspillinger."],
                True,
            )
        return recording, "p7uuid", [], messages, False
    if file_isrc:
        identifier = (
            ExternalIdentifier.objects.filter(
                scheme=ExternalIdentifier.Scheme.ISRC,
                normalized_value=file_isrc,
            )
            .select_related("recording")
            .first()
        )
        if identifier:
            return identifier.recording, "isrc", [], messages, False
    title = parsed.get("title", "")
    artist = _artist_identity((parsed.get("artists") or [""])[0])
    matches = find_recording_candidates(
        title=title,
        duration_ms=technical.get("duration_ms"),
        artist_identity=artist,
    )
    plausible = [match for match in matches if match.score >= 70]
    if len(plausible) == 1:
        return (
            plausible[0].recording,
            "metadata",
            _candidate_payload(matches),
            messages,
            False,
        )
    if matches:
        messages.append("Flere eller usikre innspillingstreff må vurderes manuelt.")
        return None, "metadata", _candidate_payload(matches), messages, True
    return None, "", [], messages, False


def _release_identifier(parsed):
    raw = parsed.get("barcode", "")
    if not raw:
        return None
    digits = raw.replace("-", "").replace(" ", "")
    scheme = {8: "EAN", 12: "UPC", 13: "EAN", 14: "GTIN"}.get(len(digits))
    if not scheme:
        raise ValidationError("Strekkoden har en lengde som ikke støttes.")
    return scheme, normalize_trade_item_number(raw, scheme)


def _find_release(parsed):
    if not parsed.get("album"):
        return None
    try:
        identifier = _release_identifier(parsed)
    except ValidationError:
        return None
    if identifier:
        scheme, normalized = identifier
        found = (
            ExternalIdentifier.objects.filter(
                scheme=scheme, normalized_value=normalized
            )
            .select_related("release")
            .first()
        )
        if found:
            return found.release
    catalogue_number = parsed.get("catalogue_number", "")
    if catalogue_number:
        matches = Release.objects.filter(
            title__iexact=parsed["album"], catalogue_number__iexact=catalogue_number
        )[:2]
        matches = list(matches)
        if len(matches) == 1:
            return matches[0]
    return None


def _metadata_errors(parsed):
    errors = []
    limits = {
        "title": 500,
        "album": 500,
        "catalogue_number": 100,
        "genre": 100,
        "language": 64,
    }
    for field, limit in limits.items():
        if len(parsed.get(field, "")) > limit:
            errors.append(f"{field.upper()} er lengre enn {limit} tegn.")
    for field in ("artists", "composers", "lyricists", "arrangers"):
        if any(len(value) > 255 for value in parsed.get(field, [])):
            errors.append(f"En verdi i {field.upper()} er lengre enn 255 tegn.")
    for field in ("channels", "target_audiences"):
        if any(len(value) > 100 for value in parsed.get(field, [])):
            errors.append(f"En verdi i {field.upper()} er lengre enn 100 tegn.")
    if parsed.get("language"):
        try:
            validate_language(parsed["language"])
        except ValidationError as error:
            errors.extend(error.messages)
    if parsed.get("energy_invalid"):
        errors.append("RATING må være et Energy-nivå fra 1 til 5.")
    if parsed.get("gender_invalid"):
        errors.append("GENDER har en ukjent kontrollert verdi.")
    return errors


def scan_directory(*, relative_root=".", recursive=True, user):
    root, folder = resolve_music_path(relative_root)
    if not folder.is_dir():
        raise ValidationError("Valgt innlesingsmappe finnes ikke.")
    source = _source_system()
    provenance_batch = ImportBatch.objects.create(
        source_system=source,
        notes=f"FLAC-skann av {relative_root}",
    )
    batch = FlacIngestBatch.objects.create(
        import_batch=provenance_batch,
        relative_root=str(PurePosixPath(relative_root or ".")),
        recursive=recursive,
        created_by=user,
    )
    candidates = folder.rglob("*") if recursive else folder.iterdir()
    for path in sorted(candidates, key=lambda item: str(item).casefold()):
        if not path.is_file() or path.suffix.casefold() != ".flac":
            continue
        relative_path = path.relative_to(root).as_posix()
        stat = path.stat()
        modified = datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.get_current_timezone()
        )
        existing_locations = FileLocation.objects.filter(
            storage_type=FileLocation.StorageType.NAS,
            relative_path=relative_path,
            is_current=True,
        ).select_related("asset")
        if existing_locations.count() > 1:
            FlacIngestItem.objects.create(
                batch=batch,
                relative_path=relative_path,
                action=FlacIngestItem.Action.CONFLICT,
                file_size=stat.st_size,
                source_modified_at=modified,
                messages=["Flere aktive filreferanser bruker samme sti."],
            )
            continue
        existing_asset = (
            existing_locations.first().asset if existing_locations else None
        )
        if (
            existing_asset
            and existing_asset.size_bytes == stat.st_size
            and existing_asset.source_modified_at
            and abs((existing_asset.source_modified_at - modified).total_seconds())
            < 0.001
        ):
            FlacIngestItem.objects.create(
                batch=batch,
                relative_path=relative_path,
                action=FlacIngestItem.Action.UNCHANGED,
                match_method="file_location",
                recording=existing_asset.recording,
                file_asset=existing_asset,
                file_size=stat.st_size,
                source_modified_at=modified,
            )
            continue
        try:
            snapshot = read_flac(path)
            checksum = file_sha256(path)
        except FlacReadError as error:
            FlacIngestItem.objects.create(
                batch=batch,
                relative_path=relative_path,
                action=FlacIngestItem.Action.INVALID,
                file_size=stat.st_size,
                source_modified_at=modified,
                messages=[str(error)],
            )
            continue
        recording, method, candidates, messages, conflict = _match_recording(
            snapshot.parsed, snapshot.technical
        )
        if existing_asset and existing_asset.recording_id:
            path_recording = existing_asset.recording
            if recording and recording.pk != path_recording.pk:
                conflict = True
                messages.append(
                    "Filplasseringen og filens identifikatorer peker mot ulike innspillinger."
                )
            elif not conflict:
                recording = path_recording
                method = "file_location"
        if not snapshot.parsed.get("title"):
            conflict = True
            messages.append("TITLE mangler; innspilling opprettes ikke fra filnavnet.")
        metadata_errors = _metadata_errors(snapshot.parsed)
        if metadata_errors:
            conflict = True
            messages.extend(metadata_errors)
        if snapshot.parsed.get("barcode"):
            try:
                _release_identifier(snapshot.parsed)
            except ValidationError as error:
                messages.extend(error.messages)
                conflict = True
        action = (
            FlacIngestItem.Action.CONFLICT
            if conflict
            else (
                FlacIngestItem.Action.MATCHED
                if recording
                else FlacIngestItem.Action.NEW
            )
        )
        if recording and database_is_catalogue_authority(recording):
            differences = []
            if (
                snapshot.parsed.get("title")
                and snapshot.parsed["title"] != recording.title
            ):
                differences.append("TITLE avviker fra databaseverdien")
            existing_isrc = _recording_isrc(recording)
            if (
                snapshot.parsed.get("isrc")
                and existing_isrc
                and normalize_isrc(snapshot.parsed["isrc"]) != existing_isrc
            ):
                differences.append("ISRC avviker fra databaseverdien")
            messages.extend(differences)
        FlacIngestItem.objects.create(
            batch=batch,
            relative_path=relative_path,
            action=action,
            match_method=method,
            raw_tags=snapshot.raw_tags,
            parsed_metadata=snapshot.parsed,
            technical_metadata=snapshot.technical,
            file_size=stat.st_size,
            source_modified_at=modified,
            sha256=checksum,
            candidates=candidates,
            messages=messages,
            recording=recording,
            release=_find_release(snapshot.parsed),
            file_asset=existing_asset,
        )
    return batch


def _year(value):
    try:
        year = int(str(value)[:4])
    except (TypeError, ValueError):
        return None
    return year if 1800 <= year <= 2200 else None


def _release_is_sufficient(parsed):
    return bool(
        parsed.get("album")
        and (parsed.get("barcode") or parsed.get("catalogue_number"))
    )


def _resolve_or_create_release(parsed):
    release = _find_release(parsed)
    if release or not _release_is_sufficient(parsed):
        return release
    release = Release.objects.create(
        title=parsed["album"],
        release_year=_year(parsed.get("date")),
        catalogue_number=parsed.get("catalogue_number", ""),
    )
    identifier = _release_identifier(parsed) if parsed.get("barcode") else None
    if identifier:
        scheme, _normalized = identifier
        ExternalIdentifier.objects.create(
            release=release, scheme=scheme, value=parsed["barcode"]
        )
    return release


def _release_track(release, recording, parsed, duration_ms):
    if not release:
        return None
    disc = parsed.get("disc_number")
    track = parsed.get("track_number")
    if disc and track:
        sequence = (disc - 1) * 1000 + track
    elif track:
        sequence = track
    else:
        sequence = (
            release.tracks.order_by("-sequence_number")
            .values_list("sequence_number", flat=True)
            .first()
            or 0
        ) + 1
    collision = ReleaseTrack.objects.filter(
        release=release, sequence_number=sequence
    ).first()
    if collision and collision.recording_id != recording.pk:
        raise ValidationError(
            "Sporplasseringen er allerede knyttet til en annen innspilling."
        )
    return collision or create_release_track(
        release=release,
        recording=recording,
        sequence_number=sequence,
        disc_number=disc,
        track_number=track,
        duration_ms=duration_ms,
        title_override=(
            parsed.get("title", "") if parsed.get("title") != recording.title else ""
        ),
    )


def _raw_value(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _assertion(source_record, entity_type, entity, field, value):
    return MetadataAssertion.objects.create(
        source_record=source_record,
        entity_type=entity_type,
        entity_uuid=entity.pk,
        field_name=field,
        raw_value=_raw_value(value),
        normalized_value=value,
    )


def _log_applied(assertion, entity, before, after, user, base_revision):
    AppliedMetadataChange.objects.create(
        assertion=assertion,
        entity_type=assertion.entity_type,
        entity_uuid=entity.pk,
        field_name=assertion.field_name,
        before_value=before,
        after_value=after,
        base_revision=base_revision,
        result_revision=entity.revision,
        changed_by=user,
    )


def _credit(recording, role, name, source_record):
    name = name.strip()
    if not name:
        return None
    identity = (
        _artist_identity(name) if role == RecordingContribution.Role.PRIMARY else None
    )
    party = identity.party if identity else None
    if not party and role != RecordingContribution.Role.PRIMARY:
        parties = list(Party.objects.filter(name__iexact=name)[:2])
        party = parties[0] if len(parties) == 1 else None
    contribution, _ = RecordingContribution.objects.get_or_create(
        recording=recording,
        role=role,
        credited_as=name,
        defaults={
            "party": party,
            "artist_identity": identity,
            "source_record": source_record,
        },
    )
    return contribution


def _controlled_value(model, name):
    base = slugify(name).replace("-", "_")[:45] or "verdi"
    code = base
    number = 2
    while model.objects.filter(code=code).exclude(name__iexact=name).exists():
        code = f"{base[:42]}_{number}"
        number += 1
    existing = model.objects.filter(name__iexact=name).first()
    if existing:
        return existing
    value = model.objects.create(name=name, code=code)
    return value


def _sync_library_relations(entry, model, through, relation_name, values):
    before = list(getattr(entry, relation_name).values_list("name", flat=True))
    through.objects.filter(library_entry=entry).delete()
    for name in values:
        target = _controlled_value(model, name)
        link_field = "channel" if model is Channel else "target_audience"
        through.objects.create(library_entry=entry, **{link_field: target})
    return before


@transaction.atomic
def review_item(item, *, parsed, resolution, user, note=""):
    """Approve corrected interpreted metadata without changing the source FLAC."""
    item = (
        FlacIngestItem.objects.select_for_update()
        .select_related("recording", "file_asset__recording")
        .get(pk=item.pk)
    )
    if item.applied_at:
        raise ValidationError(
            "Filen er allerede brukt og kan ikke kontrolleres på nytt."
        )
    if item.action != FlacIngestItem.Action.CONFLICT:
        raise ValidationError("Bare filer merket «Må kontrolleres» kan godkjennes her.")

    errors = _metadata_errors(parsed)
    if parsed.get("barcode"):
        try:
            _release_identifier(parsed)
        except ValidationError as error:
            errors.extend(error.messages)
    if errors:
        raise ValidationError(errors)

    recording = None
    if resolution.startswith("recording:"):
        recording_id = resolution.partition(":")[2]
        recording = Recording.objects.filter(pk=recording_id).first()
        if not recording:
            raise ValidationError("Den valgte innspillingen finnes ikke lenger.")
    elif resolution != "new":
        raise ValidationError("Velg om filen skal bruke en eksisterende innspilling.")

    p7uuid = parsed.get("p7uuid")
    if p7uuid and (not recording or str(recording.pk) != p7uuid):
        raise ValidationError(
            "P7UUID må peke til den valgte innspillingen. Korriger eller tøm feltet."
        )
    if parsed.get("isrc"):
        normalized_isrc = normalize_isrc(parsed["isrc"])
        linked_recording_id = (
            ExternalIdentifier.objects.filter(
                scheme=ExternalIdentifier.Scheme.ISRC,
                namespace="",
                normalized_value=normalized_isrc,
            )
            .values_list("recording_id", flat=True)
            .first()
        )
        if linked_recording_id and (
            not recording or linked_recording_id != recording.pk
        ):
            raise ValidationError(
                "ISRC er allerede knyttet til en annen innspilling. Velg den innspillingen eller korriger ISRC."
            )
    if (
        item.file_asset_id
        and item.file_asset.recording_id
        and (not recording or item.file_asset.recording_id != recording.pk)
    ):
        raise ValidationError(
            "Den registrerte filplasseringen tilhører en annen innspilling."
        )

    item.parsed_metadata = parsed
    item.recording = recording
    item.release = _find_release(parsed)
    item.action = (
        FlacIngestItem.Action.MATCHED if recording else FlacIngestItem.Action.NEW
    )
    item.match_method = "manual_review"
    item.messages = []
    item.reviewed_by = user
    item.reviewed_at = timezone.now()
    item.review_note = note.strip()
    item.save()
    return item


@transaction.atomic
def apply_item(item, *, user):
    item = (
        FlacIngestItem.objects.select_for_update()
        .select_related("batch__import_batch__source_system", "recording", "file_asset")
        .get(pk=item.pk)
    )
    if not item.can_apply:
        return item
    parsed = item.parsed_metadata
    source_record = SourceRecord.objects.create(
        source_system=item.batch.import_batch.source_system,
        import_batch=item.batch.import_batch,
        external_record_id=f"{item.batch_id}:{item.relative_path}",
        source_locator=item.relative_path,
        raw_payload={
            "tags": item.raw_tags,
            "parsed": parsed,
            "technical": item.technical_metadata,
            "sha256": item.sha256,
        },
    )
    recording = item.recording
    # Several files in the same preview may carry the same previously unseen
    # ISRC. The first applied row creates the Recording; later rows must
    # re-check the identifier boundary instead of creating a duplicate.
    if recording is None and parsed.get("isrc"):
        normalized_isrc = normalize_isrc(parsed["isrc"])
        identifier = (
            ExternalIdentifier.objects.select_for_update()
            .select_related("recording")
            .filter(
                scheme=ExternalIdentifier.Scheme.ISRC,
                namespace="",
                normalized_value=normalized_isrc,
            )
            .first()
        )
        if identifier:
            recording = identifier.recording
    is_new = recording is None
    if is_new:
        recording = Recording.objects.create(
            title=parsed["title"],
            duration_ms=item.technical_metadata.get("duration_ms"),
            recording_kind=Recording.Kind.SOUND,
        )
        title_assertion = _assertion(
            source_record,
            MetadataAssertion.EntityType.RECORDING,
            recording,
            "title",
            parsed["title"],
        )
        _log_applied(
            title_assertion,
            recording,
            "",
            recording.title,
            user,
            recording.revision,
        )
    catalogue_authority = (
        False if is_new else database_is_catalogue_authority(recording)
    )
    if not catalogue_authority:
        if not is_new and parsed.get("title") and recording.title != parsed["title"]:
            before, base = recording.title, recording.revision
            assertion = _assertion(
                source_record,
                MetadataAssertion.EntityType.RECORDING,
                recording,
                "title",
                parsed["title"],
            )
            recording.title = parsed["title"]
            recording.save(update_fields=("title",))
            _log_applied(assertion, recording, before, recording.title, user, base)
        if item.technical_metadata.get("duration_ms") and (
            is_new or recording.duration_ms != item.technical_metadata["duration_ms"]
        ):
            recording.duration_ms = item.technical_metadata["duration_ms"]
            recording.save(update_fields=("duration_ms",))
        file_isrc = parsed.get("isrc", "")
        if file_isrc and not _recording_isrc(recording):
            ExternalIdentifier.objects.create(
                recording=recording,
                scheme=ExternalIdentifier.Scheme.ISRC,
                value=file_isrc,
            )
        for artist in parsed.get("artists", []):
            _credit(
                recording, RecordingContribution.Role.PRIMARY, artist, source_record
            )
        for field, role in (
            ("composers", RecordingContribution.Role.COMPOSER),
            ("lyricists", RecordingContribution.Role.LYRICIST),
            ("arrangers", RecordingContribution.Role.ARRANGER),
        ):
            for name in parsed.get(field, []):
                _credit(recording, role, name, source_record)
    release = None if catalogue_authority else _resolve_or_create_release(parsed)
    known_track = item.file_asset.release_track if item.file_asset else None
    release_track = (
        known_track
        if known_track and release and known_track.release_id == release.pk
        else _release_track(
            release, recording, parsed, item.technical_metadata.get("duration_ms")
        )
    )
    entry, _ = MusicLibraryEntry.objects.get_or_create(recording=recording)
    scalar_fields = {
        "genre": parsed.get("genre"),
        "language": parsed.get("language"),
        "energy": parsed.get("energy"),
        "gender": parsed.get("gender"),
    }
    for field, after in scalar_fields.items():
        if field not in parsed:
            continue
        before, base = getattr(entry, field), entry.revision
        assertion = _assertion(
            source_record,
            MetadataAssertion.EntityType.MUSIC_LIBRARY_ENTRY,
            entry,
            field,
            after,
        )
        if before != after:
            setattr(entry, field, after if field == "energy" else (after or ""))
            entry.save(update_fields=(field,))
            _log_applied(assertion, entry, before, getattr(entry, field), user, base)
    for field, model, through, relation in (
        ("channels", Channel, MusicLibraryChannel, "channels"),
        (
            "target_audiences",
            TargetAudience,
            MusicLibraryTargetAudience,
            "target_audiences",
        ),
    ):
        if field not in parsed:
            continue
        before = _sync_library_relations(entry, model, through, relation, parsed[field])
        base = entry.revision
        entry.save()
        assertion = _assertion(
            source_record,
            MetadataAssertion.EntityType.MUSIC_LIBRARY_ENTRY,
            entry,
            field,
            parsed[field],
        )
        _log_applied(assertion, entry, before, parsed[field], user, base)
    _root, absolute = resolve_music_path(item.relative_path)
    location = (
        FileLocation.objects.filter(
            storage_type=FileLocation.StorageType.NAS,
            relative_path=item.relative_path,
            is_current=True,
        )
        .select_related("asset")
        .first()
    )
    asset = location.asset if location else None
    if asset and asset.recording_id not in {None, recording.pk}:
        raise ValidationError(
            "Filplasseringen er allerede knyttet til en annen innspilling."
        )
    if not asset:
        asset = FileAsset.objects.create(
            recording=recording,
            release_track=release_track,
            filename=absolute.name,
            mime_type="audio/flac",
            size_bytes=item.file_size,
            sha256=item.sha256,
            role=FileAsset.Role.RADIO_FLAC,
            technical_metadata=item.technical_metadata,
            metadata_read_at=timezone.now(),
            source_modified_at=item.source_modified_at,
            sync_status=FileAsset.SyncStatus.SYNCED,
        )
        location = FileLocation.objects.create(
            asset=asset,
            storage_type=FileLocation.StorageType.NAS,
            relative_path=item.relative_path,
            verification_status=FileLocation.VerificationStatus.VERIFIED,
        )
    else:
        asset.recording = recording
        asset.release_track = asset.release_track or release_track
        asset.filename = absolute.name
        asset.mime_type = "audio/flac"
        asset.size_bytes = item.file_size
        asset.sha256 = item.sha256
        asset.technical_metadata = item.technical_metadata
        asset.metadata_read_at = timezone.now()
        asset.source_modified_at = item.source_modified_at
        asset.sync_status = FileAsset.SyncStatus.SYNCED
        asset.sync_error = ""
        asset.save()
    FileChecksum.objects.get_or_create(
        asset=asset,
        sha256=item.sha256,
        defaults={"reason": FileChecksum.Reason.INGEST},
    )
    item.recording = recording
    item.release = release
    item.release_track = release_track
    item.file_asset = asset
    item.source_record = source_record
    item.applied_at = timezone.now()
    item.save()
    transaction.on_commit(
        lambda: sync_file_asset(asset.pk, p7uuid_only=not catalogue_authority)
    )
    return item


def apply_batch(batch, *, user):
    applied = 0
    for item in batch.items.order_by("relative_path"):
        if item.can_apply:
            apply_item(item, user=user)
            applied += 1
    batch.refresh_from_db()
    remaining = (
        batch.items.filter(applied_at__isnull=True)
        .exclude(action=FlacIngestItem.Action.UNCHANGED)
        .exists()
    )
    batch.status = (
        FlacIngestBatch.Status.PARTIAL if remaining else FlacIngestBatch.Status.APPLIED
    )
    batch.save(update_fields=("status",))
    return applied


def _catalogue_tags(asset, p7uuid_only=False):
    recording = asset.recording
    values = {"P7UUID": str(recording.pk)}
    if p7uuid_only:
        return values
    values.update(
        {
            "TITLE": recording.title,
            "ARTIST": "",
            "ISRC": "",
            "COMPOSER": [],
            "LYRICIST": [],
            "ARRANGER": [],
        }
    )
    primary = recording.contributions.filter(
        role=RecordingContribution.Role.PRIMARY
    ).first()
    if primary:
        values["ARTIST"] = primary.credited_as or str(primary.party)
    isrc = _recording_isrc(recording)
    if isrc:
        values["ISRC"] = isrc
    for role, tag in (
        (RecordingContribution.Role.COMPOSER, "COMPOSER"),
        (RecordingContribution.Role.LYRICIST, "LYRICIST"),
        (RecordingContribution.Role.ARRANGER, "ARRANGER"),
    ):
        names = [
            contribution.credited_as or str(contribution.party)
            for contribution in recording.contributions.filter(role=role)
        ]
        if names:
            values[tag] = names
    track = asset.release_track
    if track:
        release = track.release
        values.update(
            {
                "ALBUM": release.title,
                "ALBUMARTIST": (
                    primary.credited_as or str(primary.party) if primary else ""
                ),
                "TRACKNUMBER": track.track_number or "",
                "DISCNUMBER": track.disc_number or "",
                "DATE": "",
                "CATALOGNUMBER": release.catalogue_number,
                "BARCODE": "",
            }
        )
        if release.release_date:
            values["DATE"] = release.release_date.isoformat()
        elif release.release_year:
            values["DATE"] = release.release_year
        barcode = release.identifiers.filter(
            scheme__in=(
                ExternalIdentifier.Scheme.UPC,
                ExternalIdentifier.Scheme.EAN,
                ExternalIdentifier.Scheme.GTIN,
            )
        ).first()
        if barcode:
            values["BARCODE"] = barcode.normalized_value
    return values


def _asset_path(asset):
    location = asset.locations.filter(
        storage_type=FileLocation.StorageType.NAS, is_current=True
    ).first()
    if not location:
        return None
    _root, path = resolve_music_path(location.relative_path)
    return path


def sync_file_asset(asset_id, *, p7uuid_only=False):
    asset = FileAsset.objects.select_related("recording", "release_track__release").get(
        pk=asset_id
    )
    if asset.role != FileAsset.Role.RADIO_FLAC or not asset.recording_id:
        return None
    if not p7uuid_only and not database_is_catalogue_authority(asset.recording):
        message = (
            "Katalogtags ble ikke skrevet fordi databasen ikke er "
            "katalogautoritet for innspillingen."
        )
        asset.sync_status = FileAsset.SyncStatus.CONFLICT
        asset.sync_error = message
        asset.save(update_fields=("sync_status", "sync_error"))
        return FlacSyncLog.objects.create(
            asset=asset,
            result=FlacSyncLog.Result.CONFLICT,
            written_tags={},
            protected_tags={},
            error=message,
        )
    values = _catalogue_tags(asset, p7uuid_only=p7uuid_only)
    error_message = ""
    try:
        path = _asset_path(asset)
        if not path or not path.is_file():
            raise FileNotFoundError("Radio-FLAC finnes ikke på registrert plassering.")
        _all_tags, protected = write_catalogue_tags(path, values)
        checksum = file_sha256(path)
        stat = path.stat()
        snapshot = read_flac(path)
    except FileNotFoundError as error:
        result = FlacSyncLog.Result.MISSING
        status = FileAsset.SyncStatus.MISSING
        protected = {}
        error_message = str(error)
    except Exception as error:  # recorded for retry; catalogue change must survive
        result = FlacSyncLog.Result.FAILED
        status = FileAsset.SyncStatus.FAILED
        protected = {}
        error_message = str(error)
    else:
        result = FlacSyncLog.Result.SUCCESS
        status = FileAsset.SyncStatus.SYNCED
        asset.sha256 = checksum
        asset.size_bytes = stat.st_size
        asset.source_modified_at = datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.get_current_timezone()
        )
        asset.metadata_read_at = timezone.now()
        asset.technical_metadata = snapshot.technical
        asset.synced_at = timezone.now()
        FileChecksum.objects.get_or_create(
            asset=asset,
            sha256=checksum,
            defaults={"reason": FileChecksum.Reason.WRITEBACK},
        )
    asset.sync_status = status
    asset.sync_error = error_message
    asset.save()
    return FlacSyncLog.objects.create(
        asset=asset,
        result=result,
        written_tags=values,
        protected_tags=protected,
        error=asset.sync_error,
    )


def sync_recording_files(recording):
    if not database_is_catalogue_authority(recording):
        return []
    return [
        sync_file_asset(asset.pk)
        for asset in FileAsset.objects.filter(
            recording=recording,
            role=FileAsset.Role.RADIO_FLAC,
            sync_status=FileAsset.SyncStatus.PENDING,
        )
    ]
