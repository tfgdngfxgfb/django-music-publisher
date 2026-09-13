"""Conservative, derived summaries for current master-rights claims."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import models

from rights_core.models import VerificationStatus

from .models import RightsClaim


class OwnershipCategory(models.TextChoices):
    FULL = "full", "Heleid av lokal organisasjon"
    PARTIAL = "partial", "Deleid av lokal organisasjon"
    NOT_OWNED = "not_owned", "Ikke eid av lokal organisasjon"
    UNRESOLVED = "unresolved", "Eierskap uavklart"
    DISPUTED = "disputed", "Eierskap bestridt"


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
        if not self.local_claims or any(
            claim.share is None for claim in self.local_claims
        ):
            return None
        return sum((claim.share for claim in self.local_claims), Decimal("0"))

    @property
    def has_unknown_local_share(self):
        return bool(self.local_claims) and any(
            claim.share is None for claim in self.local_claims
        )


def _is_current(claim, on_date):
    return (claim.valid_from is None or claim.valid_from <= on_date) and (
        claim.valid_until is None or claim.valid_until >= on_date
    )


def classify_ownership(claims, local_organization, *, on_date=None):
    """Classify current ownership without inventing missing rights information.

    The overview deliberately only concludes full or absent ownership for
    worldwide confirmed claims. Territory-specific combinations remain partial
    or unresolved until a later scope-aware rights engine exists.
    """

    on_date = on_date or date.today()
    active = tuple(
        claim
        for claim in claims
        if claim.right_type == RightsClaim.RightType.OWNERSHIP
        and claim.status
        not in (VerificationStatus.REJECTED, VerificationStatus.SUPERSEDED)
        and _is_current(claim, on_date)
    )
    local_id = getattr(local_organization, "pk", None)
    if any(claim.status == VerificationStatus.DISPUTED for claim in active):
        return OwnershipSummary(
            OwnershipCategory.DISPUTED, active, (), (), local_organization
        )

    confirmed = tuple(
        claim for claim in active if claim.status == VerificationStatus.CONFIRMED
    )
    if not confirmed or local_id is None:
        return OwnershipSummary(
            OwnershipCategory.UNRESOLVED, active, (), confirmed, local_organization
        )

    local_claims = tuple(
        claim for claim in confirmed if claim.rights_holder_id == local_id
    )
    other_claims = tuple(
        claim for claim in confirmed if claim.rights_holder_id != local_id
    )

    # A worldwide 100 % claim and a simultaneous confirmed claim for another
    # holder is a clear conflict even without a full territory-overlap engine.
    local_world_total = sum(
        (
            claim.share
            for claim in local_claims
            if claim.territory_mode == RightsClaim.TerritoryMode.WORLD
            and claim.share is not None
        ),
        Decimal("0"),
    )
    other_world_total = sum(
        (
            claim.share
            for claim in other_claims
            if claim.territory_mode == RightsClaim.TerritoryMode.WORLD
            and claim.share is not None
        ),
        Decimal("0"),
    )
    if (
        local_world_total + other_world_total > Decimal("100")
        or (local_world_total >= 100 and other_claims)
        or (other_world_total >= 100 and local_claims)
    ):
        return OwnershipSummary(
            OwnershipCategory.DISPUTED,
            active,
            local_claims,
            other_claims,
            local_organization,
        )

    if local_claims:
        all_local_world = all(
            claim.territory_mode == RightsClaim.TerritoryMode.WORLD
            for claim in local_claims
        )
        local_known = all(claim.share is not None for claim in local_claims)
        if (
            all_local_world
            and local_known
            and not other_claims
            and sum((claim.share for claim in local_claims), Decimal("0"))
            == Decimal("100")
        ):
            category = OwnershipCategory.FULL
        else:
            category = OwnershipCategory.PARTIAL
        return OwnershipSummary(
            category, active, local_claims, other_claims, local_organization
        )

    other_world = tuple(
        claim
        for claim in other_claims
        if claim.territory_mode == RightsClaim.TerritoryMode.WORLD
    )
    if (
        other_world
        and len(other_world) == len(other_claims)
        and all(claim.share is not None for claim in other_world)
        and sum((claim.share for claim in other_world), Decimal("0"))
        == Decimal("100")
    ):
        category = OwnershipCategory.NOT_OWNED
    else:
        category = OwnershipCategory.UNRESOLVED
    return OwnershipSummary(
        category, active, local_claims, other_claims, local_organization
    )


def ownership_summaries_for_recordings(recording_ids, local_organization):
    """Return summaries using a bounded set of claims and no per-row queries."""

    recording_ids = tuple(recording_ids)
    grouped = defaultdict(list)
    if recording_ids:
        claims = (
            RightsClaim.objects.filter(
                recording_id__in=recording_ids,
                right_type=RightsClaim.RightType.OWNERSHIP,
            )
            .select_related("rights_holder")
            .prefetch_related("territories")
        )
        for claim in claims:
            grouped[claim.recording_id].append(claim)
    return {
        recording_id: classify_ownership(
            grouped[recording_id], local_organization
        )
        for recording_id in recording_ids
    }


def has_local_confirmed_right(claims, local_organization, right_type):
    local_id = getattr(local_organization, "pk", None)
    today = date.today()
    return bool(local_id) and any(
        claim.right_type == right_type
        and claim.rights_holder_id == local_id
        and claim.status == VerificationStatus.CONFIRMED
        and _is_current(claim, today)
        for claim in claims
    )


def local_confirmed_right_recording_ids(
    recording_ids, local_organization, right_type, *, on_date=None
):
    local_id = getattr(local_organization, "pk", None)
    if not local_id:
        return set()
    on_date = on_date or date.today()
    return set(
        RightsClaim.objects.filter(
            recording_id__in=recording_ids,
            right_type=right_type,
            rights_holder_id=local_id,
            status=VerificationStatus.CONFIRMED,
        )
        .filter(
            models.Q(valid_from__isnull=True) | models.Q(valid_from__lte=on_date),
            models.Q(valid_until__isnull=True) | models.Q(valid_until__gte=on_date),
        )
        .values_list("recording_id", flat=True)
    )
