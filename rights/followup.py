"""Pure, ephemeral follow-up facts and evaluation. No ORM access or writes.

These observations are neither usage clearance nor a replacement for the
manual workflow membership guard. Callers supply permission-filtered facts.
"""

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256

from managed_music.lifecycle import ManagementState
from rights.positions import (
    claims_applicable_in_release_context,
    legal_position_identity,
)
from rights.scope import (
    RELEVANT_STATUSES,
    claim_applies_to_date,
    claim_countries,
    find_ownership_conflict,
    periods_overlap,
)

CATEGORIES = (
    ("action", "Krever behandling"),
    ("quality", "Datakvalitet"),
    ("upcoming", "Kommende"),
    ("review", "Kataloggjennomgang"),
)
# code -> primary category, visual strength, explanation
RULES = {
    "claim.unverified": (
        "action",
        "warning",
        "Uverifisert rettighetsposisjon",
    ),
    "claim.disputed": ("action", "danger", "Bestridt rettighetsposisjon"),
    "management.history_uncertain": (
        "action",
        "warning",
        "Historikk må avklares",
    ),
    "management.pending_without_basis": (
        "action",
        "warning",
        "Under vurdering uten plausibelt P7-grunnlag",
    ),
    "management.local_basis_without_membership": (
        "quality",
        "danger",
        "Bekreftet P7-grunnlag uten forvaltningsmedlemskap",
    ),
    "ownership.conflict": (
        "quality",
        "danger",
        "Overlappende bekreftede eierandeler overstiger 100 %",
    ),
    "ownership.local_share_unknown": (
        "quality",
        "warning",
        "Ukjent lokal eierandel",
    ),
    "evidence.not_assessed": (
        "quality",
        "warning",
        "Dokumentasjonsstyrke er ikke vurdert",
    ),
    "evidence.weak": (
        "quality",
        "warning",
        "Svakt dokumentert bekreftet grunnlag",
    ),
    "scope.release_mismatch": (
        "quality",
        "warning",
        "Innspillingen finnes ikke lenger på claimets utgivelse",
    ),
    "claim.duplicate_position": (
        "quality",
        "warning",
        "Flere claims beskriver nøyaktig samme juridiske posisjon",
    ),
    "management.needs_reconciliation": (
        "quality",
        "info",
        "Venter på systemoppdatering",
    ),
    "claim.expiring": (
        "upcoming",
        "info",
        "Bekreftet rettighetsperiode utløper snart",
    ),
    "claim.starts_soon": (
        "upcoming",
        "info",
        "Bekreftet rettighetsperiode starter snart",
    ),
    "release.managed_only_recordings": (
        "review",
        "info",
        "Innspillinger beskyttet kun via forvaltet utgivelse",
    ),
    "release.without_current_local_basis": (
        "review",
        "info",
        "Innspillinger uten aktuelt bekreftet P7-grunnlag i utgivelsen",
    ),
}


@dataclass(frozen=True)
class TerritoryFact:
    pk: object
    code: str


@dataclass(frozen=True)
class ClaimFact:
    pk: object
    recording_id: object
    right_type: str
    rights_holder_id: object
    status: str = "unverified"
    grantor_id: object = None
    share: Decimal | None = None
    territory_mode: str = "world"
    territories: tuple = ()
    valid_from: date | None = None
    valid_until: date | None = None
    release_scope_id: object = None
    evidence_strength: str = "not_assessed"
    # Labels are already permission-masked by the query boundary.
    details: tuple = ()

    @property
    def _prefetched_objects_cache(self):
        """Loaded-fact protocol for the existing pure 6C primitives."""
        return {"territories": self.territories}


@dataclass(frozen=True)
class RecordingFacts:
    pk: object
    title: str
    identity: tuple
    claims: tuple
    release_ids: frozenset
    managed_id: object = None
    state: ManagementState | None = None
    show_management: bool = False


@dataclass(frozen=True)
class ReleaseFacts:
    pk: object
    title: str
    status: str
    recording_ids: frozenset
    managed_recording_ids: frozenset
    claims: tuple
    details: tuple = ()


@dataclass(frozen=True)
class FollowUpSignal:
    code: str
    relevant_date: date | None = None

    @property
    def category(self):
        return RULES[self.code][0]

    @property
    def tone(self):
        return RULES[self.code][1]

    @property
    def reason(self):
        return RULES[self.code][2]


@dataclass(frozen=True)
class FollowUpItem:
    key: str
    target_type: str
    target_id: object
    title: str
    signals: tuple
    recording_id: object = None
    release_id: object = None
    claim_ids: tuple = ()
    claims: tuple = ()
    details: tuple = ()

    @property
    def primary(self):
        ranks = {c: i for i, (c, _) in enumerate(CATEGORIES)}
        return min(
            self.signals,
            key=lambda s: (ranks[s.category], list(RULES).index(s.code)),
        )

    @property
    def category(self):
        return self.primary.category

    @property
    def relevant_date(self):
        return min(
            (s.relevant_date for s in self.signals if s.relevant_date),
            default=None,
        )

    @property
    def codes(self):
        return tuple(s.code for s in self.signals)


def _live(claim, day):
    return (
        claim.status in RELEVANT_STATUSES
        and periods_overlap(claim.valid_from, claim.valid_until, day, None)
        and bool(claim_countries(claim))
    )


def _digest(identity):
    return sha256(repr(identity).encode()).hexdigest()[:24]


def evaluate_recording(facts, *, local_id, on_date, horizon=90):
    """Only current/future issues; all-status history is supplied through 6D state."""
    if local_id is None:
        return ()
    end = on_date + timedelta(days=horizon)
    live = tuple(c for c in facts.claims if _live(c, on_date))
    items = []
    for claim in live:
        signals = []
        local = claim.rights_holder_id == local_id
        if local:
            if claim.status in ("unverified", "disputed"):
                signals.append(FollowUpSignal("claim." + claim.status))
            if claim.right_type == "master_ownership" and claim.share is None:
                signals.append(FollowUpSignal("ownership.local_share_unknown"))
            if claim.status == "confirmed":
                if claim.evidence_strength in ("not_assessed", "weak"):
                    signals.append(
                        FollowUpSignal("evidence." + claim.evidence_strength)
                    )
                if claim.valid_from and on_date < claim.valid_from <= end:
                    signals.append(
                        FollowUpSignal("claim.starts_soon", claim.valid_from)
                    )
                if (
                    claim_applies_to_date(claim, on_date)
                    and claim.valid_until
                    and on_date <= claim.valid_until <= end
                ):
                    signals.append(
                        FollowUpSignal("claim.expiring", claim.valid_until)
                    )
        if (
            claim.release_scope_id
            and claim.release_scope_id not in facts.release_ids
        ):
            signals.append(FollowUpSignal("scope.release_mismatch"))
        if signals:
            items.append(
                FollowUpItem(
                    f"claim:{claim.pk}",
                    "claim",
                    claim.pk,
                    facts.title,
                    tuple(signals),
                    recording_id=facts.pk,
                    claim_ids=(claim.pk,),
                    claims=(claim,),
                    details=facts.identity,
                )
            )

    groups = defaultdict(list)
    for claim in live:
        groups[legal_position_identity(claim)].append(claim)
    for identity, claims in groups.items():
        if len(claims) > 1:
            items.append(
                FollowUpItem(
                    f"duplicate:{facts.pk}:{_digest(identity)}",
                    "recording",
                    facts.pk,
                    facts.title,
                    (FollowUpSignal("claim.duplicate_position"),),
                    recording_id=facts.pk,
                    claim_ids=tuple(c.pk for c in claims),
                    claims=tuple(claims),
                    details=facts.identity,
                )
            )
    # Clip the evaluation window, not canonical claims: past-only conflicts
    # must not obscure a later real conflict or create permanent historical noise.
    confirmed = tuple(
        replace(c, valid_from=max(on_date, c.valid_from or on_date))
        for c in live
        if c.status == "confirmed"
    )
    conflict = find_ownership_conflict(confirmed)
    if conflict:
        ids = tuple(sorted((c.pk for c in conflict.claims), key=str))
        items.append(
            FollowUpItem(
                f"ownership-conflict:{facts.pk}:{_digest(ids)}",
                "recording",
                facts.pk,
                facts.title,
                (FollowUpSignal("ownership.conflict", conflict.on_date),),
                recording_id=facts.pk,
                claim_ids=ids,
                claims=tuple(c for c in live if c.pk in ids),
                details=facts.identity
                + (
                    (
                        "Overlapp",
                        f"{conflict.territory} · {conflict.on_date} · {conflict.total} %",
                    ),
                ),
            )
        )

    if facts.show_management:
        signals = []
        state = facts.state
        details = facts.identity
        if state:
            if state.history_uncertain:
                signals.append(FollowUpSignal("management.history_uncertain"))
            if (
                state.status == "pending"
                and not state.has_plausible_basis
                and not state.has_confirmed_history
            ):
                signals.append(
                    FollowUpSignal("management.pending_without_basis")
                )
            if state.needs_reconciliation:
                signals.append(
                    FollowUpSignal("management.needs_reconciliation")
                )
            labels = {
                "active": "Aktiv",
                "pending": "Under vurdering",
                "inactive": "Historisk/inaktiv",
                None: "Historikk må avklares",
            }
            details += (
                ("Beregnet status", labels[state.status]),
                ("Lagret status", labels[state.stored_status]),
            )
            details += tuple(
                (label, "Ja" if value else "Nei")
                for label, value in (
                    ("Aktuelt bekreftet grunnlag", state.has_current_basis),
                    (
                        "Bekreftet historisk forvaltning",
                        state.has_confirmed_history,
                    ),
                    ("Uverifisert grunnlag", state.has_unverified_basis),
                    ("Bestridt grunnlag", state.has_disputed_basis),
                    (
                        "Framtidig bekreftet grunnlag",
                        state.has_future_confirmed_basis,
                    ),
                )
            )
        elif not facts.managed_id and any(
            c.status == "confirmed" and c.rights_holder_id == local_id
            for c in live
        ):
            signals.append(
                FollowUpSignal("management.local_basis_without_membership")
            )
        if signals:
            related = tuple(
                c
                for c in facts.claims
                if c.rights_holder_id == local_id
                and (
                    (
                        not facts.managed_id
                        and c in live
                        and c.status == "confirmed"
                    )
                    or (
                        state
                        and state.history_uncertain
                        and c.status in ("unverified", "disputed")
                    )
                )
            )
            items.append(
                FollowUpItem(
                    f"management:{facts.managed_id or facts.pk}",
                    "management",
                    facts.managed_id or facts.pk,
                    facts.title,
                    tuple(signals),
                    recording_id=facts.pk,
                    details=details,
                    claims=related,
                    claim_ids=tuple(c.pk for c in related),
                )
            )
    return tuple(items)


def evaluate_release(facts, *, local_id, on_date):
    if local_id is None or facts.status not in ("active", "pending"):
        return ()
    current = claims_applicable_in_release_context(
        facts.claims, facts.pk, local_id
    )
    covered = {
        c.recording_id
        for c in current
        if c.status == "confirmed" and claim_applies_to_date(c, on_date)
    }
    only = facts.recording_ids - facts.managed_recording_ids
    missing = facts.recording_ids - covered
    signals = []
    if only:
        signals.append(FollowUpSignal("release.managed_only_recordings"))
    if missing:
        signals.append(FollowUpSignal("release.without_current_local_basis"))
    if not signals:
        return ()
    return (
        FollowUpItem(
            f"release-review:{facts.pk}",
            "release",
            facts.pk,
            facts.title,
            tuple(signals),
            release_id=facts.pk,
            details=facts.details
            + (
                ("Unike innspillinger", str(len(facts.recording_ids))),
                ("Kun via forvaltet utgivelse", str(len(only))),
                ("Uten aktuelt P7-grunnlag her", str(len(missing))),
            ),
        ),
    )


def item_sort_key(item):
    codes = set(item.codes)
    rank = (
        0
        if codes
        & {"ownership.conflict", "management.local_basis_without_membership"}
        else (
            1
            if item.category == "action"
            else (
                2
                if item.category == "quality" and item.primary.tone != "info"
                else 3 if item.category == "upcoming" else 4
            )
        )
    )
    return (
        rank,
        item.relevant_date or date.max,
        item.title.casefold(),
        item.key,
    )
