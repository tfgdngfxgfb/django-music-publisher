"""Read-only master-rights scope. No membership, authority or use permission.

Evaluation works on loaded claims. Query wrappers prefetch territories once;
pure claim evaluation rejects unloaded territorial lists instead of doing N+1.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import prefetch_related_objects
from django.utils import timezone
from music_metadata.territories.territory import Territory as IndustryTerritory

from rights.models import RightsClaim, RightsConfiguration
from rights_core.models import VerificationStatus

COUNTRY_CODES = frozenset(
    code
    for code, country in IndustryTerritory.all_tis_a.items()
    if len(code) == 2 and code.isalpha() and country.is_country
)
RELEVANT_STATUSES = frozenset(
    (
        VerificationStatus.CONFIRMED,
        VerificationStatus.DISPUTED,
        VerificationStatus.UNVERIFIED,
    )
)
_DEFAULT_ORGANIZATION = object()


class OwnershipCategory(models.TextChoices):
    FULL = "full", "Heleid av lokal organisasjon"
    PARTIAL = "partial", "Deleid av lokal organisasjon"
    NOT_OWNED = "not_owned", "Ikke eid av lokal organisasjon"
    UNRESOLVED = "unresolved", "Eierskap uavklart"
    DISPUTED = "disputed", "Eierskap bestridt"


def normalize_territory(territory):
    code = str(getattr(territory, "code", territory) or "").strip().upper()
    if code not in COUNTRY_CODES:
        raise ValidationError("Angi en gyldig ISO alfa-2-landkode.")
    return code


def period_contains_date(valid_from, valid_until, on_date):
    return (valid_from is None or valid_from <= on_date) and (
        valid_until is None or on_date <= valid_until
    )


def periods_overlap(start_a, end_a, start_b, end_b):
    return (start_a is None or end_b is None or start_a <= end_b) and (
        start_b is None or end_a is None or start_b <= end_a
    )


def territory_scope_countries(mode, territories=()):
    codes = frozenset(normalize_territory(value) for value in territories)
    if mode == RightsClaim.TerritoryMode.WORLD:
        return COUNTRY_CODES
    if mode == RightsClaim.TerritoryMode.INCLUDE:
        return codes
    if mode == RightsClaim.TerritoryMode.EXCLUDE:
        return COUNTRY_CODES - codes
    raise ValidationError("Ugyldig territorieomfang.")


def territory_scope_contains(mode, territories, territory):
    return normalize_territory(territory) in territory_scope_countries(
        mode, territories
    )


def territory_scopes_overlap(mode_a, territories_a, mode_b, territories_b):
    return bool(
        territory_scope_countries(mode_a, territories_a)
        & territory_scope_countries(mode_b, territories_b)
    )


def _identity(value):
    return getattr(value, "pk", value)


def release_scopes_overlap(release_a, release_b):
    a, b = _identity(release_a), _identity(release_b)
    return a is None or b is None or a == b


def claim_applies_to_release(claim, release):
    return (
        claim.release_scope_id is None
        or claim.release_scope_id == _identity(release)
    )


def claim_applies_to_date(claim, on_date):
    return period_contains_date(claim.valid_from, claim.valid_until, on_date)


def claim_countries(claim):
    if claim.territory_mode == RightsClaim.TerritoryMode.WORLD:
        return COUNTRY_CODES
    cached = getattr(claim, "_prefetched_objects_cache", {})
    if "territories" not in cached:
        raise ValueError("Prefetch territories before pure scope evaluation.")
    return territory_scope_countries(
        claim.territory_mode, cached["territories"]
    )


def claim_applies_to_territory(claim, territory):
    return normalize_territory(territory) in claim_countries(claim)


def prepare_claims(claims):
    claims = tuple(claims)
    prefetch_related_objects(claims, "territories")
    return claims


def claims_for_recordings(recording_ids):
    return (
        RightsClaim.objects.filter(recording_id__in=recording_ids)
        .select_related("rights_holder")
        .prefetch_related("territories")
        .order_by("recording_id", "created_at", "id")
    )


def _local_id(organization):
    if organization is _DEFAULT_ORGANIZATION:
        return RightsConfiguration.objects.values_list(
            "local_organization_id", flat=True
        ).first()
    return _identity(organization)


@dataclass(frozen=True)
class ScopeContext:
    recording_id: object
    right_type: str
    territory: str
    on_date: date
    release_id: object | None = None

    def __post_init__(self):
        object.__setattr__(
            self, "territory", normalize_territory(self.territory)
        )
        if self.right_type not in RightsClaim.RightType.values:
            raise ValidationError("Ugyldig rettighetstype.")


def claim_applies(claim, context):
    return (
        claim.recording_id == context.recording_id
        and claim.right_type == context.right_type
        and claim.status in RELEVANT_STATUSES
        and claim_applies_to_date(claim, context.on_date)
        and claim_applies_to_release(claim, context.release_id)
        and claim_applies_to_territory(claim, context.territory)
    )


@dataclass(frozen=True)
class ClaimResolution:
    claims: tuple
    local_organization_id: object | None

    @property
    def confirmed_claims(self):
        return tuple(
            c for c in self.claims if c.status == VerificationStatus.CONFIRMED
        )

    @property
    def disputed_claims(self):
        return tuple(
            c for c in self.claims if c.status == VerificationStatus.DISPUTED
        )

    @property
    def unverified_claims(self):
        return tuple(
            c for c in self.claims if c.status == VerificationStatus.UNVERIFIED
        )

    def _local(self, claims):
        return tuple(
            c
            for c in claims
            if self.local_organization_id is not None
            and c.rights_holder_id == self.local_organization_id
        )

    @property
    def local_confirmed_claims(self):
        return self._local(self.confirmed_claims)

    @property
    def local_disputed_claims(self):
        return self._local(self.disputed_claims)

    @property
    def local_unverified_claims(self):
        return self._local(self.unverified_claims)

    @property
    def other_confirmed_claims(self):
        return tuple(
            c
            for c in self.confirmed_claims
            if c.rights_holder_id != self.local_organization_id
        )

    @property
    def has_local_confirmed(self):
        return bool(self.local_confirmed_claims)

    @property
    def has_local_pending(self):
        return bool(self.local_disputed_claims or self.local_unverified_claims)

    @property
    def has_dispute(self):
        return bool(self.disputed_claims)


@dataclass(frozen=True)
class RightResolution(ClaimResolution):
    context: ScopeContext


def evaluate_right(claims, context, *, local_organization=None):
    return RightResolution(
        tuple(c for c in claims if claim_applies(c, context)),
        _identity(local_organization),
        context,
    )


def resolve_right(
    recording,
    right_type,
    *,
    territory,
    on_date=None,
    release=None,
    claims=None,
    local_organization=_DEFAULT_ORGANIZATION
):
    context = ScopeContext(
        _identity(recording),
        right_type,
        territory,
        on_date or timezone.localdate(),
        _identity(release),
    )
    loaded = (
        prepare_claims(claims)
        if claims is not None
        else claims_for_recordings([context.recording_id])
    )
    return evaluate_right(
        loaded, context, local_organization=_local_id(local_organization)
    )


def resolve_rights_for_recordings(
    recording_ids,
    right_type,
    *,
    territory,
    on_date=None,
    release=None,
    local_organization=_DEFAULT_ORGANIZATION
):
    """Two data queries for any batch size; optional one configuration query."""
    ids = tuple(recording_ids)
    code = normalize_territory(territory)
    day = on_date or timezone.localdate()
    local_id = _local_id(local_organization)
    grouped = defaultdict(list)
    for claim in claims_for_recordings(ids):
        grouped[claim.recording_id].append(claim)
    return {
        pk: evaluate_right(
            grouped[pk],
            ScopeContext(pk, right_type, code, day, _identity(release)),
            local_organization=local_id,
        )
        for pk in ids
    }


@dataclass(frozen=True)
class ManagementBasis(ClaimResolution):
    recording_id: object
    on_date: date


def evaluate_management_basis(claims, *, local_organization, on_date):
    """Any nonempty territory AND any release scope, explicitly not a use right."""
    local_id = _identity(local_organization)
    return tuple(
        c
        for c in claims
        if local_id is not None
        and c.rights_holder_id == local_id
        and c.right_type in RightsClaim.RightType.values
        and c.status in RELEVANT_STATUSES
        and claim_applies_to_date(c, on_date)
        and claim_countries(c)
    )


def resolve_management_basis(
    recording,
    *,
    on_date=None,
    claims=None,
    local_organization=_DEFAULT_ORGANIZATION
):
    pk = _identity(recording)
    day = on_date or timezone.localdate()
    local_id = _local_id(local_organization)
    loaded = (
        prepare_claims(claims)
        if claims is not None
        else claims_for_recordings([pk])
    )
    applicable = evaluate_management_basis(
        (c for c in loaded if c.recording_id == pk),
        local_organization=local_id,
        on_date=day,
    )
    return ManagementBasis(applicable, local_id, pk, day)


@dataclass(frozen=True)
class OwnershipResolution:
    rights: RightResolution
    category: str
    known_local_share: Decimal
    known_other_share: Decimal
    unknown_share_claims: tuple

    @property
    def local_share(self):
        if not self.rights.local_confirmed_claims or any(
            c.share is None for c in self.rights.local_confirmed_claims
        ):
            return None
        return self.known_local_share


def evaluate_ownership(rights):
    if rights.context.right_type != RightsClaim.RightType.OWNERSHIP:
        raise ValueError("Ownership requires master_ownership resolution.")
    local = sum(
        (
            c.share
            for c in rights.local_confirmed_claims
            if c.share is not None
        ),
        Decimal("0"),
    )
    other = sum(
        (
            c.share
            for c in rights.other_confirmed_claims
            if c.share is not None
        ),
        Decimal("0"),
    )
    unknown = tuple(c for c in rights.confirmed_claims if c.share is None)
    if (
        rights.has_dispute
        or local + other > 100
        or (local + other == 100 and unknown)
    ):
        category = OwnershipCategory.DISPUTED
    elif rights.local_organization_id is None or unknown:
        category = OwnershipCategory.UNRESOLVED
    elif local == 100:
        category = OwnershipCategory.FULL
    elif local > 0:
        category = OwnershipCategory.PARTIAL
    elif other == 100 and not rights.has_local_pending:
        category = OwnershipCategory.NOT_OWNED
    else:
        category = OwnershipCategory.UNRESOLVED
    return OwnershipResolution(rights, category, local, other, unknown)


def resolve_ownership(
    recording,
    *,
    territory,
    on_date=None,
    release=None,
    claims=None,
    local_organization=_DEFAULT_ORGANIZATION
):
    return evaluate_ownership(
        resolve_right(
            recording,
            RightsClaim.RightType.OWNERSHIP,
            territory=territory,
            on_date=on_date,
            release=release,
            claims=claims,
            local_organization=local_organization,
        )
    )


@dataclass(frozen=True)
class OwnershipConflict:
    territory: str
    on_date: date
    total: Decimal
    claims: tuple


def find_ownership_conflict(claims):
    """Exact multi-claim overlap. Callers supply confirmed/proposed positions.

    A sum can first increase only on a start date (including unbounded start).
    Enumerating these dates and the existing validated country universe also
    handles EXCLUDE exactly and avoids false sums of pairwise-only overlaps.
    """
    claims = tuple(
        c
        for c in claims
        if c.right_type == RightsClaim.RightType.OWNERSHIP
        and c.share is not None
    )
    scopes = {c.pk: claim_countries(c) for c in claims}
    for day in sorted({c.valid_from or date.min for c in claims}):
        active = tuple(c for c in claims if claim_applies_to_date(c, day))
        for code in sorted(COUNTRY_CODES):
            overlapping = tuple(c for c in active if code in scopes[c.pk])
            total = sum((c.share for c in overlapping), Decimal("0"))
            if total > 100:
                return OwnershipConflict(code, day, total, overlapping)
    return None
