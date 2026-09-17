"""Scope-aware lifecycle of existing membership, never concrete use clearance."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch, prefetch_related_objects
from django.utils import timezone

from catalogue.models import Recording
from rights.models import RightsClaim, RightsConfiguration, RightsDecision
from rights.scope import (
    claim_countries,
    claims_for_recordings,
    evaluate_management_basis,
    periods_overlap,
)
from rights_core.models import VerificationStatus

from .models import ManagedRecording


@dataclass(frozen=True)
class ManagementState:
    status: str | None
    stored_status: str
    has_current_basis: bool
    has_confirmed_history: bool
    has_unverified_basis: bool
    has_disputed_basis: bool
    has_future_confirmed_basis: bool
    history_uncertain: bool
    requires_follow_up: bool

    @property
    def has_pending_basis(self):
        """6A compatibility: unresolved or future local basis, of any period."""
        return (
            self.has_unverified_basis
            or self.has_disputed_basis
            or self.has_future_confirmed_basis
        )

    @property
    def has_plausible_basis(self):
        return self.has_current_basis or self.has_pending_basis

    @property
    def needs_reconciliation(self):
        return self.status is not None and self.status != self.stored_status

    @property
    def can_return_to_music_library(self):
        return (
            self.stored_status == ManagedRecording.Status.PENDING
            and self.status == ManagedRecording.Status.PENDING
            and not self.has_current_basis
            and not self.has_confirmed_history
            and not self.has_plausible_basis
            and not self.history_uncertain
        )


def _has_started_period(claim, on_date):
    return periods_overlap(claim.valid_from, claim.valid_until, None, on_date)


def _confirmed_history(claim, on_date):
    """Retain confirmation evidence, including retrospective confirmations.

    A future-dated claim withdrawn before its start never establishes past
    management. Later rejection does not erase an already effective confirmation.
    Documentation events do not open or close confirmation periods.
    """
    decisions = getattr(claim, "_prefetched_objects_cache", {}).get(
        "decisions"
    )
    if decisions is None:
        raise ValueError(
            "Prefetch decisions before pure lifecycle evaluation."
        )
    confirmed = False
    for decision in sorted(decisions, key=lambda d: (d.created_at, d.pk)):
        decision_date = timezone.localtime(decision.created_at).date()
        if decision_date > on_date:
            break
        if decision.decision == VerificationStatus.CONFIRMED:
            confirmed = True
            if _has_started_period(claim, decision_date):
                return True
        elif decision.decision in {
            VerificationStatus.DISPUTED,
            VerificationStatus.REJECTED,
            VerificationStatus.SUPERSEDED,
        }:
            if confirmed and _has_started_period(claim, decision_date):
                return True
            confirmed = False
    # Also supports existing confirmed records without a decision entry.
    return (
        confirmed or claim.status == VerificationStatus.CONFIRMED
    ) and _has_started_period(claim, on_date)


def evaluate_management_state(managed, claims, *, local_organization, on_date):
    """Pure evaluation; callers preload library_entry, territories, decisions.

    History deliberately includes rejected/superseded positions. Current basis
    is 6C's any-nonempty-territory/any-release question, not a usage decision.
    """
    local_id = getattr(local_organization, "pk", local_organization)
    if local_id is None:
        raise ValidationError("Lokal organisasjon må være konfigurert.")
    claims = tuple(
        c
        for c in claims
        if c.recording_id == managed.library_entry.recording_id
        and c.rights_holder_id == local_id
        and c.right_type in RightsClaim.RightType.values
        and claim_countries(c)
    )
    current_claims = evaluate_management_basis(
        claims, local_organization=local_id, on_date=on_date
    )
    current = any(
        c.status == VerificationStatus.CONFIRMED for c in current_claims
    )
    historical = any(_confirmed_history(c, on_date) for c in claims)
    unverified = any(c.status == VerificationStatus.UNVERIFIED for c in claims)
    disputed = any(c.status == VerificationStatus.DISPUTED for c in claims)
    future = any(
        c.status == VerificationStatus.CONFIRMED
        and not _has_started_period(c, on_date)
        for c in claims
    )
    # A materialized legacy status is not evidence of never having managed.
    uncertain = (
        not current
        and not historical
        and managed.status
        in {
            ManagedRecording.Status.ACTIVE,
            ManagedRecording.Status.INACTIVE,
        }
    )
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
        status = None
    return ManagementState(
        status=status,
        stored_status=managed.status,
        has_current_basis=current,
        has_confirmed_history=historical,
        has_unverified_basis=unverified,
        has_disputed_basis=disputed,
        has_future_confirmed_basis=future,
        history_uncertain=uncertain,
        requires_follow_up=uncertain
        or disputed
        or (
            status == ManagedRecording.Status.PENDING
            and not (unverified or future)
        ),
    )


def _local_organization_id():
    local_id = RightsConfiguration.objects.values_list(
        "local_organization_id", flat=True
    ).first()
    if local_id is None:
        raise ValidationError("Lokal organisasjon må være konfigurert.")
    return local_id


def _management_states(managed_records, *, local_id, on_date):
    grouped = defaultdict(list)
    # Include all statuses: current resolution and historical evidence differ.
    claims = (
        claims_for_recordings(
            [m.library_entry.recording_id for m in managed_records]
        )
        .filter(rights_holder_id=local_id)
        .prefetch_related(
            Prefetch(
                "decisions",
                queryset=RightsDecision.objects.order_by("created_at", "id"),
            )
        )
    )
    for claim in claims:
        grouped[claim.recording_id].append(claim)
    return {
        m.pk: evaluate_management_state(
            m,
            grouped[m.library_entry.recording_id],
            local_organization=local_id,
            on_date=on_date,
        )
        for m in managed_records
    }


def management_states(managed_records, *, on_date=None):
    """Read-only batch: one config + three claims/territories/decisions queries.

    At most one additional library-entry query if callers did not join it.
    Results are keyed by ManagedRecording UUID. No writes or locks on read.
    """
    records = tuple(managed_records)
    if not records:
        return {}
    prefetch_related_objects(records, "library_entry")
    return _management_states(
        records,
        local_id=_local_organization_id(),
        on_date=on_date or timezone.localdate(),
    )


def management_state(managed, *, on_date=None):
    """Read effective and stored lifecycle without changing either membership."""
    return management_states((managed,), on_date=on_date)[managed.pk]


def _save_status(managed, state):
    if state.needs_reconciliation:
        from flac_ingest.signals import suppress_automatic_flac_sync

        # All lifecycle statuses have identical 6B authority. Preserve ordinary
        # creation/authority signals and CanonicalModel revision validation.
        with suppress_automatic_flac_sync():
            managed.status = state.status
            managed.save(update_fields=("status",))


@transaction.atomic
def refresh_management_status(recording, *, on_date=None):
    """Reconcile existing membership only; never create or delete membership."""
    Recording.objects.select_for_update().get(pk=recording.pk)
    managed = (
        ManagedRecording.objects.select_for_update(of=("self",))
        .select_related("library_entry")
        .filter(library_entry__recording_id=recording.pk)
        .first()
    )
    if managed is None or not RightsConfiguration.objects.exists():
        return None
    state = management_state(managed, on_date=on_date)
    _save_status(managed, state)
    return state


@dataclass(frozen=True)
class ReconciliationResult:
    on_date: date
    dry_run: bool
    examined: int
    needs_reconciliation: int
    updated: int
    uncertain: int
    requires_follow_up: int


def reconcile_management_statuses(
    *, on_date=None, batch_size=200, dry_run=False
):
    """Reconcile existing members in bounded, independently atomic batches.

    Recording locks (ordered UUID) precede membership locks, as in decisions,
    superseding and explicit return. Re-read membership/claims after locking.
    Even dry-run evaluates under these locks, but saves nothing. A retry after
    a partially completed run is safe; no lifecycle scheduler is installed.
    """
    if not 1 <= batch_size <= 500:
        raise ValueError("Batchstørrelse må være mellom 1 og 500.")
    day = on_date or timezone.localdate()
    local_id = _local_organization_id()
    counts = dict(
        examined=0,
        needs_reconciliation=0,
        updated=0,
        uncertain=0,
        requires_follow_up=0,
    )
    cursor = None
    while True:
        candidates = ManagedRecording.objects.order_by(
            "library_entry__recording_id"
        )
        if cursor is not None:
            candidates = candidates.filter(
                library_entry__recording_id__gt=cursor
            )
        recording_ids = list(
            candidates.values_list("library_entry__recording_id", flat=True)[
                :batch_size
            ]
        )
        if not recording_ids:
            break
        with transaction.atomic():
            list(
                Recording.objects.select_for_update()
                .filter(pk__in=recording_ids)
                .order_by("pk")
                .values_list("pk", flat=True)
            )
            records = list(
                ManagedRecording.objects.select_for_update(of=("self",))
                .select_related("library_entry")
                .filter(library_entry__recording_id__in=recording_ids)
                .order_by("library_entry__recording_id")
            )
            states = _management_states(
                records, local_id=local_id, on_date=day
            )
            for managed in records:
                state = states[managed.pk]
                counts["examined"] += 1
                counts["uncertain"] += state.history_uncertain
                counts["requires_follow_up"] += state.requires_follow_up
                counts["needs_reconciliation"] += state.needs_reconciliation
                if not dry_run and state.needs_reconciliation:
                    _save_status(managed, state)
                    counts["updated"] += 1
        cursor = recording_ids[-1]
    return ReconciliationResult(on_date=day, dry_run=dry_run, **counts)
