"""Read-only ReleaseTrack matrix; rights remain Recording-level positions."""

from collections import Counter, defaultdict
from django.db.models import Prefetch
from django.utils import timezone
from catalogue.models import RecordingContribution, ReleaseTrack
from managed_music.lifecycle import evaluate_management_state
from managed_music.models import ManagedRecording
from rights.models import RightsClaim
from rights.scope import (
    claim_applies_to_release,
    claim_applies_to_date,
    claim_countries,
    OwnershipCategory,
)
from rights.services import get_local_organization
from rights.summaries import classify_ownership
from rights_core.models import VerificationStatus as Status
from gui_v2.recording_rights import claim_presentation
from gui_v2.managed_music import STATUS_LABELS


def release_tracks(release):
    return list(
        release.tracks.select_related("recording")
        .prefetch_related(
            "recording__identifiers",
            Prefetch(
                "recording__contributions",
                queryset=RecordingContribution.objects.select_related(
                    "party", "artist_identity"
                ).order_by("display_order", "id"),
            ),
        )
        .order_by("sequence_number", "id")
    )


def contextual_basis(claims, kind, release, local, day):
    relevant = [
        c
        for c in claims
        if c.right_type == kind
        and local
        and c.rights_holder_id == local.pk
        and claim_applies_to_release(c, release)
        and c.status in (Status.CONFIRMED, Status.UNVERIFIED, Status.DISPUTED)
        and claim_countries(c)
    ]
    current = [c for c in relevant if claim_applies_to_date(c, day)]
    return {
        "confirmed": sum(c.status == Status.CONFIRMED for c in current),
        "pending": sum(c.status == Status.UNVERIFIED for c in relevant),
        "disputed": sum(c.status == Status.DISPUTED for c in relevant),
        "future": sum(
            c.status == Status.CONFIRMED
            and c.valid_from is not None
            and c.valid_from > day
            for c in relevant
        ),
        "positions": [claim_presentation(c, on_date=day) for c in relevant],
    }


def build_matrix(release, tracks):
    from gui_v2.views import _artist_text

    recordings = {t.recording_id: t.recording for t in tracks}
    grouped = defaultdict(list)
    queryset = (
        RightsClaim.objects.filter(recording_id__in=recordings)
        .select_related("rights_holder", "release_scope")
        .prefetch_related("territories", "decisions")
    )
    for claim in queryset:
        grouped[claim.recording_id].append(claim)
    memberships = {
        m.library_entry.recording_id: m
        for m in ManagedRecording.objects.filter(
            library_entry__recording_id__in=recordings
        ).select_related("library_entry")
    }
    protecting = defaultdict(dict)
    for track in ReleaseTrack.objects.filter(
        recording_id__in=recordings, release__managed_release__isnull=False
    ).select_related("release"):
        protecting[track.recording_id][track.release_id] = track.release
    local, day = get_local_organization(), timezone.localdate()
    placements = defaultdict(list)
    for track in tracks:
        placements[track.recording_id].append(track.sequence_number)
    unique = {}
    for pk, recording in recordings.items():
        claims = grouped[pk]
        managed = memberships.get(pk)
        state = (
            evaluate_management_state(
                managed, claims, local_organization=local, on_date=day
            )
            if managed and local
            else None
        )
        if managed:
            key = (
                "unavailable"
                if not state
                else "uncertain" if state.history_uncertain else state.status
            )
            label = STATUS_LABELS[key]
        else:
            key = (
                "via_current"
                if release.pk in protecting[pk]
                else "via_other" if protecting[pk] else "none"
            )
            label = {
                "via_current": "Kun via denne utgivelsen",
                "via_other": "Kun via annen forvaltet utgivelse",
                "none": "Ikke forvaltet",
            }[key]
        ownership = classify_ownership(claims, local, on_date=day)
        admin = contextual_basis(
            claims, RightsClaim.RightType.ADMINISTRATION, release, local, day
        )
        distribution = contextual_basis(
            claims, RightsClaim.RightType.DISTRIBUTION, release, local, day
        )
        follow_up = []
        if ownership.category in (
            OwnershipCategory.UNRESOLVED,
            OwnershipCategory.DISPUTED,
        ):
            follow_up.append("Avklar eierskap")
        if any(
            c.status == Status.DISPUTED
            and claim_applies_to_release(c, release)
            for c in claims
        ):
            follow_up.append("Kontroller bestridt posisjon")
        if any(
            c.status == Status.UNVERIFIED
            and claim_applies_to_release(c, release)
            for c in claims
        ):
            follow_up.append("Uverifisert grunnlag")
        if state and state.history_uncertain:
            follow_up.append("Historikk må avklares")
        if state and state.needs_reconciliation:
            follow_up.append("Venter på systemoppdatering")
        if (
            state
            and state.status == "pending"
            and not state.has_plausible_basis
        ):
            follow_up.append("Mangler plausibelt P7-grunnlag")
        if not managed:
            follow_up.append("Vurder onboarding ved konkret P7-grunnlag")
        if not local:
            follow_up.append("Lokal organisasjon må konfigureres")
        unique[pk] = {
            "recording": recording,
            "artist": _artist_text(recording),
            "isrc": next(
                (
                    i.normalized_value
                    for i in recording.identifiers.all()
                    if i.scheme == "ISRC"
                ),
                "",
            ),
            "managed": managed,
            "state": state,
            "management_key": key,
            "management_label": label,
            "ownership": ownership,
            "administration": admin,
            "distribution": distribution,
            "follow_up": follow_up,
            "protecting": list(protecting[pk].values()),
            "placements": placements[pk],
        }
    rows = []
    for track in tracks:
        position = [f"Disc {track.disc_number}"] if track.disc_number else []
        if track.side:
            position.append(f"Side {track.side}")
        position.append(f"Spor {track.track_number or track.sequence_number}")
        rows.append(
            {
                **unique[track.recording_id],
                "track": track,
                "position": " · ".join(position),
            }
        )
    counts = Counter(r["ownership"].category for r in unique.values())
    return {
        "rows": rows,
        "unique_count": len(unique),
        "track_count": len(tracks),
        "local_organization": local,
        "ownership_counts": [
            (kind.label, counts[kind]) for kind in OwnershipCategory
        ],
        "basis_counts": [
            {
                "label": label,
                "confirmed": sum(
                    bool(r[name]["confirmed"]) for r in unique.values()
                ),
                "pending": sum(
                    bool(
                        r[name]["pending"]
                        or r[name]["disputed"]
                        or r[name]["future"]
                    )
                    for r in unique.values()
                ),
            }
            for name, label in (
                ("administration", "Administrasjon"),
                ("distribution", "Distribusjon"),
            )
        ],
    }
