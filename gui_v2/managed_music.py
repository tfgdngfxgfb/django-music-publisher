"""Read-only, bounded batch presentation of actual management memberships."""

from collections import defaultdict
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db.models import Prefetch, Q
from django.utils import timezone

from catalogue.models import RecordingContribution
from managed_music.lifecycle import management_states
from managed_music.models import ManagedRecording
from rights.models import RightsClaim
from rights.scope import claims_for_recordings, evaluate_management_basis
from rights.services import get_local_organization
from rights.summaries import classify_ownership
from rights_core.models import VerificationStatus as Status

BATCH_SIZE = 200
STATUS_LABELS = {
    "active": "Aktiv",
    "pending": "Under vurdering",
    "inactive": "Historisk/inaktiv",
    "uncertain": "Historikk må avklares",
    "unavailable": "Kan ikke vurderes",
}


def memberships(filters):
    queryset = ManagedRecording.objects.select_related(
        "library_entry__recording", "source_system"
    ).prefetch_related(
        "library_entry__recording__identifiers",
        Prefetch(
            "library_entry__recording__contributions",
            queryset=RecordingContribution.objects.select_related(
                "party", "artist_identity"
            ).order_by("display_order", "id"),
        ),
    )
    if term := filters.get("q"):
        try:
            recording_id = UUID(term)
        except (ValueError, TypeError):
            recording_id = None
        # Stable deep links from follow-up must also work for identical titles.
        if recording_id:
            queryset = queryset.filter(
                library_entry__recording_id=recording_id
            )
        else:
            queryset = _search_memberships(queryset, term)
    if source := filters.get("source"):
        queryset = queryset.filter(source_system=source)
    return queryset.order_by("library_entry__recording__title", "pk")


def _search_memberships(queryset, term):
    return queryset.filter(
        Q(library_entry__recording__title__icontains=term)
        | Q(
            library_entry__recording__identifiers__normalized_value__icontains=term
        )
        | Q(
            library_entry__recording__contributions__credited_as__icontains=term
        )
        | Q(
            library_entry__recording__contributions__party__name__icontains=term
        )
        | Q(
            library_entry__recording__contributions__artist_identity__display_name__icontains=term
        )
    ).distinct()


def present_batch(records, *, can_view_rights, on_date=None):
    """Constant query count within a batch, including history and identities."""
    from gui_v2.views import _artist_text

    records = list(records)
    if not records:
        return []
    day = on_date or timezone.localdate()
    try:
        states = management_states(records, on_date=day)
    except ValidationError:
        # Missing configuration must never masquerade as a returnable state.
        states = {}
    grouped = defaultdict(list)
    local = get_local_organization() if can_view_rights else None
    if can_view_rights:
        for claim in claims_for_recordings([m.recording.pk for m in records]):
            grouped[claim.recording_id].append(claim)
    rows = []
    for managed in records:
        recording = managed.recording
        state = states.get(managed.pk)
        key = (
            "unavailable"
            if state is None
            else "uncertain" if state.history_uncertain else state.status
        )
        follow_up = []
        if state is None:
            follow_up.append("Lokal organisasjon må konfigureres")
        else:
            if state.history_uncertain:
                follow_up.append("Historikk må avklares")
            if state.has_disputed_basis:
                follow_up.append("Bestridt P7-grunnlag")
            if state.status == "pending" and not state.has_plausible_basis:
                follow_up.append("Mangler plausibelt P7-grunnlag")
            if state.needs_reconciliation:
                follow_up.append("Venter på systemoppdatering")
        row = {
            "managed": managed,
            "recording": recording,
            "state": state,
            "status": key,
            "status_label": STATUS_LABELS[key],
            "artist": _artist_text(recording),
            "isrc": next(
                (
                    i.normalized_value
                    for i in recording.identifiers.all()
                    if i.scheme == "ISRC"
                ),
                "",
            ),
            "follow_up": follow_up,
        }
        if can_view_rights:
            claims = grouped[recording.pk]
            basis = evaluate_management_basis(
                claims, local_organization=local, on_date=day
            )
            row["ownership"] = classify_ownership(claims, local, on_date=day)
            for name, kind in (
                ("administration", RightsClaim.RightType.ADMINISTRATION),
                ("distribution", RightsClaim.RightType.DISTRIBUTION),
            ):
                confirmed = [
                    c
                    for c in basis
                    if c.right_type == kind and c.status == Status.CONFIRMED
                ]
                pending = [
                    c
                    for c in claims
                    if c.right_type == kind
                    and local
                    and c.rights_holder_id == local.pk
                    and c.status in (Status.UNVERIFIED, Status.DISPUTED)
                ]
                row[name] = {
                    "confirmed": len(confirmed),
                    "pending": len(pending),
                    "release_scoped": sum(
                        c.release_scope_id is not None for c in confirmed
                    ),
                }
        rows.append(row)
    return rows


def matches(row, filters):
    status = filters.get("status", "")
    if status and status != "all" and row["status"] != status:
        return False
    if not status and row["status"] == "inactive" and not row["follow_up"]:
        return False
    follow_up = filters.get("follow_up")
    if follow_up and bool(row["follow_up"]) != (follow_up == "yes"):
        return False
    if filters.get("ownership") and row.get("ownership"):
        if row["ownership"].category != filters["ownership"]:
            return False
    for name in ("administration", "distribution"):
        selected = filters.get(name)
        if selected and name in row:
            summary = row[name]
            if selected == "confirmed" and not summary["confirmed"]:
                return False
            if selected == "pending" and not summary["pending"]:
                return False
            if selected == "none" and (
                summary["confirmed"] or summary["pending"]
            ):
                return False
    return True


def filtered_rows(filters, *, can_view_rights):
    """Derive before pagination. Retain only matching rows, fetch in chunks.

    Lifecycle predicates cannot use stored status. All SQL-narrowed candidates
    must be evaluated; memory/query growth is bounded per 200-record batch.
    """
    batch = []
    day = timezone.localdate()
    for managed in memberships(filters).iterator(chunk_size=BATCH_SIZE):
        batch.append(managed)
        if len(batch) == BATCH_SIZE:
            yield from (
                r
                for r in present_batch(
                    batch, can_view_rights=can_view_rights, on_date=day
                )
                if matches(r, filters)
            )
            batch = []
    if batch:
        yield from (
            r
            for r in present_batch(
                batch, can_view_rights=can_view_rights, on_date=day
            )
            if matches(r, filters)
        )
