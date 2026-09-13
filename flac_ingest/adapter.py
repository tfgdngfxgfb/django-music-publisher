"""Translate FLAC/Vorbis comments to and from P7's normalized model."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
import unicodedata
from uuid import UUID

from mutagen import MutagenError
from mutagen.flac import FLAC, FLACNoHeaderError

TAG_ADAPTER_VERSION = 2

TAG_ALIASES = {
    "title": ("TITLE",),
    "artists": ("ARTIST",),
    "album_artist": ("ALBUMARTIST", "ALBUM ARTIST"),
    "album": ("ALBUM",),
    "track_number": ("TRACKNUMBER", "TRACK"),
    "disc_number": ("DISCNUMBER", "DISC"),
    "date": ("DATE", "YEAR"),
    "isrc": ("ISRC",),
    "composers": ("COMPOSER",),
    "lyricists": ("LYRICIST", "LYRICS_BY"),
    "arrangers": ("ARRANGER",),
    "barcode": ("BARCODE", "UPC", "EAN", "GTIN"),
    "catalogue_number": (
        "CATALOGNUMBER",
        "CATALOGUENUMBER",
        "CATALOGUE_NUMBER",
        "CATALOG_NUM",
    ),
    "p7uuid": ("P7UUID",),
    "genre": ("GENRE",),
    "language": ("LANGUAGE",),
    # OneTagger RATING is P7 Energy. ENERGY remains a read alias for older files.
    "energy": ("RATING", "ENERGY"),
    "channels": ("KANAL", "CHANNEL", "CHANNELS"),
    "target_audiences": ("TARGET", "TARGETAUDIENCE", "MÅLGRUPPE"),
    "gender": ("GENDER",),
}

RADIO_FIELDS = {
    "genre",
    "language",
    "energy",
    "channels",
    "target_audiences",
    "gender",
}
CATALOGUE_WRITE_TAGS = {
    "TITLE",
    "ARTIST",
    "ISRC",
    "COMPOSER",
    "LYRICIST",
    "ARRANGER",
    "P7UUID",
    "ALBUM",
    "ALBUMARTIST",
    "TRACKNUMBER",
    "DISCNUMBER",
    "DATE",
    "BARCODE",
    "CATALOGNUMBER",
}

COMMENT_TXXX_ALIASES = {
    "sprak": "LANGUAGE",
    "language": "LANGUAGE",
    "kanal": "KANAL",
    "channel": "KANAL",
    "target": "TARGET",
    "targetaudience": "TARGET",
    "malgruppe": "TARGET",
    "gender": "GENDER",
    "kjonn": "GENDER",
}

LANGUAGE_NAMES = {
    "norsk": "no",
    "svensk": "sv",
    "engelsk": "en",
    "hebraisk": "he",
    "samisk": "smi",
}


class FlacReadError(ValueError):
    pass


class FlacWriteError(ValueError):
    pass


@dataclass(frozen=True)
class FlacSnapshot:
    raw_tags: dict[str, list[str]]
    parsed: dict
    technical: dict


def _casefolded(raw_tags):
    folded = {}
    for key, values in raw_tags.items():
        folded.setdefault(key.upper(), []).extend(values)
    return folded


def _normalized_label(value):
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]", "", ascii_value.casefold())


def _with_embedded_txxx(tags):
    """Expose StationPlaylist TXXX comments to the normal tag adapter.

    Some existing P7 FLAC files contain values such as
    ``TXXX:Kanal - P7 Riks`` as separate COMMENT values. The original COMMENT
    values stay untouched in ``raw_tags``; this only creates an adapter view.
    """
    enriched = {key: list(values) for key, values in tags.items()}
    for comment in tags.get("COMMENT", []):
        for line in str(comment).splitlines():
            match = re.match(
                r"^\s*TXXX\s*:\s*(.+?)\s*(?:\s+-\s+|\s*=\s*)(.*?)\s*$",
                line,
                flags=re.IGNORECASE,
            )
            if not match:
                continue
            target = COMMENT_TXXX_ALIASES.get(_normalized_label(match.group(1)))
            value = match.group(2).strip()
            if not target or not value:
                continue
            existing = enriched.setdefault(target, [])
            if value.casefold() not in {item.casefold() for item in existing}:
                existing.append(value)
    return enriched


def _values(tags, aliases):
    result = []
    for alias in aliases:
        result.extend(tags.get(alias, []))
    return [value.strip() for value in result if value.strip()]


def _multi_values(values):
    result = []
    for value in values:
        for part in value.replace("|", ";").split(";"):
            cleaned = part.strip()
            if cleaned and cleaned.casefold() not in {
                item.casefold() for item in result
            }:
                result.append(cleaned)
    return result


def _first(values):
    return values[0] if values else ""


def _position(value):
    try:
        return int(value.split("/", 1)[0].strip())
    except (AttributeError, TypeError, ValueError):
        return None


def normalize_energy(value):
    """Translate direct P7 levels and OneTagger's 20-step rating scale."""
    number = _position(value)
    if number is not None and 1 <= number <= 5:
        return number
    if number in {20, 40, 60, 80, 100}:
        return number // 20
    return None


def _gender(value):
    return {
        "female": "female",
        "kvinne": "female",
        "male": "male",
        "mann": "male",
        "mixed": "mixed",
        "blandet": "mixed",
        "group": "group",
        "gruppe": "group",
        "instrumental": "instrumental",
        "other": "other",
        "annet": "other",
    }.get(value.casefold())


def _language(value):
    return LANGUAGE_NAMES.get(value.casefold(), value)


def file_sha256(path):
    digest = sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_flac(path):
    path = Path(path)
    try:
        audio = FLAC(path)
    except (FLACNoHeaderError, MutagenError, OSError, TypeError, ValueError) as error:
        raise FlacReadError(f"Kunne ikke lese FLAC-filen: {error}") from error
    raw_tags = {
        str(key): [str(value) for value in values]
        for key, values in (audio.tags or {}).items()
    }
    tags = _with_embedded_txxx(_casefolded(raw_tags))
    parsed = {}
    for field, aliases in TAG_ALIASES.items():
        values = _values(tags, aliases)
        if not values and not any(alias in tags for alias in aliases):
            continue
        if field in {
            "artists",
            "composers",
            "lyricists",
            "arrangers",
            "channels",
            "target_audiences",
        }:
            parsed[field] = _multi_values(values)
        elif field == "genre":
            genre_values = _multi_values(values)
            parsed["genre_values"] = genre_values
            parsed[field] = "; ".join(genre_values)
        elif field in {"track_number", "disc_number"}:
            parsed[field] = _position(_first(values))
        elif field == "energy":
            value = _first(values)
            energy = normalize_energy(value)
            if value and energy is None:
                parsed["energy_invalid"] = value
            else:
                parsed[field] = energy
        elif field == "gender":
            value = _first(values)
            gender = _gender(value)
            if value and gender is None:
                parsed["gender_invalid"] = value
            else:
                parsed[field] = gender or ""
        elif field == "language":
            parsed[field] = _language(_first(values))
        else:
            parsed[field] = _first(values)
    p7uuid = parsed.get("p7uuid")
    if p7uuid:
        try:
            parsed["p7uuid"] = str(UUID(p7uuid))
        except ValueError:
            parsed["p7uuid_invalid"] = p7uuid
            parsed.pop("p7uuid", None)
    try:
        info = audio.info
        technical = {
            "tag_adapter_version": TAG_ADAPTER_VERSION,
            "container": "FLAC",
            "codec": "FLAC",
            "sample_rate": info.sample_rate,
            "bits_per_sample": info.bits_per_sample,
            "channels": info.channels,
            "length_seconds": round(info.length, 6),
            "duration_ms": round(info.length * 1000),
        }
    except (AttributeError, TypeError, ValueError) as error:
        raise FlacReadError(
            f"FLAC-filen mangler gyldige tekniske lydopplysninger: {error}"
        ) from error
    return FlacSnapshot(raw_tags=raw_tags, parsed=parsed, technical=technical)


def write_catalogue_tags(path, values):
    """Write an explicit catalogue allowlist and verify every other text tag."""
    path = Path(path)
    try:
        before = FLAC(path)
    except (FLACNoHeaderError, OSError, ValueError) as error:
        raise FlacWriteError(f"Kunne ikke åpne FLAC-filen: {error}") from error
    before_tags = {
        str(key).upper(): [str(value) for value in tag_values]
        for key, tag_values in (before.tags or {}).items()
    }
    for tag, value in values.items():
        tag = tag.upper()
        if tag not in CATALOGUE_WRITE_TAGS:
            raise FlacWriteError(
                f"Taggen {tag} er ikke tillatt for katalogsynkronisering."
            )
        if value in (None, "", []):
            if tag in before:
                del before[tag]
            continue
        before[tag] = (
            [str(item) for item in value] if isinstance(value, list) else [str(value)]
        )
    try:
        before.save()
        after = FLAC(path)
    except (OSError, ValueError) as error:
        raise FlacWriteError(
            f"Kunne ikke lagre eller lese tilbake FLAC-tags: {error}"
        ) from error
    after_tags = {
        str(key).upper(): [str(value) for value in tag_values]
        for key, tag_values in (after.tags or {}).items()
    }
    for tag, value in values.items():
        tag = tag.upper()
        if value in (None, "", []):
            if tag in after_tags:
                raise FlacWriteError(f"Kontroll etter sletting feilet for {tag}.")
            continue
        expected = (
            [str(item) for item in value] if isinstance(value, list) else [str(value)]
        )
        if after_tags.get(tag) != expected:
            raise FlacWriteError(f"Kontroll etter skriving feilet for {tag}.")
    for tag, previous in before_tags.items():
        if tag not in CATALOGUE_WRITE_TAGS and after_tags.get(tag) != previous:
            raise FlacWriteError(f"Den beskyttede taggen {tag} ble endret.")
    protected = {
        tag: values
        for tag, values in after_tags.items()
        if tag not in CATALOGUE_WRITE_TAGS
    }
    return after_tags, protected
