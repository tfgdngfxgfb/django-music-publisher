"""Conservative all-territory overviews built on the common scope evaluator."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.utils import timezone

from rights_core.models import VerificationStatus
from .models import RightsClaim
from .scope import (
    COUNTRY_CODES,
    OwnershipCategory,
    ScopeContext,
    claim_applies_to_date,
    claim_countries,
    claims_for_recordings,
    evaluate_management_basis,
    evaluate_ownership,
    evaluate_right,
    prepare_claims,
    RELEVANT_STATUSES,
)


@dataclass(frozen=True)
class OwnershipSummary:
    category: str
    claims: tuple
    local_claims: tuple
    other_claims: tuple
    local_organization: object | None

    @property
    def label(self):
        return OwnershipCategory(self.category).label

    @property
    def status_class(self):
        return {
            OwnershipCategory.FULL: "confirmed",
            OwnershipCategory.PARTIAL: "partial",
            OwnershipCategory.NOT_OWNED: "not-owned",
            OwnershipCategory.UNRESOLVED: "unverified",
            OwnershipCategory.DISPUTED: "disputed",
        }[OwnershipCategory(self.category)]

    @property
    def local_share(self):
        if not self.local_claims or self.has_unknown_local_share:
            return None
        # Never add territorially disjoint interests into a fictitious global sum.
        totals = {
            sum(
                (
                    c.share
                    for c in self.local_claims
                    if code in claim_countries(c)
                ),
                Decimal("0"),
            )
            for code in COUNTRY_CODES
        }
        return totals.pop() if len(totals) == 1 else None

    @property
    def has_unknown_local_share(self):
        return any(claim.share is None for claim in self.local_claims)


def classify_ownership(claims, local_organization, *, on_date=None):
    """Global overview, never a substitute for a concrete territorial decision.

    FULL/NOT_OWNED require the same positive result in every supported country.
    Known local ownership in only part of the world is PARTIAL. Unknown confirmed
    shares remain UNRESOLVED; a conflict in any country remains DISPUTED.
    """
    day = on_date or timezone.localdate()
    active = tuple(
        c
        for c in prepare_claims(claims)
        if c.right_type == RightsClaim.RightType.OWNERSHIP
        and c.release_scope_id is None
        and c.status in RELEVANT_STATUSES
        and claim_applies_to_date(c, day)
        and claim_countries(c)
    )
    local_id = getattr(local_organization, "pk", None)
    confirmed = tuple(
        c for c in active if c.status == VerificationStatus.CONFIRMED
    )
    local = tuple(
        c
        for c in confirmed
        if local_id is not None and c.rights_holder_id == local_id
    )
    other = tuple(c for c in confirmed if c.rights_holder_id != local_id)
    results = []
    # Countries with the same applicable claims have the same result.
    seen = set()
    countries = {c.pk: claim_countries(c) for c in active}
    for code in sorted(COUNTRY_CODES):
        signature = tuple(c.pk for c in active if code in countries[c.pk])
        if signature in seen:
            continue
        seen.add(signature)
        context = ScopeContext(
            active[0].recording_id if active else None,
            RightsClaim.RightType.OWNERSHIP,
            code,
            day,
        )
        results.append(
            evaluate_ownership(
                evaluate_right(active, context, local_organization=local_id)
            )
        )
    categories = {r.category for r in results}
    if OwnershipCategory.DISPUTED in categories:
        category = OwnershipCategory.DISPUTED
    elif categories == {OwnershipCategory.FULL}:
        category = OwnershipCategory.FULL
    elif categories == {OwnershipCategory.NOT_OWNED}:
        category = OwnershipCategory.NOT_OWNED
    elif any(c.share is None for c in confirmed):
        category = OwnershipCategory.UNRESOLVED
    elif any(r.known_local_share > 0 for r in results):
        category = OwnershipCategory.PARTIAL
    else:
        category = OwnershipCategory.UNRESOLVED
    return OwnershipSummary(category, active, local, other, local_organization)


def ownership_summaries_for_recordings(recording_ids, local_organization):
    ids = tuple(recording_ids)
    grouped = defaultdict(list)
    for claim in claims_for_recordings(ids).filter(
        right_type=RightsClaim.RightType.OWNERSHIP
    ):
        grouped[claim.recording_id].append(claim)
    return {
        pk: classify_ownership(grouped[pk], local_organization) for pk in ids
    }


def has_local_confirmed_right(
    claims, local_organization, right_type, *, on_date=None
):
    """Legacy overview flag: general claim in some country, not use permission.

    Release-only interests use resolve_management_basis or resolve_right with
    explicit release instead of implying general distribution/administration.
    """
    return any(
        c.right_type == right_type
        and c.release_scope_id is None
        and c.status == VerificationStatus.CONFIRMED
        for c in evaluate_management_basis(
            prepare_claims(claims),
            local_organization=local_organization,
            on_date=on_date or timezone.localdate(),
        )
    )


def local_confirmed_right_recording_ids(
    recording_ids, local_organization, right_type, *, on_date=None
):
    if local_organization is None:
        return set()
    claims = claims_for_recordings(tuple(recording_ids)).filter(
        right_type=right_type,
        status=VerificationStatus.CONFIRMED,
        release_scope__isnull=True,
        rights_holder=local_organization,
    )
    return {
        c.recording_id
        for c in evaluate_management_basis(
            claims,
            local_organization=local_organization,
            on_date=on_date or timezone.localdate(),
        )
    }
