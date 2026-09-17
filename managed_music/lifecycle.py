"""Bounded management lifecycle; not a territorial rights resolver (phase 6C)."""

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from catalogue.models import Recording
from rights.models import RightsClaim, RightsConfiguration, RightsDecision
from rights_core.models import VerificationStatus

from .models import ManagedRecording


@dataclass(frozen=True)
class ManagementState:
    status: str | None
    has_current_basis: bool
    has_confirmed_history: bool
    has_pending_basis: bool
    history_uncertain: bool
    requires_follow_up: bool


def _confirmed_history(claim, on_date):
    """Retain confirmation evidence, including retrospective confirmations.

    A future-dated claim withdrawn before its start never establishes past
    management. Later rejection does not erase an already effective confirmation.
    Documentation events do not open or close confirmation periods.
    """
    starts = claim.valid_from
    confirmed = False
    for decision in claim.decisions.all():
        decision_date = timezone.localtime(decision.created_at).date()
        if decision_date > on_date:
            break
        if decision.decision == VerificationStatus.CONFIRMED:
            confirmed = True
            if starts is None or starts <= decision_date:
                return True
        elif decision.decision in {
            VerificationStatus.DISPUTED,
            VerificationStatus.REJECTED,
            VerificationStatus.SUPERSEDED,
        }:
            if confirmed and (starts is None or starts <= decision_date):
                return True
            confirmed = False
    # Also supports existing confirmed records without a decision entry.
    return (confirmed or claim.status == VerificationStatus.CONFIRMED) and (
        starts is None or starts <= on_date
    )


def management_state(managed, *, on_date=None):
    """Read current/historical basis without changing membership or claims.

    All three existing local master right types qualify in 6A. Territory-specific
    use permission is deliberately not inferred from this catalogue lifecycle.
    """
    on_date = on_date or timezone.localdate()
    local_id = RightsConfiguration.objects.values_list(
        "local_organization_id", flat=True
    ).first()
    if local_id is None:
        raise ValidationError("Lokal organisasjon må være konfigurert.")
    claims = list(
        RightsClaim.objects.filter(
            recording_id=managed.library_entry.recording_id,
            rights_holder_id=local_id,
            right_type__in=RightsClaim.RightType.values,
        ).prefetch_related(
            Prefetch(
                "decisions",
                queryset=RightsDecision.objects.order_by("created_at", "id"),
            )
        )
    )
    current = any(
        claim.status == VerificationStatus.CONFIRMED
        and (claim.valid_from is None or claim.valid_from <= on_date)
        and (claim.valid_until is None or claim.valid_until >= on_date)
        for claim in claims
    )
    historical = any(_confirmed_history(claim, on_date) for claim in claims)
    pending = any(
        claim.status
        in {VerificationStatus.UNVERIFIED, VerificationStatus.DISPUTED}
        or (
            claim.status == VerificationStatus.CONFIRMED
            and claim.valid_from is not None
            and claim.valid_from > on_date
        )
        for claim in claims
    )
    # Legacy/manual ACTIVE or INACTIVE is not evidence of never having managed.
    # Retain it conservatively, and require investigation before any return.
    uncertain = not historical and managed.status in {
        ManagedRecording.Status.ACTIVE,
        ManagedRecording.Status.INACTIVE,
    }
    status = (
        ManagedRecording.Status.ACTIVE
        if current
        else (
            ManagedRecording.Status.INACTIVE
            if historical
            else ManagedRecording.Status.PENDING
        )
    )
    if uncertain:
        status = None  # No safe reclassification of undocumented legacy state.
    return ManagementState(
        status=status,
        has_current_basis=current,
        has_confirmed_history=historical,
        has_pending_basis=pending,
        history_uncertain=uncertain,
        requires_follow_up=uncertain
        or (status == ManagedRecording.Status.PENDING and not pending),
    )


@transaction.atomic
def refresh_management_status(recording, *, on_date=None):
    """Reconcile existing membership only; never create or delete membership."""
    Recording.objects.select_for_update().get(pk=recording.pk)
    managed = (
        ManagedRecording.objects.select_for_update()
        .filter(library_entry__recording_id=recording.pk)
        .first()
    )
    if managed is None or not RightsConfiguration.objects.exists():
        return None
    state = management_state(managed, on_date=on_date)
    if state.status is not None and managed.status != state.status:
        managed.status = state.status
        managed.save(update_fields=("status",))
    return state
