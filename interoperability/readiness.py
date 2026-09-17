"""Pure assessment of loaded canonical facts. Never evaluates legal clearance."""

from django.core.exceptions import ValidationError

from catalogue.validators import normalize_isrc, normalize_trade_item_number
from interoperability.contracts import (
    Assessment,
    Disposition,
    EntityFacts,
    ReadinessReport,
)
from interoperability.registry import REGISTRY


def milliseconds_to_duration(value):
    if type(value) is not int or value < 0:
        raise ValueError(
            "Duration must be a nonnegative integer of milliseconds."
        )
    seconds, remainder = divmod(value, 1000)
    fraction = f".{remainder:03d}".rstrip("0") if remainder else ""
    return f"PT{seconds}{fraction}S"


TRANSFORMS = {"milliseconds_to_duration": milliseconds_to_duration}


def _present(value):
    return (
        value is not None
        and value != ()
        and (not isinstance(value, str) or bool(value.strip()))
    )


def _validate_direct(entry, facts):
    values = tuple(facts.get(c) for c in entry.canonical_concepts)
    if any(
        not isinstance(value, str) or not value.strip() for value in values
    ):
        raise ValueError(
            "This text/identifier mapping needs nonempty strings."
        )
    if "recording.isrc" in entry.canonical_concepts:
        if normalize_isrc(values[0]) != values[0]:
            raise ValueError(
                "Supply canonical normalized ISRC, not raw input."
            )
    if "release.barcode" in entry.canonical_concepts:
        barcode, scheme = values
        if scheme not in ("UPC", "EAN", "GTIN"):
            raise ValueError("Unknown trade-item identifier scheme.")
        if normalize_trade_item_number(barcode, scheme) != barcode:
            raise ValueError("Supply a canonical normalized barcode.")


def assess_interoperability(entity_facts, standard_id, *, registry=REGISTRY):
    """Assess concept representability, not a schema-valid message or export grant.

    Caller supplies one explicit context. Unknown keys are reported rather than
    guessed. Phase/gap/LOSSY mappings cannot become DIRECT by supplying a value.
    """
    if not isinstance(entity_facts, EntityFacts):
        raise TypeError(
            "Pass EntityFacts; this API does not load model relations."
        )
    adapter = registry.get(standard_id)
    assessments = []
    known = set()
    for entry in adapter.entries:
        known.update(entry.canonical_concepts)
        disposition = entry.disposition
        reason = entry.notes
        derived = None
        if disposition in (Disposition.DIRECT, Disposition.DERIVED):
            missing = tuple(
                c
                for c in entry.canonical_concepts
                if not _present(entity_facts.get(c))
            )
            if missing:
                disposition = Disposition.MISSING
                reason = "Missing canonical facts: " + ", ".join(missing)
            else:
                try:
                    if disposition == Disposition.DERIVED:
                        if entry.transform not in TRANSFORMS:
                            raise ValueError("Undeclared readiness transform.")
                        derived = TRANSFORMS[entry.transform](
                            *(
                                entity_facts.get(c)
                                for c in entry.canonical_concepts
                            )
                        )
                    else:
                        _validate_direct(entry, entity_facts)
                except (ValueError, ValidationError) as error:
                    disposition = Disposition.MISSING
                    reason = f"Invalid canonical facts: {error}"
        assessments.append(Assessment(entry, disposition, reason, derived))
    ready = (
        Disposition.DIRECT,
        Disposition.DERIVED,
        Disposition.NOT_APPLICABLE,
    )
    return ReadinessReport(
        standard_id,
        tuple(assessments),
        tuple(
            a.entry.standard_path
            for a in assessments
            if a.entry.standard_path in adapter.core_paths
            and a.disposition not in ready
        ),
        tuple(
            sorted(
                f.concept for f in entity_facts.facts if f.concept not in known
            )
        ),
    )


def assess_rdrn_readiness(entity_facts):
    return assess_interoperability(entity_facts, "ddex:rdr-n:1.5")
