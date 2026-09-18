"""Read-only suggestions from preloaded facts; never authoritative assignments."""

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class MasterLinkSuggestion:
    track: object | None
    source: object | None
    reason: str
    source_reason: str
    strength: str


def suggest_master_links(masters, tracks, raws, sources):
    """Explicit position first; complete numeric/order proposals second.

    Timestamp order is only a review proposal and requires distinct recorded
    source timestamps. Count mismatch disables every sequence-based proposal.
    Existing database links always win. No filesystem or ORM calls are made.
    """
    tracks = sorted(tracks, key=lambda track: track.sequence_number)
    same_count = len(masters) == len(tracks)
    numbered = {}
    for asset in masters:
        match = re.match(
            r"^(\d+)(?:[\s._-]|$)", PurePosixPath(asset.filename).stem
        )
        if match:
            numbered[asset.pk] = int(match[1])
    numeric_complete = (
        same_count
        and len(numbered) == len(masters)
        and set(numbered.values()) == set(range(1, len(tracks) + 1))
    )
    times = [asset.source_modified_at for asset in masters]
    timed = {}
    if same_count and all(times) and len(set(times)) == len(masters):
        timed = {
            asset.pk: track
            for asset, track in zip(
                sorted(masters, key=lambda asset: asset.source_modified_at),
                tracks,
            )
        }
    proposed = {}
    for asset in masters:
        track = None
        reason = "Velg spor manuelt."
        strength = "Manuell kontroll"
        if asset.release_track_id:
            track = next(
                (t for t in tracks if t.pk == asset.release_track_id), None
            )
            reason, strength = "Lagret sporkobling.", "Lagret"
        else:
            stem = PurePosixPath(asset.filename).stem
            explicit = re.match(
                r"^(?:(\d+)[.-])?([A-Da-d])(\d+)(?:[\s._-]|$)", stem
            )
            if explicit:
                disc, side, number = explicit.groups()
                choices = [
                    t
                    for t in tracks
                    if t.side.casefold() == side.casefold()
                    and t.track_number == int(number)
                    and (not disc or t.disc_number == int(disc))
                ]
                if len(choices) == 1:
                    track = choices[0]
                    reason, strength = (
                        f"Side/spornummer {side.upper()}{int(number)} i filnavnet.",
                        "Tydelig forslag",
                    )
                else:
                    reason = "Side/spornummer mangler eller er tvetydig i sporlisten."
            elif numeric_complete:
                track = tracks[numbered[asset.pk] - 1]
                reason, strength = (
                    "Fullstendig nummerert filrekkefølge mot sporlisten.",
                    "Kontroller rekkefølge",
                )
            elif asset.pk in timed:
                track = timed[asset.pk]
                reason, strength = (
                    "Filens lagrede endringstid mot sporrekkefølge; ikke sikker opprettelsesdato.",
                    "Kontroller rekkefølge",
                )
            elif not same_count:
                reason = "Ulikt antall mastere og spor. Rekkefølgeforslag er slått av."
        if (
            track
            and asset.recording_id
            and asset.recording_id != track.recording_id
        ):
            track, reason, strength = (
                None,
                "Forslaget avviker fra lagret innspilling. Velg manuelt.",
                "Manuell kontroll",
            )
        proposed[asset.pk] = (track, reason, strength)
    counts = Counter(t.pk for t, _, _ in proposed.values() if t)
    result = {}
    for asset in masters:
        track, reason, strength = proposed[asset.pk]
        if track and counts[track.pk] > 1 and not asset.release_track_id:
            track, reason, strength = (
                None,
                "Flere mastere peker mot samme spor. Velg manuelt.",
                "Manuell kontroll",
            )
        source = sources.get(asset.pk)
        source_reason = (
            "Lagret råkobling." if source else "Velg RAW-kilde manuelt."
        )
        if not source and track:
            choices = []
            for raw in raws:
                sides = set(
                    re.findall(
                        r"(?:^|[\s_.+-])(?:side[\s_.-]*)?([A-D])(?=$|[\s_.+-])",
                        PurePosixPath(raw.filename).stem,
                        re.I,
                    )
                )
                sides = {side.upper() for side in sides}
                if track.side and track.side.upper() in sides:
                    choices.append(raw)
                elif len(raws) == 1 and not sides:
                    choices.append(raw)
            if len(choices) == 1:
                source = choices[0]
                source_reason = (
                    "Samme side i RAW-filnavnet."
                    if len(raws) > 1
                    else "Eneste forenlige RAW-kilde; kontroller innholdet."
                )
        result[asset.pk] = MasterLinkSuggestion(
            track, source, reason, source_reason, strength
        )
    return result
