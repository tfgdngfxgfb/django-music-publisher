import json
import logging
import mimetypes
import re
from difflib import SequenceMatcher
from datetime import datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from catalogue.models import (
    DuplicateCandidate,
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
)
from managed_music.models import ManagedRecording
from media_assets.models import FileAsset, FileChecksum, FileLocation
from media_assets.selection import establish_current_radio_if_unambiguous
from media_assets.storage import get_storage_root, resolve_storage_path
from music_library.models import (
    Channel,
    MusicLibraryChannel,
    MusicLibraryEntry,
    MusicLibraryTargetAudience,
    TargetAudience,
)
from music_library.validators import validate_radio_language
from parties.models import ArtistIdentity, Party
from provenance.models import (
    AppliedMetadataChange,
    AssertionDecision,
    ImportBatch,
    MetadataAssertion,
    SourceRecord,
    SourceSystem,
)
from rights.models import RightsClaim, RightsConfiguration
from rights_core.models import VerificationStatus

from .adapter import (
    TAG_ADAPTER_VERSION,
    FlacReadError,
    file_sha256,
    read_flac,
    write_catalogue_tags,
)
from .models import FlacIngestBatch, FlacIngestItem, FlacSyncLog

SOURCE_SYSTEM_NAME = "P7 radio-FLAC"
AUTOMATIC_FLAC_DECISION_NOTE = (
    "Automatisk bekreftet ved anvendelse fra autoritativ radio-FLAC."
)
RELEASE_SIGNATURE_NAMESPACE = "p7-flac-release-signature-v1"
RELEASE_CONTEXT_VERSION = 1
DISC_FOLDER_PATTERN = re.compile(
    r"^(?:cd|disc|disk|plate)\s*[-_. ]*([1-9][0-9]*)\b", re.IGNORECASE
)
logger = logging.getLogger(__name__)


class FileChangedDuringScan(OSError):
    pass


class SourceFileUnavailable(ValidationError):
    pass


def music_root():
    return get_storage_root(require_directory=True).server_root


def resolve_music_path(relative_path="."):
    """Compatibility wrapper; path policy is owned by media_assets.storage."""
    resolved = resolve_storage_path(relative_path, require_root=True)
    return resolved.root.server_root, resolved.server_path


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
        asset.save(
            update_fields=("sync_status", "sync_requested_at", "sync_error")
        )
        count += 1
    return count


def _artist_identity(name):
    if not name:
        return None
    matches = list(
        ArtistIdentity.objects.filter(display_name__iexact=name)[:2]
    )
    return matches[0] if len(matches) == 1 else None


def _recording_isrc(recording):
    return (
        recording.identifiers.filter(scheme=ExternalIdentifier.Scheme.ISRC)
        .values_list("normalized_value", flat=True)
        .first()
        or ""
    )


def _identity_text(value):
    return " ".join(slugify(str(value or "")).replace("-", " ").split())


def _material_identity_conflicts(left, left_technical, right, right_technical):
    """Return strong contradictions that make a shared ISRC unsafe to trust.

    ISRC remains a strong signal, but real source catalogues sometimes reuse a
    code incorrectly. Minor punctuation/title variations are tolerated. A
    materially different title together with artist, duration or album evidence
    must be reviewed instead of silently joining two files to one Recording.
    """
    left_title = _identity_text(left.get("title"))
    right_title = _identity_text(right.get("title"))
    similarity = (
        SequenceMatcher(None, left_title, right_title).ratio()
        if left_title and right_title
        else 1.0
    )
    title_conflict = bool(left_title and right_title and similarity < 0.82)

    left_artists = {
        _identity_text(value) for value in left.get("artists", []) if value
    }
    right_artists = {
        _identity_text(value) for value in right.get("artists", []) if value
    }
    artist_conflict = bool(
        left_artists
        and right_artists
        and left_artists.isdisjoint(right_artists)
    )

    left_duration = left_technical.get("duration_ms")
    right_duration = right_technical.get("duration_ms")
    duration_conflict = False
    if left_duration and right_duration:
        difference = abs(left_duration - right_duration)
        duration_conflict = difference > max(
            10000, max(left_duration, right_duration) * 0.08
        )
        severe_duration_conflict = difference > max(
            30000, max(left_duration, right_duration) * 0.20
        )
    else:
        severe_duration_conflict = False

    left_album = _identity_text(left.get("album"))
    right_album = _identity_text(right.get("album"))
    album_conflict = bool(
        left_album and right_album and left_album != right_album
    )
    clearly_incompatible = (
        (title_conflict and similarity < 0.45)
        or (
            title_conflict
            and (artist_conflict or duration_conflict or album_conflict)
        )
        or (artist_conflict and (duration_conflict or album_conflict))
        or (severe_duration_conflict and artist_conflict)
    )
    if not clearly_incompatible:
        return []

    reasons = []
    if title_conflict:
        reasons.append("vesentlig forskjellig tittel")
    if artist_conflict:
        reasons.append("forskjellig artist")
    if duration_conflict:
        reasons.append("forskjellig varighet")
    if album_conflict:
        reasons.append("forskjellig utgivelseskontekst")
    return reasons


def _recording_identity_conflicts(recording, parsed, technical):
    artists = [
        contribution.display_credit
        for contribution in recording.contributions.filter(
            role__in=(
                RecordingContribution.Role.PRIMARY,
                RecordingContribution.Role.FEATURED,
            )
        )
    ]
    return _material_identity_conflicts(
        {
            "title": recording.title,
            "artists": artists,
            "album": next(
                (
                    track.release.title
                    for track in recording.release_tracks.select_related(
                        "release"
                    )
                    if track.release_id
                ),
                "",
            ),
        },
        {"duration_ms": recording.duration_ms},
        parsed,
        technical,
    )


def _manual_file_assignment(asset):
    value = (asset.technical_metadata or {}).get(
        "manual_recording_assignment"
    ) or {}
    return bool(
        asset.recording_id
        and value.get("recording_uuid") == str(asset.recording_id)
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


def _match_recording(
    parsed,
    technical,
    *,
    catalogue_has_recordings=True,
    allow_uuid_recovery=False,
):
    messages = []
    p7uuid = parsed.get("p7uuid")
    if parsed.get("p7uuid_invalid"):
        return None, "", [], ["P7UUID er ugyldig."], True
    file_isrc = parsed.get("isrc", "")
    if file_isrc:
        try:
            file_isrc = normalize_isrc(file_isrc)
        except ValidationError:
            shown = str(file_isrc)[:80]
            return (
                None,
                "",
                [],
                [f"Kunne ikke lese ISRC: ugyldig verdi «{shown}»."],
                True,
            )
    if p7uuid:
        recording = Recording.objects.filter(pk=p7uuid).first()
        if not recording:
            if allow_uuid_recovery:
                conflicting_isrc = (
                    ExternalIdentifier.objects.filter(
                        scheme=ExternalIdentifier.Scheme.ISRC,
                        normalized_value=file_isrc,
                    )
                    .select_related("recording")
                    .first()
                    if file_isrc
                    else None
                )
                if conflicting_isrc:
                    return (
                        conflicting_isrc.recording,
                        "p7uuid_recovery",
                        [],
                        [
                            "Kildedataene bruker en ISRC som allerede er knyttet "
                            "til en annen innspilling. Dette er en konflikt i "
                            "kildekatalogen; innspillingene slås ikke sammen automatisk."
                        ],
                        True,
                    )
                return (
                    None,
                    "p7uuid_recovery",
                    [],
                    ["Ny innspilling vil gjenbruke P7UUID fra filen."],
                    False,
                )
            return (
                None,
                "",
                [],
                ["P7UUID peker ikke til en innspilling."],
                True,
            )
        existing_isrc = _recording_isrc(recording)
        if file_isrc and existing_isrc and file_isrc != existing_isrc:
            return (
                recording,
                "p7uuid",
                [],
                [
                    "P7UUID og ISRC peker mot ulike katalogidentiteter. Dette er "
                    "en konflikt i kildekatalogen; innspillingene slås ikke sammen "
                    "automatisk."
                ],
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
                [
                    "P7UUID og ISRC peker mot ulike innspillinger. Dette er en "
                    "konflikt i kildekatalogen; innspillingene slås ikke sammen "
                    "automatisk."
                ],
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
            contradictions = _recording_identity_conflicts(
                identifier.recording, parsed, technical
            )
            if contradictions:
                return (
                    identifier.recording,
                    "isrc_conflict",
                    [
                        {
                            "recording_uuid": str(identifier.recording_id),
                            "title": identifier.recording.title,
                            "signals": ["samme ISRC", *contradictions],
                            "score": 100,
                        }
                    ],
                    [
                        "Samme ISRC er allerede registrert, men filene ser ut til "
                        f"å være ulike innspillinger ({', '.join(contradictions)}). "
                        "Kontroller koblingen; ingen automatisk sammenslåing er gjort."
                    ],
                    True,
                )
            return identifier.recording, "isrc", [], messages, False
    if not catalogue_has_recordings:
        return None, "", [], messages, False
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
        messages.append(
            "Flere eller usikre innspillingstreff må vurderes manuelt."
        )
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


def _release_signature(parsed):
    """Return a conservative adapter identity when industry IDs are absent."""
    folder = str(parsed.get("release_folder", "")).strip()
    if folder and folder != ".":
        return sha256(folder.casefold().encode("utf-8")).hexdigest()
    year = _year(parsed.get("date"))
    if not (
        parsed.get("album")
        and parsed.get("album_artist")
        and year
        and parsed.get("track_number")
    ):
        return ""
    values = (
        parsed["album"].strip().casefold(),
        parsed["album_artist"].strip().casefold(),
        str(year),
    )
    return sha256(
        json.dumps(values, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _release_candidates(parsed):
    releases = {}
    try:
        identifier = _release_identifier(parsed)
    except ValidationError:
        identifier = None
    if identifier:
        scheme, normalized = identifier
        for release in Release.objects.filter(
            identifiers__scheme=scheme,
            identifiers__normalized_value=normalized,
        ):
            releases[release.pk] = release
    catalogue_number = parsed.get("catalogue_number", "")
    if parsed.get("album") and catalogue_number:
        for release in Release.objects.filter(
            title__iexact=parsed["album"],
            catalogue_number__iexact=catalogue_number,
        )[:2]:
            releases[release.pk] = release
    signature = _release_signature(parsed)
    if signature:
        for release in Release.objects.filter(
            identifiers__scheme=ExternalIdentifier.Scheme.EXTERNAL,
            identifiers__namespace=RELEASE_SIGNATURE_NAMESPACE,
            identifiers__normalized_value=signature,
        ):
            releases[release.pk] = release
    return list(releases.values())


def _find_release(parsed):
    if not parsed.get("album"):
        return None
    matches = _release_candidates(parsed)
    return matches[0] if len(matches) == 1 else None


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
            errors.append(
                f"En verdi i {field.upper()} er lengre enn 255 tegn."
            )
    for field in ("channels", "target_audiences"):
        if any(len(value) > 100 for value in parsed.get(field, [])):
            errors.append(
                f"En verdi i {field.upper()} er lengre enn 100 tegn."
            )
    if parsed.get("language"):
        try:
            validate_radio_language(parsed["language"])
        except ValidationError as error:
            errors.extend(error.messages)
    if parsed.get("energy_invalid"):
        errors.append("RATING må være et Energy-nivå fra 1 til 5.")
    if parsed.get("gender_invalid"):
        errors.append("GENDER har en ukjent kontrollert verdi.")
    if parsed.get("rotation_suitability_invalid"):
        errors.append("ROTASJON har en ukjent kontrollert verdi.")
    return errors


def _with_release_folder_context(parsed, relative_path):
    """Add adapter-only release context from P7's archive folder convention."""
    result = dict(parsed)
    file_folder = PurePosixPath(relative_path).parent
    album_folder = file_folder
    disc_match = DISC_FOLDER_PATTERN.match(file_folder.name)
    if disc_match and file_folder.parent.name:
        album_folder = file_folder.parent
        if not result.get("disc_number"):
            result["disc_number"] = int(disc_match.group(1))
    if album_folder.name:
        result["release_folder"] = str(album_folder)
        if not result.get("album"):
            result["album"] = album_folder.name
    return result


def _stable_snapshot(path):
    """Read tags and checksum only when the file stays unchanged throughout."""
    before = path.stat()
    snapshot = read_flac(path)
    checksum = file_sha256(path)
    after = path.stat()
    before_state = (before.st_size, before.st_mtime_ns)
    after_state = (after.st_size, after.st_mtime_ns)
    if before_state != after_state:
        raise FileChangedDuringScan(
            "Filen ble endret under skanning og må prøves igjen."
        )
    return snapshot, checksum, after


def _scan_error_item(batch, relative_path, action, message, stat=None):
    modified = None
    size = None
    if stat is not None:
        size = stat.st_size
        modified = datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.get_current_timezone()
        )
    return FlacIngestItem.objects.create(
        batch=batch,
        relative_path=relative_path,
        action=action,
        file_size=size,
        source_modified_at=modified,
        messages=[message],
    )


def _candidate_paths(root, folder, recursive, relative_paths):
    if relative_paths is not None:
        return [resolve_music_path(value)[1] for value in relative_paths]
    return list(folder.rglob("*") if recursive else folder.iterdir())


def _existing_locations(relative_root, relative_paths):
    base_queryset = FileLocation.objects.filter(
        storage_type=FileLocation.StorageType.NAS,
        is_current=True,
    ).select_related("asset", "asset__recording")
    if relative_paths is not None:
        requested = sorted(set(relative_paths))
        locations = []
        for offset in range(0, len(requested), 500):
            locations.extend(
                base_queryset.filter(
                    relative_path__in=requested[offset : offset + 500]
                )
            )
    else:
        prefix = str(PurePosixPath(relative_root or "."))
        if prefix not in {"", "."}:
            base_queryset = base_queryset.filter(
                Q(relative_path=prefix)
                | Q(relative_path__startswith=f"{prefix}/")
            )
        locations = base_queryset
    result = {}
    for location in locations:
        result.setdefault(location.relative_path, []).append(location)
    return result


def _moved_asset_candidate(parsed, technical):
    """Find one compatible radio file by STREAMINFO PCM MD5.

    PCM MD5 survives tag edits, but identical audio can legitimately occur in
    several files. It is therefore accepted only with one candidate and a
    compatible catalogue signal; it never merges Recordings by itself.
    """
    audio_md5 = technical.get("audio_md5")
    if not audio_md5 or audio_md5 == "0" * 32:
        return None
    assets = list(
        FileAsset.objects.filter(
            role=FileAsset.Role.RADIO_FLAC,
            technical_metadata__audio_md5=audio_md5,
            recording__isnull=False,
        )
        .select_related("recording")
        .prefetch_related("locations")[:2]
    )
    if len(assets) != 1:
        return None
    asset = assets[0]
    p7uuid = parsed.get("p7uuid")
    if p7uuid:
        return asset if str(asset.recording_id) == p7uuid else None
    file_isrc = parsed.get("isrc")
    if file_isrc:
        try:
            return (
                asset
                if _recording_isrc(asset.recording)
                == normalize_isrc(file_isrc)
                else None
            )
        except ValidationError:
            return None
    duration = technical.get("duration_ms")
    title_matches = (
        parsed.get("title", "").strip().casefold()
        == asset.recording.title.strip().casefold()
    )
    duration_matches = (
        duration is not None
        and asset.recording.duration_ms is not None
        and abs(duration - asset.recording.duration_ms) <= 2000
    )
    return asset if title_matches and duration_matches else None


def reconcile_missing_file_locations(
    *, relative_root=".", relative_paths=None, recursive=True
):
    """Mark absent registered radio locations without deleting catalogue data."""
    root, _folder = resolve_music_path(relative_root)
    locations = _existing_locations(relative_root, relative_paths)
    changed = 0
    prefix = PurePosixPath(relative_root or ".")
    for logical_path, candidates in locations.items():
        if (
            not recursive
            and relative_paths is None
            and PurePosixPath(logical_path).parent != prefix
        ):
            continue
        path = root.joinpath(*PurePosixPath(logical_path).parts)
        if path.exists():
            continue
        for location in candidates:
            if location.asset.role != FileAsset.Role.RADIO_FLAC:
                continue
            if location.status != FileLocation.Status.MISSING:
                location.status = FileLocation.Status.MISSING
                location.save(update_fields=("status",))
                changed += 1
            asset = location.asset
            if asset.sync_status != FileAsset.SyncStatus.MISSING:
                asset.sync_status = FileAsset.SyncStatus.MISSING
                asset.sync_error = (
                    "Radio-FLAC mangler på registrert plassering."
                )
                asset.save(update_fields=("sync_status", "sync_error"))
    return changed


def scan_directory(
    *,
    relative_root=".",
    recursive=True,
    user,
    relative_paths=None,
    allow_uuid_recovery=False,
    force_read=False,
):
    # RECOVERY OVERRIDE: keep administrator-only and review before production use.
    if allow_uuid_recovery and not user.is_superuser:
        raise ValidationError(
            "Bare en administrator kan gjenopprette innspillinger fra P7UUID."
        )
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
        allow_uuid_recovery=allow_uuid_recovery,
        created_by=user,
    )
    candidates = _candidate_paths(root, folder, recursive, relative_paths)
    locations_by_path = _existing_locations(relative_root, relative_paths)
    catalogue_has_recordings = Recording.objects.exists()
    batch_isrcs = {}
    for path in sorted(candidates, key=lambda item: str(item).casefold()):
        if path.suffix.casefold() != ".flac":
            continue
        relative_path = path.relative_to(root).as_posix()
        try:
            stat = path.stat()
        except OSError as error:
            _scan_error_item(
                batch,
                relative_path,
                FlacIngestItem.Action.INVALID,
                f"Kunne ikke lese filopplysninger: {error}",
            )
            continue
        if not path.is_file():
            continue
        modified = datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.get_current_timezone()
        )
        existing_locations = locations_by_path.get(relative_path, [])
        if len(existing_locations) > 1:
            _scan_error_item(
                batch,
                relative_path,
                FlacIngestItem.Action.CONFLICT,
                "Flere aktive filreferanser bruker samme sti.",
                stat,
            )
            continue
        existing_asset = (
            existing_locations[0].asset if existing_locations else None
        )
        if (
            existing_asset
            and not force_read
            and existing_asset.size_bytes == stat.st_size
            and existing_asset.source_modified_at
            and existing_asset.technical_metadata.get("tag_adapter_version")
            == TAG_ADAPTER_VERSION
            and existing_asset.technical_metadata.get(
                "release_context_version"
            )
            == RELEASE_CONTEXT_VERSION
            and abs(
                (existing_asset.source_modified_at - modified).total_seconds()
            )
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
            snapshot, checksum, stable_stat = _stable_snapshot(path)
            stat = stable_stat
            modified = datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.get_current_timezone()
            )
        except FileChangedDuringScan as error:
            _scan_error_item(
                batch,
                relative_path,
                FlacIngestItem.Action.RETRY,
                str(error),
                stat,
            )
            continue
        except FlacReadError as error:
            _scan_error_item(
                batch,
                relative_path,
                FlacIngestItem.Action.INVALID,
                str(error),
                stat,
            )
            continue
        except OSError as error:
            _scan_error_item(
                batch,
                relative_path,
                FlacIngestItem.Action.INVALID,
                f"Kunne ikke lese hele filen: {error}",
                stat,
            )
            continue
        parsed = _with_release_folder_context(snapshot.parsed, relative_path)
        technical = {
            **snapshot.technical,
            "release_context_version": RELEASE_CONTEXT_VERSION,
        }
        if not existing_asset:
            existing_asset = _moved_asset_candidate(parsed, technical)
        recording, method, candidates, messages, conflict = _match_recording(
            parsed,
            technical,
            catalogue_has_recordings=catalogue_has_recordings,
            allow_uuid_recovery=allow_uuid_recovery,
        )
        if parsed.get("isrc"):
            try:
                normalized_isrc = normalize_isrc(parsed["isrc"])
            except ValidationError:
                normalized_isrc = ""
            previous = (
                batch_isrcs.get(normalized_isrc) if normalized_isrc else None
            )
            if previous:
                contradictions = _material_identity_conflicts(
                    previous[0], previous[1], parsed, technical
                )
                previous_asset = previous[3]
                manually_separated = bool(
                    previous_asset
                    and existing_asset
                    and previous_asset.recording_id
                    != existing_asset.recording_id
                    and (
                        _manual_file_assignment(previous_asset)
                        or _manual_file_assignment(existing_asset)
                    )
                )
                if contradictions and not manually_separated:
                    conflict = True
                    method = "isrc_batch_conflict"
                    messages.append(
                        "Samme ISRC finnes på en tidligere fil i denne skanningen, "
                        "men metadataene beskriver ulike innspillinger "
                        f"({', '.join(contradictions)}). Filene kobles ikke automatisk."
                    )
            elif normalized_isrc:
                batch_isrcs[normalized_isrc] = (
                    parsed,
                    technical,
                    relative_path,
                    existing_asset,
                )
        if existing_asset and existing_asset.recording_id:
            path_recording = existing_asset.recording
            if (
                recording
                and recording.pk != path_recording.pk
                and _manual_file_assignment(existing_asset)
            ):
                recording = path_recording
                method = "manual_file_assignment"
                conflict = False
                messages = [
                    "Den manuelt kontrollerte filkoblingen er beholdt selv om "
                    "filen rapporterer en identifikator som brukes av en annen innspilling."
                ]
            elif recording and recording.pk != path_recording.pk:
                conflict = True
                messages.append(
                    "Filplasseringen og filens identifikatorer peker mot ulike innspillinger."
                )
            elif (
                recording
                and recording.pk == path_recording.pk
                and conflict
                and method == "isrc_conflict"
                and path_recording.file_assets.filter(
                    role=FileAsset.Role.RADIO_FLAC
                ).count()
                == 1
            ):
                # A previously established one-file link is stronger than stale
                # unmanaged catalogue text during an explicit re-read. Multiple
                # files on the same Recording stay in conflict until reviewed.
                conflict = False
                method = "file_location"
                messages = [
                    "Den eksisterende, entydige filkoblingen ble brukt ved ny innlesing."
                ]
            elif not conflict:
                recording = path_recording
                method = (
                    "audio_md5_move"
                    if not existing_locations
                    else "file_location"
                )
                if method == "audio_md5_move":
                    messages.append(
                        "Samme lydinnhold er funnet på en ny filplassering."
                    )
        if not parsed.get("title"):
            conflict = True
            messages.append(
                "TITLE mangler; innspilling opprettes ikke fra filnavnet."
            )
        metadata_errors = _metadata_errors(parsed)
        if metadata_errors:
            conflict = True
            messages.extend(metadata_errors)
        if parsed.get("barcode"):
            try:
                _release_identifier(parsed)
            except ValidationError as error:
                messages.extend(error.messages)
                conflict = True
        release_candidates = _release_candidates(parsed)
        if len(release_candidates) > 1:
            messages.append(
                "Utgivelsesidentifikatorene peker mot ulike utgivelser."
            )
            conflict = True
        action = (
            FlacIngestItem.Action.CONFLICT
            if conflict
            else (
                FlacIngestItem.Action.UPDATED
                if existing_asset
                else (
                    FlacIngestItem.Action.MATCHED
                    if recording
                    else FlacIngestItem.Action.NEW
                )
            )
        )
        if recording and database_is_catalogue_authority(recording):
            differences = []
            if parsed.get("title") and parsed["title"] != recording.title:
                differences.append("TITLE avviker fra databaseverdien")
            existing_isrc = _recording_isrc(recording)
            if (
                parsed.get("isrc")
                and existing_isrc
                and normalize_isrc(parsed["isrc"]) != existing_isrc
            ):
                differences.append("ISRC avviker fra databaseverdien")
            messages.extend(differences)
        FlacIngestItem.objects.create(
            batch=batch,
            relative_path=relative_path,
            action=action,
            match_method=method,
            raw_tags=snapshot.raw_tags,
            parsed_metadata=parsed,
            technical_metadata=technical,
            file_size=stat.st_size,
            source_modified_at=modified,
            sha256=checksum,
            candidates=candidates,
            messages=messages,
            recording=recording,
            release=(
                release_candidates[0] if len(release_candidates) == 1 else None
            ),
            file_asset=existing_asset,
        )
    reconcile_missing_file_locations(
        relative_root=relative_root,
        relative_paths=relative_paths,
        recursive=recursive,
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
        and (
            parsed.get("barcode")
            or parsed.get("catalogue_number")
            or _release_signature(parsed)
        )
    )


def _resolve_or_create_release(parsed):
    candidates = _release_candidates(parsed)
    if len(candidates) > 1:
        raise ValidationError(
            "Utgivelsesidentifikatorene peker mot ulike utgivelser."
        )
    release = _find_release(parsed)
    signature = _release_signature(parsed)
    if release:
        if (
            signature
            and not release.identifiers.filter(
                scheme=ExternalIdentifier.Scheme.EXTERNAL,
                namespace=RELEASE_SIGNATURE_NAMESPACE,
            ).exists()
        ):
            ExternalIdentifier.objects.create(
                release=release,
                scheme=ExternalIdentifier.Scheme.EXTERNAL,
                namespace=RELEASE_SIGNATURE_NAMESPACE,
                value=signature,
            )
        return release
    if not _release_is_sufficient(parsed):
        return release
    release = Release.objects.create(
        title=parsed["album"],
        release_year=_year(parsed.get("date")),
        catalogue_number=parsed.get("catalogue_number", ""),
        verification_status=VerificationStatus.CONFIRMED,
    )
    identifier = _release_identifier(parsed) if parsed.get("barcode") else None
    if identifier:
        scheme, _normalized = identifier
        ExternalIdentifier.objects.create(
            release=release, scheme=scheme, value=parsed["barcode"]
        )
    if signature:
        ExternalIdentifier.objects.create(
            release=release,
            scheme=ExternalIdentifier.Scheme.EXTERNAL,
            namespace=RELEASE_SIGNATURE_NAMESPACE,
            value=signature,
        )
    return release


def _attach_release_cover(release, parsed):
    """Register one unambiguous cover file from the album folder, without writing it."""
    folder_value = str(parsed.get("release_folder", "")).strip()
    if not release or not folder_value or folder_value == ".":
        return None
    _root, folder = resolve_music_path(folder_value)
    try:
        images = sorted(
            (
                path
                for path in folder.iterdir()
                if path.is_file()
                and path.suffix.casefold()
                in {".jpg", ".jpeg", ".png", ".webp"}
            ),
            key=lambda path: path.name.casefold(),
        )
    except OSError:
        return None
    preferred = [
        path
        for path in images
        if path.stem.casefold() in {"cover", "folder", "front", "album"}
    ]
    candidate = (
        preferred[0]
        if len(preferred) == 1
        else (images[0] if len(images) == 1 else None)
    )
    if not candidate:
        return None
    root = music_root()
    relative_path = candidate.relative_to(root).as_posix()
    location = (
        FileLocation.objects.filter(
            storage_type=FileLocation.StorageType.NAS,
            relative_path=relative_path,
            is_current=True,
        )
        .select_related("asset")
        .first()
    )
    if location:
        if location.asset.release_id not in {None, release.pk}:
            raise ValidationError(
                "Coverfilen er allerede knyttet til en annen utgivelse."
            )
        return location.asset
    try:
        stat = candidate.stat()
        checksum = file_sha256(candidate)
    except OSError:
        return None
    asset = FileAsset.objects.create(
        release=release,
        filename=candidate.name,
        mime_type=mimetypes.guess_type(candidate.name)[0] or "",
        size_bytes=stat.st_size,
        sha256=checksum,
        role=FileAsset.Role.COVER_IMAGE,
        source_modified_at=datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.get_current_timezone()
        ),
    )
    FileLocation.objects.create(
        asset=asset,
        storage_type=FileLocation.StorageType.NAS,
        relative_path=relative_path,
        status=FileLocation.Status.ACTIVE,
        verification_status=FileLocation.VerificationStatus.UNCHECKED,
    )
    return asset


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
            parsed.get("title", "")
            if parsed.get("title") != recording.title
            else ""
        ),
    )


def _raw_value(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _assertion(source_record, entity_type, entity, field, value, *, user):
    """Record a FLAC value that the authority rules applied as canonical.

    These assertions are confirmed because the value has already passed the
    controlled ingest checks and FLAC is authoritative for this field. This is
    separate from rights claims, which are never confirmed by FLAC ingest.
    """
    assertion = MetadataAssertion.objects.create(
        source_record=source_record,
        entity_type=entity_type,
        entity_uuid=entity.pk,
        field_name=field,
        raw_value=_raw_value(value),
        normalized_value=value,
        status=VerificationStatus.CONFIRMED,
    )
    AssertionDecision.objects.create(
        assertion=assertion,
        decision=VerificationStatus.CONFIRMED,
        decided_by=user,
        note=AUTOMATIC_FLAC_DECISION_NOTE,
    )
    return assertion


@transaction.atomic
def confirm_existing_flac_assertions(*, user):
    """Confirm legacy FLAC assertions created before automatic confirmation."""
    flac_assertions = MetadataAssertion.objects.filter(
        source_record__source_system__name=SOURCE_SYSTEM_NAME,
        source_record__external_record_id__startswith="flac:",
    )
    library_entry_ids = set(
        flac_assertions.filter(
            entity_type=MetadataAssertion.EntityType.MUSIC_LIBRARY_ENTRY
        ).values_list("entity_uuid", flat=True)
    )
    library_entry_ids.update(
        FlacIngestItem.objects.filter(
            applied_at__isnull=False,
            recording__music_library_entry__isnull=False,
        ).values_list("recording__music_library_entry__pk", flat=True)
    )
    assertions = flac_assertions.filter(
        status=VerificationStatus.UNVERIFIED,
    ).order_by("pk")
    count = 0
    for assertion in assertions.iterator(chunk_size=200):
        AssertionDecision.objects.create(
            assertion=assertion,
            decision=VerificationStatus.CONFIRMED,
            decided_by=user,
            note="Manuelt bekreftet som tidligere anvendt FLAC-kildedata.",
        )
        assertion.status = VerificationStatus.CONFIRMED
        assertion.save(update_fields=("status",))
        count += 1
    for entry in MusicLibraryEntry.objects.filter(pk__in=library_entry_ids):
        if entry.verification_status != VerificationStatus.CONFIRMED:
            entry.verification_status = VerificationStatus.CONFIRMED
            entry.save(update_fields=("verification_status",))
    return count


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
        _artist_identity(name)
        if role == RecordingContribution.Role.PRIMARY
        else None
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


def _source_record_reference(item):
    """Build a bounded source ID while retaining a useful path locator."""
    external_record_id = f"flac:{item.batch_id}:{item.pk}"
    relative_path = item.relative_path
    source_locator = (
        relative_path
        if len(relative_path) <= 500
        else f"{relative_path[:244]}…{relative_path[-255:]}"
    )
    return external_record_id, source_locator


def _record_reported_isrc_collision(
    *, recording, conflicting_recording, reported_isrc, source_record, user
):
    """Preserve a bad source ISRC without violating canonical uniqueness."""
    first, second = sorted(
        (recording, conflicting_recording), key=lambda value: str(value.pk)
    )
    candidate, _ = DuplicateCandidate.objects.get_or_create(
        recording_a=first,
        recording_b=second,
        defaults={
            "signals": [
                "reported_isrc_collision",
                "manual_separate_recordings",
            ],
            "score": 100,
            "status": DuplicateCandidate.Status.DISMISSED,
            "notes": (
                f"Manuelt beholdt som ulike innspillinger selv om kildefilen "
                f"rapporterer ISRC {normalize_isrc(reported_isrc)}."
            ),
        },
    )
    if candidate.status != DuplicateCandidate.Status.DISMISSED:
        candidate.status = DuplicateCandidate.Status.DISMISSED
        candidate.signals = list(
            dict.fromkeys(
                [
                    *(candidate.signals or []),
                    "reported_isrc_collision",
                    "manual_separate_recordings",
                ]
            )
        )
        candidate.notes = (
            f"Manuelt beholdt som ulike innspillinger selv om kildefilen "
            f"rapporterer ISRC {normalize_isrc(reported_isrc)}."
        )
        candidate.save(update_fields=("status", "signals", "notes"))
    assertion = MetadataAssertion.objects.create(
        source_record=source_record,
        entity_type=MetadataAssertion.EntityType.RECORDING,
        entity_uuid=recording.pk,
        field_name="reported_isrc",
        raw_value=str(reported_isrc),
        normalized_value=normalize_isrc(reported_isrc),
        status=VerificationStatus.DISPUTED,
    )
    AssertionDecision.objects.create(
        assertion=assertion,
        decision=VerificationStatus.DISPUTED,
        decided_by=user,
        note=(
            "Kildeverdien kolliderer med en annen innspilling og er derfor ikke "
            "lagret som kanonisk ISRC. Innspillingene skal forbli adskilt."
        ),
    )
    return candidate


def _verify_item_source(item):
    """Fail before canonical writes if the preview no longer matches the file."""
    _root, path = resolve_music_path(item.relative_path)
    try:
        before = path.stat()
        checksum = file_sha256(path)
        after = path.stat()
    except OSError as error:
        raise SourceFileUnavailable(
            f"Filen er ikke tilgjengelig lenger: {error}"
        ) from error
    if (before.st_size, before.st_mtime_ns) != (
        after.st_size,
        after.st_mtime_ns,
    ):
        raise SourceFileUnavailable(
            "Filen ble endret mens den ble kontrollert. Skann den på nytt."
        )
    expected_modified = item.source_modified_at
    actual_modified = datetime.fromtimestamp(
        after.st_mtime, tz=timezone.get_current_timezone()
    )
    if (
        item.file_size != after.st_size
        or not expected_modified
        or abs((expected_modified - actual_modified).total_seconds()) >= 0.001
        or (item.sha256 and checksum != item.sha256)
    ):
        raise SourceFileUnavailable(
            "Filen er endret siden forhåndsvisningen. Skann den på nytt før import."
        )
    return path


@transaction.atomic
def review_item(item, *, parsed, resolution, user, note=""):
    """Approve corrected interpreted metadata without changing the source FLAC."""
    item = (
        FlacIngestItem.objects.select_for_update(of=("self",))
        .select_related("recording", "file_asset__recording")
        .get(pk=item.pk)
    )
    if item.applied_at:
        raise ValidationError(
            "Filen er allerede brukt og kan ikke kontrolleres på nytt."
        )
    if item.action != FlacIngestItem.Action.CONFLICT:
        raise ValidationError(
            "Bare filer merket «Må kontrolleres» kan godkjennes her."
        )

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
            raise ValidationError(
                "Den valgte innspillingen finnes ikke lenger."
            )
    elif resolution != "new":
        raise ValidationError(
            "Velg om filen skal bruke en eksisterende innspilling."
        )

    p7uuid = parsed.get("p7uuid")
    if p7uuid and (not recording or str(recording.pk) != p7uuid):
        raise ValidationError(
            "P7UUID må peke til den valgte innspillingen. Korriger eller tøm feltet."
        )
    isrc_collision_recording = None
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
            if resolution == "new":
                isrc_collision_recording = linked_recording_id
            else:
                raise ValidationError(
                    "Kildedataene bruker en ISRC som allerede er knyttet til en annen "
                    "innspilling. Velg den innspillingen, eller opprett en ny "
                    "innspilling med ISRC-en bevart som en rapportert konflikt."
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
        FlacIngestItem.Action.MATCHED
        if recording
        else FlacIngestItem.Action.NEW
    )
    item.match_method = (
        "manual_isrc_collision"
        if isrc_collision_recording
        else "manual_review"
    )
    item.messages = []
    item.reviewed_by = user
    item.reviewed_at = timezone.now()
    item.review_note = note.strip()
    item.save()
    return item


@transaction.atomic
def apply_item(item, *, user):
    item = (
        FlacIngestItem.objects.select_for_update(of=("self",))
        .select_related(
            "batch__import_batch__source_system", "recording", "file_asset"
        )
        .get(pk=item.pk)
    )
    if not item.can_apply:
        return item
    _verify_item_source(item)
    parsed = item.parsed_metadata
    external_record_id, source_locator = _source_record_reference(item)
    source_record = SourceRecord.objects.create(
        source_system=item.batch.import_batch.source_system,
        import_batch=item.batch.import_batch,
        external_record_id=external_record_id,
        source_locator=source_locator,
        raw_payload={
            "relative_path": item.relative_path,
            "tags": item.raw_tags,
            "parsed": parsed,
            "technical": item.technical_metadata,
            "sha256": item.sha256,
        },
    )
    recording = item.recording
    if (
        recording is None
        and item.batch.allow_uuid_recovery
        and item.match_method == "p7uuid_recovery"
        and parsed.get("p7uuid")
    ):
        recording = (
            Recording.objects.select_for_update()
            .filter(pk=parsed["p7uuid"])
            .first()
        )
    # Several files in the same preview may carry the same previously unseen
    # ISRC. The first applied row creates the Recording; later rows must
    # re-check the identifier boundary instead of creating a duplicate.
    if (
        recording is None
        and parsed.get("isrc")
        and item.match_method != "manual_isrc_collision"
    ):
        normalized_isrc = normalize_isrc(parsed["isrc"])
        identifier = (
            ExternalIdentifier.objects.select_for_update(of=("self",))
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
        recording_values = {
            "title": parsed["title"],
            "duration_ms": item.technical_metadata.get("duration_ms"),
            "recording_kind": Recording.Kind.SOUND,
        }
        if (
            item.batch.allow_uuid_recovery
            and item.match_method == "p7uuid_recovery"
            and parsed.get("p7uuid")
        ):
            recording_values["pk"] = parsed["p7uuid"]
        recording = Recording.objects.create(**recording_values)
        title_assertion = _assertion(
            source_record,
            MetadataAssertion.EntityType.RECORDING,
            recording,
            "title",
            parsed["title"],
            user=user,
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
        if (
            not is_new
            and parsed.get("title")
            and recording.title != parsed["title"]
        ):
            before, base = recording.title, recording.revision
            assertion = _assertion(
                source_record,
                MetadataAssertion.EntityType.RECORDING,
                recording,
                "title",
                parsed["title"],
                user=user,
            )
            recording.title = parsed["title"]
            recording.save(update_fields=("title",))
            _log_applied(
                assertion, recording, before, recording.title, user, base
            )
        if item.technical_metadata.get("duration_ms") and (
            is_new
            or recording.duration_ms != item.technical_metadata["duration_ms"]
        ):
            recording.duration_ms = item.technical_metadata["duration_ms"]
            recording.save(update_fields=("duration_ms",))
        file_isrc = parsed.get("isrc", "")
        if file_isrc and not _recording_isrc(recording):
            linked_identifier = (
                ExternalIdentifier.objects.filter(
                    scheme=ExternalIdentifier.Scheme.ISRC,
                    namespace="",
                    normalized_value=normalize_isrc(file_isrc),
                )
                .select_related("recording")
                .first()
            )
            if not linked_identifier:
                ExternalIdentifier.objects.create(
                    recording=recording,
                    scheme=ExternalIdentifier.Scheme.ISRC,
                    value=file_isrc,
                )
            elif linked_identifier.recording_id != recording.pk:
                _record_reported_isrc_collision(
                    recording=recording,
                    conflicting_recording=linked_identifier.recording,
                    reported_isrc=file_isrc,
                    source_record=source_record,
                    user=user,
                )
        for artist in parsed.get("artists", []):
            _credit(
                recording,
                RecordingContribution.Role.PRIMARY,
                artist,
                source_record,
            )
        for field, role in (
            ("composers", RecordingContribution.Role.COMPOSER),
            ("lyricists", RecordingContribution.Role.LYRICIST),
            ("arrangers", RecordingContribution.Role.ARRANGER),
        ):
            for name in parsed.get(field, []):
                _credit(recording, role, name, source_record)
    release = (
        None if catalogue_authority else _resolve_or_create_release(parsed)
    )
    if release and not catalogue_authority:
        _attach_release_cover(release, parsed)
    known_track = item.file_asset.release_track if item.file_asset else None
    release_track = (
        known_track
        if known_track and release and known_track.release_id == release.pk
        else _release_track(
            release,
            recording,
            parsed,
            item.technical_metadata.get("duration_ms"),
        )
    )
    entry, _ = MusicLibraryEntry.objects.get_or_create(recording=recording)
    # Radio metadata is an exact snapshot of the FLAC tags for an already
    # registered radio file. Missing tags therefore clear earlier values on a
    # re-read; otherwise deleted OneTagger values would remain in the database.
    existing_file_refresh = item.file_asset_id is not None
    scalar_fields = {
        "genre": parsed.get("genre"),
        "language": parsed.get("language"),
        "energy": parsed.get("energy"),
        "gender": parsed.get("gender"),
        "rotation_suitability": parsed.get("rotation_suitability"),
    }
    for field, after in scalar_fields.items():
        if field not in parsed and not existing_file_refresh:
            continue
        before, base = getattr(entry, field), entry.revision
        assertion = _assertion(
            source_record,
            MetadataAssertion.EntityType.MUSIC_LIBRARY_ENTRY,
            entry,
            field,
            after,
            user=user,
        )
        if before != after:
            setattr(
                entry, field, after if field == "energy" else (after or "")
            )
            entry.save(update_fields=(field,))
            _log_applied(
                assertion, entry, before, getattr(entry, field), user, base
            )
    for field, model, through, relation in (
        ("channels", Channel, MusicLibraryChannel, "channels"),
        (
            "target_audiences",
            TargetAudience,
            MusicLibraryTargetAudience,
            "target_audiences",
        ),
    ):
        if field not in parsed and not existing_file_refresh:
            continue
        values = parsed.get(field, [])
        before = _sync_library_relations(
            entry, model, through, relation, values
        )
        base = entry.revision
        entry.save()
        assertion = _assertion(
            source_record,
            MetadataAssertion.EntityType.MUSIC_LIBRARY_ENTRY,
            entry,
            field,
            values,
            user=user,
        )
        _log_applied(assertion, entry, before, values, user, base)
    if entry.verification_status != VerificationStatus.CONFIRMED:
        entry.verification_status = VerificationStatus.CONFIRMED
        entry.save(update_fields=("verification_status",))
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
    asset = location.asset if location else item.file_asset
    if asset and asset.recording_id not in {None, recording.pk}:
        raise ValidationError(
            "Filplasseringen er allerede knyttet til en annen innspilling."
        )
    if not asset:
        technical_metadata = dict(item.technical_metadata)
        if item.match_method == "manual_isrc_collision":
            technical_metadata["manual_recording_assignment"] = {
                "recording_uuid": str(recording.pk),
                "decided_by": user.pk,
                "decided_at": timezone.now().isoformat(),
                "reason": "reported_isrc_collision",
            }
        asset = FileAsset.objects.create(
            recording=recording,
            release_track=release_track,
            filename=absolute.name,
            mime_type="audio/flac",
            size_bytes=item.file_size,
            sha256=item.sha256,
            role=FileAsset.Role.RADIO_FLAC,
            technical_metadata=technical_metadata,
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
        if location is None:
            for previous in asset.locations.filter(is_current=True):
                previous.is_current = False
                previous.status = FileLocation.Status.MOVED
                previous.ended_at = timezone.now()
                previous.save(
                    update_fields=("is_current", "status", "ended_at")
                )
            location = FileLocation.objects.create(
                asset=asset,
                storage_type=FileLocation.StorageType.NAS,
                relative_path=item.relative_path,
                status=FileLocation.Status.ACTIVE,
                verification_status=FileLocation.VerificationStatus.VERIFIED,
            )
        elif location.status != FileLocation.Status.ACTIVE:
            location.status = FileLocation.Status.ACTIVE
            location.verification_status = (
                FileLocation.VerificationStatus.VERIFIED
            )
            location.save(update_fields=("status", "verification_status"))
        asset.recording = recording
        asset.release_track = asset.release_track or release_track
        asset.filename = absolute.name
        asset.mime_type = "audio/flac"
        asset.size_bytes = item.file_size
        asset.sha256 = item.sha256
        manual_assignment = (asset.technical_metadata or {}).get(
            "manual_recording_assignment"
        )
        asset.technical_metadata = dict(item.technical_metadata)
        if manual_assignment:
            asset.technical_metadata["manual_recording_assignment"] = (
                manual_assignment
            )
        asset.metadata_read_at = timezone.now()
        asset.source_modified_at = item.source_modified_at
        asset.sync_status = FileAsset.SyncStatus.SYNCED
        asset.sync_error = ""
        asset.save()
    if catalogue_authority and _catalogue_metadata_differs(asset, parsed):
        asset.sync_status = FileAsset.SyncStatus.PENDING
        asset.sync_requested_at = timezone.now()
        asset.sync_error = (
            "Katalogmetadata avviker fra radio-FLAC. Filen er ikke endret."
        )
        asset.save(
            update_fields=("sync_status", "sync_requested_at", "sync_error")
        )
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
    return item


def apply_batch(batch, *, user):
    applied = 0
    recording_ids = set()
    for item in batch.items.order_by("relative_path"):
        if item.can_apply:
            try:
                applied_item = apply_item(item, user=user)
            except SourceFileUnavailable as error:
                item.refresh_from_db()
                item.action = FlacIngestItem.Action.RETRY
                item.messages = list(error.messages)
                item.save(update_fields=("action", "messages"))
            except ValidationError as error:
                item.refresh_from_db()
                item.action = FlacIngestItem.Action.CONFLICT
                item.messages = list(error.messages)
                item.save(update_fields=("action", "messages"))
            except IntegrityError:
                logger.warning(
                    "Datakonflikt ved FLAC-import av %s",
                    item.relative_path,
                    exc_info=True,
                )
                item.refresh_from_db()
                item.action = FlacIngestItem.Action.CONFLICT
                item.messages = [
                    "Dataene kolliderer med en eksisterende katalogpost. "
                    "Kontroller identifikatorer og valgt innspilling."
                ]
                item.save(update_fields=("action", "messages"))
            else:
                applied += 1
                if applied_item.recording_id:
                    recording_ids.add(applied_item.recording_id)
    for recording_id in recording_ids:
        establish_current_radio_if_unambiguous(recording_id)
    batch.refresh_from_db()
    remaining = (
        batch.items.filter(applied_at__isnull=True)
        .exclude(action=FlacIngestItem.Action.UNCHANGED)
        .exists()
    )
    batch.status = (
        FlacIngestBatch.Status.PARTIAL
        if remaining
        else FlacIngestBatch.Status.APPLIED
    )
    batch.save(update_fields=("status",))
    return applied


def preview_radio_file_split(*, asset_id):
    """Read and validate the consequences of a file split without mutations."""
    asset = (
        FileAsset.objects.select_related("recording", "release_track")
        .prefetch_related("locations")
        .get(pk=asset_id)
    )
    if asset.role != FileAsset.Role.RADIO_FLAC or not asset.recording_id:
        raise ValidationError(
            "Bare en radio-FLAC med innspillingskobling kan skilles ut."
        )
    location = next(
        (
            value
            for value in asset.locations.all()
            if value.is_current
            and value.storage_type == FileLocation.StorageType.NAS
            and value.status == FileLocation.Status.ACTIVE
        ),
        None,
    )
    if not location:
        raise ValidationError(
            "Filen har ingen aktiv NAS-plassering som kan leses."
        )
    _root, path = resolve_music_path(location.relative_path)
    try:
        snapshot, checksum, stable_stat = _stable_snapshot(path)
    except (FlacReadError, FileChangedDuringScan, OSError) as error:
        raise ValidationError(
            f"Filen kunne ikke leses sikkert: {error}"
        ) from error
    parsed = _with_release_folder_context(
        snapshot.parsed, location.relative_path
    )
    errors = _metadata_errors(parsed)
    if errors:
        raise ValidationError(errors)
    other_files = list(
        asset.recording.file_assets.filter(role=FileAsset.Role.RADIO_FLAC)
        .exclude(pk=asset.pk)
        .prefetch_related("locations")
    )
    if not other_files:
        raise ValidationError(
            "Innspillingen har ikke flere radiofiler. Det er derfor ingenting å skille ut."
        )
    blocked_reason = ""
    if (
        ManagedRecording.objects.filter(
            library_entry__recording=asset.recording
        ).exists()
        or RightsClaim.objects.filter(recording=asset.recording).exists()
    ):
        blocked_reason = (
            "Innspillingen har forvaltnings- eller rettighetsdata. Avklar hvilken "
            "innspilling disse gjelder før filkoblingen deles."
        )
    elif (
        asset.release_track
        and asset.release_track.file_assets.exclude(pk=asset.pk).exists()
    ):
        blocked_reason = (
            "Flere filer er knyttet til samme sporforekomst. Koble filene manuelt "
            "før én av dem skilles ut."
        )
    return {
        "asset": asset,
        "location": location,
        "snapshot": snapshot,
        "checksum": checksum,
        "stable_stat": stable_stat,
        "parsed": parsed,
        "other_files": other_files,
        "blocked_reason": blocked_reason,
    }


def split_radio_file_to_new_recording(*, asset_id, user):
    """Move one wrongly grouped radio-FLAC to a new Recording, read-only.

    The operation is intended for a known source-identifier collision. It
    preserves the reported ISRC as disputed provenance, moves the applicable
    ReleaseTrack, and records an explicit keep-separate decision used by later
    re-scans. No bytes are written to the FLAC.
    """
    preview = preview_radio_file_split(asset_id=asset_id)
    asset = preview["asset"]
    location = preview["location"]
    snapshot = preview["snapshot"]
    checksum = preview["checksum"]
    stable_stat = preview["stable_stat"]
    parsed = preview["parsed"]
    if preview["blocked_reason"]:
        raise ValidationError(preview["blocked_reason"])

    with transaction.atomic():
        asset = (
            FileAsset.objects.select_for_update(of=("self",))
            .select_related("recording", "release_track")
            .get(pk=asset.pk)
        )
        old_recording = Recording.objects.select_for_update().get(
            pk=asset.recording_id
        )
        if (
            old_recording.file_assets.filter(
                role=FileAsset.Role.RADIO_FLAC
            ).count()
            < 2
        ):
            raise ValidationError(
                "Innspillingen har ikke flere radiofiler. Det er derfor ingenting å skille ut."
            )
        if (
            ManagedRecording.objects.filter(
                library_entry__recording=old_recording
            ).exists()
            or RightsClaim.objects.filter(recording=old_recording).exists()
        ):
            raise ValidationError(
                "Innspillingen har forvaltnings- eller rettighetsdata. Avklar hvilken "
                "innspilling disse gjelder før filkoblingen deles."
            )
        release_track = asset.release_track
        if (
            release_track
            and release_track.file_assets.exclude(pk=asset.pk).exists()
        ):
            raise ValidationError(
                "Flere filer er knyttet til samme sporforekomst. Koble filene manuelt "
                "før én av dem skilles ut."
            )

        recording = Recording.objects.create(
            title=parsed["title"],
            duration_ms=snapshot.technical.get("duration_ms"),
            recording_kind=Recording.Kind.SOUND,
        )
        source = _source_system()
        import_batch = ImportBatch.objects.create(
            source_system=source,
            notes=f"Manuell utskilling av radio-FLAC: {location.relative_path}",
        )
        ingest_batch = FlacIngestBatch.objects.create(
            import_batch=import_batch,
            relative_root=".",
            recursive=False,
            status=FlacIngestBatch.Status.APPLIED,
            created_by=user,
        )
        item = FlacIngestItem.objects.create(
            batch=ingest_batch,
            relative_path=location.relative_path,
            action=FlacIngestItem.Action.UPDATED,
            match_method="manual_file_split",
            raw_tags=snapshot.raw_tags,
            parsed_metadata=parsed,
            technical_metadata=snapshot.technical,
            file_size=stable_stat.st_size,
            source_modified_at=datetime.fromtimestamp(
                stable_stat.st_mtime, tz=timezone.get_current_timezone()
            ),
            sha256=checksum,
            recording=recording,
            release=release_track.release if release_track else None,
            release_track=release_track,
            file_asset=asset,
            reviewed_by=user,
            reviewed_at=timezone.now(),
            review_note="Radiofil manuelt skilt ut som egen innspilling.",
            applied_at=timezone.now(),
        )
        external_record_id, source_locator = _source_record_reference(item)
        source_record = SourceRecord.objects.create(
            source_system=source,
            import_batch=import_batch,
            external_record_id=external_record_id,
            source_locator=source_locator,
            raw_payload={
                "relative_path": location.relative_path,
                "tags": snapshot.raw_tags,
                "parsed": parsed,
                "technical": snapshot.technical,
                "sha256": checksum,
                "manual_action": "split_radio_file",
            },
        )
        item.source_record = source_record
        item.save(update_fields=("source_record",))

        title_assertion = _assertion(
            source_record,
            MetadataAssertion.EntityType.RECORDING,
            recording,
            "title",
            parsed["title"],
            user=user,
        )
        _log_applied(
            title_assertion,
            recording,
            "",
            recording.title,
            user,
            recording.revision,
        )
        for artist in parsed.get("artists", []):
            _credit(
                recording,
                RecordingContribution.Role.PRIMARY,
                artist,
                source_record,
            )
        for field, role in (
            ("composers", RecordingContribution.Role.COMPOSER),
            ("lyricists", RecordingContribution.Role.LYRICIST),
            ("arrangers", RecordingContribution.Role.ARRANGER),
        ):
            for name in parsed.get(field, []):
                _credit(recording, role, name, source_record)

        reported_isrc = parsed.get("isrc", "")
        linked_identifier = None
        if reported_isrc:
            linked_identifier = (
                ExternalIdentifier.objects.filter(
                    scheme=ExternalIdentifier.Scheme.ISRC,
                    namespace="",
                    normalized_value=normalize_isrc(reported_isrc),
                )
                .select_related("recording")
                .first()
            )
            if not linked_identifier:
                ExternalIdentifier.objects.create(
                    recording=recording,
                    scheme=ExternalIdentifier.Scheme.ISRC,
                    value=reported_isrc,
                )
            elif linked_identifier.recording_id != recording.pk:
                _record_reported_isrc_collision(
                    recording=recording,
                    conflicting_recording=linked_identifier.recording,
                    reported_isrc=reported_isrc,
                    source_record=source_record,
                    user=user,
                )

        entry = MusicLibraryEntry.objects.create(
            recording=recording,
            genre=parsed.get("genre", ""),
            language=parsed.get("language", ""),
            energy=parsed.get("energy"),
            gender=parsed.get("gender", ""),
            rotation_suitability=parsed.get("rotation_suitability", ""),
            verification_status=VerificationStatus.CONFIRMED,
        )
        for field in (
            "genre",
            "language",
            "energy",
            "gender",
            "rotation_suitability",
        ):
            if field not in parsed:
                continue
            value = parsed.get(field)
            assertion = _assertion(
                source_record,
                MetadataAssertion.EntityType.MUSIC_LIBRARY_ENTRY,
                entry,
                field,
                value,
                user=user,
            )
            _log_applied(assertion, entry, "", value, user, entry.revision)
        for field, model, through, relation in (
            ("channels", Channel, MusicLibraryChannel, "channels"),
            (
                "target_audiences",
                TargetAudience,
                MusicLibraryTargetAudience,
                "target_audiences",
            ),
        ):
            values = parsed.get(field, [])
            _sync_library_relations(entry, model, through, relation, values)
            assertion = _assertion(
                source_record,
                MetadataAssertion.EntityType.MUSIC_LIBRARY_ENTRY,
                entry,
                field,
                values,
                user=user,
            )
            _log_applied(assertion, entry, [], values, user, entry.revision)

        if release_track:
            release_track.recording = recording
            if parsed.get("title") and (
                not release_track.title_override
                or release_track.title_override == old_recording.title
            ):
                release_track.title_override = parsed["title"]
            release_track.save(update_fields=("recording", "title_override"))

        technical_metadata = dict(snapshot.technical)
        technical_metadata["manual_recording_assignment"] = {
            "recording_uuid": str(recording.pk),
            "decided_by": user.pk,
            "decided_at": timezone.now().isoformat(),
            "reason": "split_radio_file",
        }
        asset.recording = recording
        asset.technical_metadata = technical_metadata
        asset.size_bytes = stable_stat.st_size
        asset.sha256 = checksum
        asset.metadata_read_at = timezone.now()
        asset.source_modified_at = datetime.fromtimestamp(
            stable_stat.st_mtime, tz=timezone.get_current_timezone()
        )
        asset.sync_status = FileAsset.SyncStatus.SYNCED
        asset.sync_error = ""
        asset.save()
        FileChecksum.objects.get_or_create(
            asset=asset,
            sha256=checksum,
            defaults={"reason": FileChecksum.Reason.INGEST},
        )
        remaining_assets = list(
            old_recording.file_assets.filter(role=FileAsset.Role.RADIO_FLAC)
            .exclude(pk=asset.pk)
            .prefetch_related("locations")
        )
    return recording, old_recording, remaining_assets


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
                    primary.credited_as or str(primary.party)
                    if primary
                    else ""
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


def _catalogue_metadata_differs(asset, parsed):
    """Compare the writable catalogue subset without treating P7UUID as required."""
    expected = _catalogue_tags(asset)

    def scalar(value):
        return str(value or "").strip().casefold()

    if scalar(parsed.get("title")) != scalar(expected.get("TITLE")):
        return True
    try:
        source_isrc = (
            normalize_isrc(parsed["isrc"]) if parsed.get("isrc") else ""
        )
    except ValidationError:
        return True
    if source_isrc != expected.get("ISRC", ""):
        return True
    if scalar((parsed.get("artists") or [""])[0]) != scalar(
        expected.get("ARTIST")
    ):
        return True
    for field, tag in (
        ("composers", "COMPOSER"),
        ("lyricists", "LYRICIST"),
        ("arrangers", "ARRANGER"),
    ):
        actual = [scalar(value) for value in parsed.get(field, [])]
        wanted = [scalar(value) for value in expected.get(tag, [])]
        if actual != wanted:
            return True
    if asset.release_track_id:
        for field, tag in (
            ("album", "ALBUM"),
            ("track_number", "TRACKNUMBER"),
            ("disc_number", "DISCNUMBER"),
            ("date", "DATE"),
            ("catalogue_number", "CATALOGNUMBER"),
            ("barcode", "BARCODE"),
        ):
            if scalar(parsed.get(field)) != scalar(expected.get(tag)):
                return True
    return False


def _asset_path(asset):
    location = asset.locations.filter(
        storage_type=FileLocation.StorageType.NAS, is_current=True
    ).first()
    if not location:
        return None
    _root, path = resolve_music_path(location.relative_path)
    return path


def sync_file_asset(asset_id, *, p7uuid_only=False):
    asset = FileAsset.objects.select_related(
        "recording", "release_track__release"
    ).get(pk=asset_id)
    if asset.role != FileAsset.Role.RADIO_FLAC or not asset.recording_id:
        return None
    if not getattr(settings, "P7_ALLOW_FILE_WRITES", False):
        if asset.sync_status != FileAsset.SyncStatus.PENDING:
            asset.sync_status = FileAsset.SyncStatus.PENDING
            asset.sync_requested_at = timezone.now()
            asset.sync_error = ""
            asset.save(
                update_fields=(
                    "sync_status",
                    "sync_requested_at",
                    "sync_error",
                )
            )
        return None
    if not p7uuid_only and not database_is_catalogue_authority(
        asset.recording
    ):
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
            raise FileNotFoundError(
                "Radio-FLAC finnes ikke på registrert plassering."
            )
        _all_tags, protected = write_catalogue_tags(path, values)
        checksum = file_sha256(path)
        stat = path.stat()
        snapshot = read_flac(path)
    except FileNotFoundError as error:
        result = FlacSyncLog.Result.MISSING
        status = FileAsset.SyncStatus.MISSING
        protected = {}
        error_message = str(error)
    except (
        Exception
    ) as error:  # recorded for retry; catalogue change must survive
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
        release_context_version = asset.technical_metadata.get(
            "release_context_version"
        )
        asset.technical_metadata = dict(snapshot.technical)
        if release_context_version:
            asset.technical_metadata["release_context_version"] = (
                release_context_version
            )
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
