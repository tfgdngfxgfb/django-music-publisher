"""Pure proposal classification. No source column names imply semantics."""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from django.core.exceptions import ValidationError

from catalogue.validators import (
    normalize_isrc,
    normalize_trade_item_number,
    validate_language,
)
from import_readiness.contracts import (
    Concept,
    Disposition,
    FrozenJSON,
    MappingResult,
)

IDENTIFIERS = {
    Concept.ISRC: "ISRC",
    Concept.UPC: "UPC",
    Concept.EAN: "EAN",
    Concept.GTIN: "GTIN",
    Concept.EXTERNAL_RECORDING_ID: "EXTERNAL",
    Concept.EXTERNAL_RELEASE_ID: "EXTERNAL",
}
RIGHTS = frozenset(
    (Concept.OWNERSHIP, Concept.ADMINISTRATION, Concept.DISTRIBUTION)
)
MEMBERSHIPS = frozenset((Concept.MANAGED_RECORDING, Concept.MANAGED_RELEASE))


def normalize_identifier(scheme, value, namespace=""):
    """Reuse canonical validators; namespace/value case stays significant."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing identifier; do not invent one.")
    if scheme == "EXTERNAL":
        if not namespace.strip():
            raise ValueError("EXTERNAL requires the correct source namespace.")
        return value.strip()
    if namespace:
        raise ValueError("ISRC/barcodes use the global namespace.")
    if scheme == "ISRC":
        return normalize_isrc(value)
    if scheme in ("UPC", "EAN", "GTIN"):
        return normalize_trade_item_number(value, scheme)
    raise ValueError("Unsupported identifier scheme.")


def classify_mapping(field, value, *, protected=False, canonical=None):
    concept = field.concept
    raw = FrozenJSON.capture(value)
    disposition, normalized, reason = (
        Disposition.DIRECT,
        value,
        "Representable proposal only; no write authority.",
    )
    dependency = proposed_status = None
    if value is None or value == "":
        disposition, normalized, reason = (
            Disposition.MANUAL_REVIEW,
            None,
            "Missing value remains missing.",
        )
    elif concept in RIGHTS:
        disposition = Disposition.WORKFLOW_REQUIRED
        proposed_status = "unverified"
        reason = "Independent legal position; existing rights validation/workflow required, never auto-confirm."
    elif concept in MEMBERSHIPS:
        disposition = Disposition.WORKFLOW_REQUIRED
        normalized = None
        reason = "Explicit P7 management workflow required; source membership/status is not applied."
    elif concept == Concept.AGREEMENT:
        disposition = Disposition.WORKFLOW_REQUIRED
        reason = (
            "Documentation workflow; does not set claim scope or verification."
        )
    elif concept in (Concept.PARTY, Concept.CONTRIBUTOR):
        disposition, dependency = Disposition.MANUAL_REVIEW, "PHASE_7"
        reason = "Preserve credit/identity observations; no inferred Party identity."
    elif concept == Concept.WORK:
        disposition, dependency = Disposition.UNSUPPORTED, "PHASE_8"
        reason = "Work/publishing requires its own canonical domain."
    elif concept in (Concept.RELEASE_TRACK, Concept.LABEL):
        disposition = Disposition.MATCH_REQUIRED
        reason = "Existing catalogue objects/structure must be matched and reviewed."
    else:
        try:
            if concept in IDENTIFIERS:
                normalized = normalize_identifier(
                    IDENTIFIERS[concept], value, field.namespace
                )
                disposition = Disposition.MATCH_REQUIRED
                reason = "Normalized identifier is a match signal, never canonical UUID or merge authority."
            elif concept in (Concept.RECORDING_TITLE, Concept.RELEASE_TITLE):
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("A nonblank title is required.")
                normalized = value.strip()
            elif concept == Concept.RECORDING_DURATION:
                if type(value) is not int or value < 0:
                    raise ValueError(
                        "Duration must already be explicit integer milliseconds."
                    )
            elif concept == Concept.RECORDING_LANGUAGE:
                if not isinstance(value, str):
                    raise ValueError("Language must be an explicit code.")
                validate_language(value)
            elif concept == Concept.RECORDING_KIND:
                if value not in ("sound", "video"):
                    raise ValueError(
                        "Recording kind requires explicit sound/video mapping."
                    )
            elif concept == Concept.RELEASE_YEAR:
                if type(value) is not int or not 1800 <= value <= 2200:
                    raise ValueError(
                        "Release year must be an explicit year, never a synthesized full date."
                    )
            elif concept == Concept.RELEASE_DATE:
                if not isinstance(value, str) or len(value) != 10:
                    raise ValueError(
                        "Full ISO date required; year-only data remains a year."
                    )
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError("Full ISO date required.")
            if normalized != value:
                if disposition == Disposition.DIRECT:
                    disposition = Disposition.NORMALIZED
        except (ValidationError, ValueError, TypeError) as error:
            disposition, normalized, reason = (
                Disposition.MANUAL_REVIEW,
                None,
                str(error),
            )

    mismatch = (
        canonical is not None
        and normalized is not None
        and canonical.value.fingerprint
        != FrozenJSON.capture(normalized).fingerprint
    )
    # Caller supplies 6B's loaded objectwise authority. Never recompute it here.
    if concept not in RIGHTS | MEMBERSHIPS | {
        Concept.AGREEMENT,
        Concept.WORK,
        Concept.PROVENANCE,
    }:
        if protected or mismatch:
            disposition = Disposition.ASSERTION_ONLY
            reason = (
                "Protected canonical metadata; preserve an assertion for review."
                if protected
                else "Source differs from canonical data; preserve an assertion for review."
            )
    return MappingResult(
        field.source_path,
        concept,
        disposition,
        raw,
        FrozenJSON.capture(normalized) if normalized is not None else None,
        reason,
        dependency,
        proposed_status,
        field.namespace,
    )


class MatchStrength(StrEnum):
    STRONG = "STRONG"
    CANDIDATE = "CANDIDATE"
    INSUFFICIENT = "INSUFFICIENT"
    NONE = "NONE"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class IdentifierFact:
    scheme: str
    value: str
    namespace: str = ""

    def __post_init__(self):
        object.__setattr__(
            self,
            "value",
            normalize_identifier(self.scheme, self.value, self.namespace),
        )


@dataclass(frozen=True)
class MatchFacts:
    """Loaded facts for one Recording; release_context is an already matched ID."""

    identifiers: tuple[IdentifierFact, ...] = ()
    title: str | None = None
    artist_credit: str | None = None
    duration_ms: int | None = None
    release_context: str | None = None

    def __post_init__(self):
        identifiers = tuple(self.identifiers)
        for value in (self.title, self.artist_credit, self.release_context):
            if value is not None and not isinstance(value, str):
                raise TypeError("Match labels/context must be immutable text.")
        if self.duration_ms is not None and (
            type(self.duration_ms) is not int or self.duration_ms < 0
        ):
            raise ValueError(
                "Duration must be nonnegative integer milliseconds."
            )
        if any(not isinstance(i, IdentifierFact) for i in identifiers):
            raise TypeError("Use immutable IdentifierFact values.")
        if any(i.scheme not in ("ISRC", "EXTERNAL") for i in identifiers):
            raise ValueError("Recording matching cannot use Release barcodes.")
        keys = [(i.scheme, i.namespace) for i in identifiers]
        if len(set(keys)) != len(keys):
            raise ValueError(
                "One identifier per scheme/namespace, as in the canonical model."
            )
        object.__setattr__(self, "identifiers", identifiers)


@dataclass(frozen=True)
class MatchAssessment:
    strength: MatchStrength
    signals: tuple[str, ...]
    disposition: Disposition = Disposition.MATCH_REQUIRED


def assess_recording_match(incoming, candidate):
    """Classify signals without querying, selecting a winner, or merging."""
    left = {(i.scheme, i.namespace): i.value for i in incoming.identifiers}
    right = {(i.scheme, i.namespace): i.value for i in candidate.identifiers}
    shared = sorted(left.keys() & right.keys())
    conflicts = tuple(
        f"conflicting {scheme}:{namespace}"
        for scheme, namespace in shared
        if left[scheme, namespace] != right[scheme, namespace]
    )
    if conflicts:
        return MatchAssessment(
            MatchStrength.CONFLICT, conflicts, Disposition.MANUAL_REVIEW
        )
    signals = tuple(
        f"same {scheme}:{namespace}" for scheme, namespace in shared
    )
    if signals:
        return MatchAssessment(MatchStrength.STRONG, signals)
    weak = []
    for name in ("title", "artist_credit"):
        a, b = getattr(incoming, name), getattr(candidate, name)
        if (
            a
            and b
            and a.strip()
            and a.strip().casefold() == b.strip().casefold()
        ):
            weak.append(name)
    if (
        incoming.duration_ms is not None
        and candidate.duration_ms is not None
        and abs(incoming.duration_ms - candidate.duration_ms) <= 3000
    ):
        weak.append("duration_ms")
    if (
        incoming.release_context
        and incoming.release_context == candidate.release_context
    ):
        weak.append("release_context")
    strength = (
        MatchStrength.CANDIDATE
        if len(weak) >= 2
        else MatchStrength.INSUFFICIENT if weak else MatchStrength.NONE
    )
    return MatchAssessment(strength, tuple(weak))


def group_legal_positions(loaded_positions):
    """Group input indexes, not additive shares; no claim creation or decisions."""
    from rights.positions import legal_position_identity

    groups = {}
    for index, claim in enumerate(loaded_positions):
        groups.setdefault(legal_position_identity(claim), []).append(index)
    return tuple(tuple(indices) for indices in groups.values())
