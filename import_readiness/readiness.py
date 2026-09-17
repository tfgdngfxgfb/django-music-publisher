"""Readiness from supplied snapshots only; no queries, writes or transport."""

from import_readiness.contracts import (
    Concept,
    Disposition,
    MappingResult,
    ReadinessReport,
    ReimportAction,
    ReimportAssessment,
)
from import_readiness.mapping import classify_mapping
from import_readiness.profiles import get_profile


def assess_reimport(source, existing_sources=()):
    same_source = tuple(
        s for s in existing_sources if s.source_name == source.source_name
    )
    same_id = tuple(
        s
        for s in same_source
        if source.external_record_id
        and s.external_record_id == source.external_record_id
    )
    if same_id:
        if any(
            s.raw_payload.fingerprint != source.raw_payload.fingerprint
            for s in same_id
        ):
            return ReimportAssessment(
                ReimportAction.UPSTREAM_REVISION_GAP,
                "Same source ID with changed payload: preserve prior SourceRecord; revision policy/schema gap requires review.",
            )
        return ReimportAssessment(
            ReimportAction.REUSE_EXISTING,
            "Same source and external ID with identical payload; reuse immutable provenance, no new record.",
        )
    if any(
        s.raw_payload.fingerprint == source.raw_payload.fingerprint
        for s in same_source
    ):
        return ReimportAssessment(
            ReimportAction.DUPLICATE_PAYLOAD_REVIEW,
            "Identical payload with different/missing source ID does not prove the same source record or canonical object.",
        )
    return ReimportAssessment(
        ReimportAction.NEW_OBSERVATION,
        "No matching supplied source snapshot; preserve provenance before any future apply.",
    )


def _leaf_paths(value, path=()):
    if isinstance(value, dict) and value:
        for key in sorted(value):
            yield from _leaf_paths(value[key], path + (key,))
    elif isinstance(value, list) and value:
        for index, item in enumerate(value):
            yield from _leaf_paths(item, path + (index,))
    else:
        yield path, value


def _value_at(payload, path):
    for part in path:
        if type(part) is int and not isinstance(payload, list):
            raise KeyError(path)
        if type(part) is str and not isinstance(payload, dict):
            raise KeyError(path)
        payload = payload[part]
    return payload


def _label(path):
    return "/" + "/".join(
        str(p).replace("~", "~0").replace("/", "~1") for p in path
    )


def assess_import_readiness(
    source,
    *,
    plan=None,
    protected_concepts=(),
    canonical_values=(),
    existing_sources=(),
):
    """A plan is an explicit reviewed interpretation, not a bundled adapter.

    protected_concepts comes from loaded 6B authority facts. The caller must
    include protected Recording/Release/ReleaseTrack concepts independently.
    All returned values remain proposals even when DIRECT or NORMALIZED.
    """
    protected = frozenset(Concept(c) for c in protected_concepts)
    canonical = {v.concept: v for v in canonical_values}
    profile = get_profile(source.source_name)
    blockers, warnings, manual, unsupported, unknowns = [], [], [], [], []
    mappings = []
    if plan is not None and (plan.source_name, plan.source_version) != (
        source.source_name,
        source.source_version,
    ):
        raise ValueError(
            "Mapping plan must match the exact source identity/version."
        )
    if plan is None:
        blockers.append("SAMPLE_REQUIRED: no reviewed source mapping plan.")
    if profile is None:
        warnings.append(
            "UNKNOWN_SOURCE: no bundled profile for this source identity."
        )
    else:
        warnings.extend(profile.notes)
    payload = source.raw_payload.decode()
    mapped_paths = []
    for field in sorted(
        plan.fields if plan else (), key=lambda f: _label(f.source_path)
    ):
        try:
            value = _value_at(payload, field.source_path)
        except (KeyError, IndexError):
            unknowns.append(
                f"MISSING_SOURCE_VALUE: {_label(field.source_path)}"
            )
            manual.append(
                f"{field.concept}: missing value; no default is invented."
            )
            continue
        mapped_paths.append(field.source_path)
        result = classify_mapping(
            field,
            value,
            protected=field.concept in protected,
            canonical=canonical.get(field.concept),
        )
        mappings.append(result)
        if result.disposition in (
            Disposition.ASSERTION_ONLY,
            Disposition.MANUAL_REVIEW,
            Disposition.MATCH_REQUIRED,
            Disposition.WORKFLOW_REQUIRED,
        ):
            manual.append(f"{_label(field.source_path)}: {result.reason}")
        if result.disposition == Disposition.UNSUPPORTED:
            unsupported.append(f"{field.concept}: {result.dependency}")
    for path, value in _leaf_paths(payload):
        if any(path[: len(mapped)] == mapped for mapped in mapped_paths):
            continue
        mappings.append(
            MappingResult(
                path,
                None,
                Disposition.UNKNOWN_SOURCE,
                value,
                reason="Unmapped source observation; no canonical interpretation is inferred.",
            )
        )
        unknowns.append(f"UNKNOWN_SOURCE_FIELD: {_label(path)}")
    reimport = assess_reimport(source, existing_sources)
    if reimport.action == ReimportAction.UPSTREAM_REVISION_GAP:
        blockers.append(reimport.reason)
    elif reimport.action == ReimportAction.DUPLICATE_PAYLOAD_REVIEW:
        manual.append(reimport.reason)
    return ReadinessReport(
        source,
        tuple(mappings),
        reimport,
        tuple(blockers),
        tuple(warnings),
        tuple(manual),
        tuple(unsupported),
        tuple(unknowns),
    )
