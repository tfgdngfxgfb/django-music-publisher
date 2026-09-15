from dataclasses import dataclass

from django.db import transaction
from django.db.models import Count

from .models import (
    DuplicateCandidate,
    ExternalIdentifier,
    Recording,
    RecordingContribution,
    Release,
    ReleaseTrack,
)
from .validators import normalize_isrc


@dataclass(frozen=True)
class RecordingMatch:
    recording: Recording
    signals: tuple[str, ...]
    score: int


def find_recording_candidates(
    *, title="", isrc="", duration_ms=None, artist_identity=None
):
    scores = {}
    signals = {}
    if isrc:
        normalized = normalize_isrc(isrc)
        for recording in Recording.objects.filter(
            identifiers__scheme=ExternalIdentifier.Scheme.ISRC,
            identifiers__normalized_value=normalized,
        ):
            scores[recording.pk] = 100
            signals.setdefault(recording.pk, []).append("samme ISRC")
    if title.strip():
        for recording in Recording.objects.filter(title__iexact=title.strip()):
            scores[recording.pk] = scores.get(recording.pk, 0) + 45
            signals.setdefault(recording.pk, []).append("samme tittel")
            if duration_ms and recording.duration_ms:
                difference = abs(recording.duration_ms - duration_ms)
                if difference <= 3000:
                    scores[recording.pk] += 25
                    signals[recording.pk].append("nær varighet")
    if artist_identity and scores:
        matching_artist = set(
            RecordingContribution.objects.filter(
                recording_id__in=scores, artist_identity=artist_identity
            ).values_list("recording_id", flat=True)
        )
        for recording_id in matching_artist:
            scores[recording_id] += 25
            signals[recording_id].append("samme artist")
    if scores:
        release_counts = (
            ReleaseTrack.objects.filter(recording_id__in=scores)
            .values("recording_id")
            .annotate(total=Count("release_id", distinct=True))
        )
        for item in release_counts:
            signals[item["recording_id"]].append(
                f"finnes på {item['total']} utgivelse(r)"
            )
            scores[item["recording_id"]] += 5
    recordings = Recording.objects.in_bulk(scores)
    return sorted(
        (
            RecordingMatch(
                recordings[key], tuple(signals[key]), min(score, 100)
            )
            for key, score in scores.items()
        ),
        key=lambda match: (
            -match.score,
            match.recording.title,
            str(match.recording.pk),
        ),
    )


def record_duplicate_candidates(recording, matches):
    for match in matches:
        first, second = sorted(
            (recording, match.recording), key=lambda item: str(item.pk)
        )
        DuplicateCandidate.objects.get_or_create(
            recording_a=first,
            recording_b=second,
            defaults={"signals": list(match.signals), "score": match.score},
        )


@transaction.atomic
def resolve_release_track_recording(
    *,
    recording=None,
    new_recording_title="",
    new_isrc="",
    force_create=False,
    artist_identity=None,
    duration_ms=None,
):
    if recording and new_recording_title.strip():
        raise ValueError(
            "Velg eksisterende innspilling eller opprett en ny, ikke begge."
        )
    if not recording and not new_recording_title.strip():
        raise ValueError(
            "Velg en innspilling eller skriv inn tittel for en ny."
        )
    matches = []
    if not recording:
        matches = find_recording_candidates(
            title=new_recording_title,
            isrc=new_isrc,
            duration_ms=duration_ms,
            artist_identity=artist_identity,
        )
        if matches and not force_create:
            names = ", ".join(
                f"{match.recording.title} ({match.recording.pk})"
                for match in matches[:5]
            )
            raise ValueError(f"Mulig eksisterende innspilling: {names}")
        if new_isrc and any(
            "samme ISRC" in match.signals for match in matches
        ):
            raise ValueError(
                "ISRC finnes allerede. Opprett eventuelt den nye innspillingen uten ISRC, "
                "og registrer kodekonflikten som en metadatapåstand."
            )
        recording = Recording.objects.create(
            title=new_recording_title.strip(),
            duration_ms=duration_ms,
        )
        if new_isrc:
            ExternalIdentifier.objects.create(
                recording=recording,
                scheme=ExternalIdentifier.Scheme.ISRC,
                value=new_isrc,
            )
        if artist_identity:
            RecordingContribution.objects.create(
                recording=recording,
                party=artist_identity.party,
                artist_identity=artist_identity,
                role=RecordingContribution.Role.PRIMARY,
                credited_as=artist_identity.display_name,
            )
        record_duplicate_candidates(recording, matches)
    return recording


@transaction.atomic
def create_release_track(
    *,
    release: Release,
    sequence_number: int,
    recording=None,
    new_recording_title="",
    new_isrc="",
    force_create=False,
    artist_identity=None,
    **track_fields,
):
    recording = resolve_release_track_recording(
        recording=recording,
        new_recording_title=new_recording_title,
        new_isrc=new_isrc,
        force_create=force_create,
        artist_identity=artist_identity,
        duration_ms=track_fields.get("duration_ms"),
    )
    return ReleaseTrack.objects.create(
        release=release,
        recording=recording,
        sequence_number=sequence_number,
        **track_fields,
    )
