"""Read-only Recording rights presentation over 6B/6C/6D; no clearance."""

from django.db.models import Prefetch
from django.utils import timezone

from catalogue.authority import recording_authority
from managed_music.lifecycle import evaluate_management_state
from managed_music.models import ManagedRecording
from rights.models import RightsClaim, RightsDecision
from rights.scope import claim_applies_to_date, evaluate_management_basis
from rights.services import DECISION_TRANSITIONS, get_local_organization
from rights.summaries import classify_ownership
from rights_core.models import VerificationStatus as Status

STATUS_LABELS = {
    Status.UNVERIFIED: "Uverifisert",
    Status.CONFIRMED: "Bekreftet",
    Status.DISPUTED: "Bestridt",
    Status.REJECTED: "Avvist",
    Status.SUPERSEDED: "Erstattet",
}
DECISION_LABELS = {
    Status.CONFIRMED: "Bekreft",
    Status.DISPUTED: "Bestrid",
    Status.REJECTED: "Avvis",
}


def claims_queryset(recording):
    return (
        RightsClaim.objects.filter(recording=recording)
        .select_related(
            "rights_holder",
            "grantor",
            "release_scope",
            "agreement",
            "source_record__source_system",
            "supersedes",
        )
        .prefetch_related(
            "territories",
            Prefetch(
                "decisions",
                queryset=RightsDecision.objects.select_related(
                    "decided_by"
                ).order_by("created_at", "id"),
            ),
        )
        .order_by("created_at", "id")
    )


def claim_presentation(claim, *, on_date=None):
    day = on_date or timezone.localdate()
    current = claim_applies_to_date(claim, day)
    upcoming = (
        not current and claim.valid_from is not None and claim.valid_from > day
    )
    period_state = (
        "Aktuell" if current else "Kommende" if upcoming else "Utløpt"
    )
    group = (
        "attention"
        if claim.status in (Status.UNVERIFIED, Status.DISPUTED)
        else (
            "confirmed"
            if claim.status == Status.CONFIRMED and (current or upcoming)
            else "history"
        )
    )
    status = STATUS_LABELS[claim.status]
    if claim.status == Status.CONFIRMED:
        status += " – " + period_state.lower()
    return {
        "claim": claim,
        "group": group,
        "status": status,
        "tone": {
            Status.UNVERIFIED: "warning",
            Status.DISPUTED: "error",
            Status.CONFIRMED: "ok" if current else "info",
        }.get(claim.status, "neutral"),
        "territory": claim.territory_display,
        "period_state": period_state,
        "decisions": [
            {"value": value, "label": DECISION_LABELS[value]}
            for value in DECISION_LABELS
            if value in DECISION_TRANSITIONS[claim.status]
        ],
    }


def build_recording_rights(recording):
    claims = list(claims_queryset(recording))
    local = get_local_organization()
    day = timezone.localdate()
    managed = (
        ManagedRecording.objects.select_related("library_entry")
        .filter(library_entry__recording=recording)
        .first()
    )
    state = (
        evaluate_management_state(
            managed, claims, local_organization=local, on_date=day
        )
        if managed and local
        else None
    )
    authority = recording_authority(recording)
    if state and state.history_uncertain:
        management_label = "Historikk må avklares"
    elif state:
        management_label = {
            "active": "Aktiv",
            "pending": "Under vurdering",
            "inactive": "Historisk/inaktiv forvaltning",
        }[state.status]
    elif managed:
        management_label = (
            "Forvaltning kan ikke vurderes – lokal organisasjon mangler"
        )
    else:
        management_label = (
            "Kun via forvaltet utgivelse"
            if authority.managed_release_only
            else "Ikke i Forvaltet musikk"
        )
    basis = evaluate_management_basis(
        claims, local_organization=local, on_date=day
    )
    summaries = []
    for kind in (
        RightsClaim.RightType.ADMINISTRATION,
        RightsClaim.RightType.DISTRIBUTION,
    ):
        summaries.append(
            {
                "label": kind.label,
                "confirmed": sum(
                    c.right_type == kind and c.status == Status.CONFIRMED
                    for c in basis
                ),
                "pending": sum(
                    c.right_type == kind
                    and c.status in (Status.UNVERIFIED, Status.DISPUTED)
                    and local is not None
                    and c.rights_holder_id == local.pk
                    for c in claims
                ),
            }
        )
    rows = [claim_presentation(c, on_date=day) for c in claims]
    groups = [
        {
            "key": key,
            "label": label,
            "rows": [r for r in rows if r["group"] == key],
        }
        for key, label in (
            ("attention", "Krever behandling"),
            ("confirmed", "Bekreftede posisjoner"),
            ("history", "Historikk"),
        )
    ]
    return {
        "managed": managed,
        "management_state": state,
        "management_label": management_label,
        "ownership": classify_ownership(claims, local, on_date=day),
        "basis_summaries": summaries,
        "claim_groups": groups,
        "unverified_count": sum(c.status == Status.UNVERIFIED for c in claims),
        "disputed_count": sum(c.status == Status.DISPUTED for c in claims),
    }
