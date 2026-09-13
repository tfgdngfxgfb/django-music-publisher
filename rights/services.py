from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from catalogue.models import Recording
from rights_core.models import VerificationStatus

from .models import ClaimTerritory, RightsClaim, RightsConfiguration, RightsDecision


def _validate_territory_scope(mode, territories):
    territories = tuple(territories)
    if mode == RightsClaim.TerritoryMode.WORLD and territories:
        raise ValidationError("Hele verden kan ikke kombineres med en territorieliste.")
    if mode != RightsClaim.TerritoryMode.WORLD and not territories:
        raise ValidationError("Velg minst ett territorium for dette omfanget.")
    return territories


def _territory_signature(claim):
    return claim.territory_mode, frozenset(
        claim.territories.values_list("id", flat=True)
    )


def validate_confirmed_ownership_total(claim):
    if claim.right_type != RightsClaim.RightType.OWNERSHIP or claim.share is None:
        return
    total = Decimal(claim.share)
    signature = _territory_signature(claim)
    others = RightsClaim.objects.filter(
        recording=claim.recording,
        right_type=RightsClaim.RightType.OWNERSHIP,
        status=VerificationStatus.CONFIRMED,
        valid_from=claim.valid_from,
        valid_until=claim.valid_until,
    ).exclude(pk=claim.pk)
    for other in others.prefetch_related("territories"):
        if (
            other.share is not None
            and _territory_signature(other) == signature
        ):
            total += other.share
    if total > Decimal("100"):
        raise ValidationError(
            "Bekreftede eierandeler overstiger 100 % for samme territorieomfang og periode."
        )


@transaction.atomic
def create_rights_claim(*, territories=(), **values):
    values.pop("status", None)
    selected = _validate_territory_scope(
        values.get("territory_mode", RightsClaim.TerritoryMode.WORLD), territories
    )
    claim = RightsClaim.objects.create(status=VerificationStatus.UNVERIFIED, **values)
    for territory in selected:
        ClaimTerritory.objects.create(claim=claim, territory=territory)
    claim.full_clean()
    return claim


@transaction.atomic
def decide_rights_claim(claim, decision, *, user, note=""):
    if decision not in {
        VerificationStatus.CONFIRMED,
        VerificationStatus.DISPUTED,
        VerificationStatus.REJECTED,
    }:
        raise ValueError("Ugyldig beslutning for rettighetskrav.")
    claim = (
        RightsClaim.objects.select_for_update()
        .prefetch_related("territories")
        .get(pk=claim.pk)
    )
    Recording.objects.select_for_update().get(pk=claim.recording_id)
    if decision == VerificationStatus.CONFIRMED:
        validate_confirmed_ownership_total(claim)
    result = RightsDecision.objects.create(
        claim=claim, decision=decision, decided_by=user, note=note
    )
    claim.status = decision
    claim._allow_status_transition = True
    claim.save(update_fields=("status",))
    return result


@transaction.atomic
def supersede_rights_claim(previous, *, user, territories=(), note="", **values):
    previous = RightsClaim.objects.select_for_update().get(pk=previous.pk)
    values.update(
        recording=previous.recording,
        right_type=previous.right_type,
        supersedes=previous,
    )
    replacement = create_rights_claim(territories=territories, **values)
    RightsDecision.objects.create(
        claim=previous,
        decision=VerificationStatus.SUPERSEDED,
        decided_by=user,
        note=note or f"Erstattet av {replacement.pk}",
    )
    previous.status = VerificationStatus.SUPERSEDED
    previous._allow_status_transition = True
    previous.save(update_fields=("status",))
    return replacement


@transaction.atomic
def link_claim_agreement(claim, agreement, *, user, note=""):
    claim = RightsClaim.objects.select_for_update().get(pk=claim.pk)
    previous_agreement = claim.agreement
    claim.agreement = agreement
    claim._allow_claim_update = True
    claim.save(update_fields=("agreement",))
    history_note = (
        f"Avtale endret fra {previous_agreement or 'ingen avtale'} til {agreement}."
    )
    if note:
        history_note += f" {note}"
    RightsDecision.objects.create(
        claim=claim,
        decision=RightsDecision.Decision.DOCUMENTED,
        decided_by=user,
        note=history_note,
    )
    return claim


def get_local_organization():
    configuration = RightsConfiguration.objects.select_related(
        "local_organization"
    ).first()
    return configuration.local_organization if configuration else None
