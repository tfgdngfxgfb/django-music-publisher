"""Immutable source-neutral facts. No ORM objects or mutable payloads."""

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum


class Disposition(StrEnum):
    DIRECT = "DIRECT"
    NORMALIZED = "NORMALIZED"
    MATCH_REQUIRED = "MATCH_REQUIRED"
    ASSERTION_ONLY = "ASSERTION_ONLY"
    WORKFLOW_REQUIRED = "WORKFLOW_REQUIRED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN_SOURCE = "UNKNOWN_SOURCE"


class Readiness(StrEnum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    WORKFLOW_REQUIRED = "WORKFLOW_REQUIRED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    PHASE_7 = "PHASE_7"
    PHASE_8 = "PHASE_8"
    UNKNOWN = "UNKNOWN"
    GAP = "GAP"


class Concept(StrEnum):
    RECORDING_TITLE = "Recording.title"
    RECORDING_DURATION = "Recording.duration_ms"
    RECORDING_LANGUAGE = "Recording.language"
    RECORDING_KIND = "Recording.recording_kind"
    RELEASE_TITLE = "Release.title"
    RELEASE_YEAR = "Release.release_year"
    RELEASE_DATE = "Release.release_date"
    RELEASE_TRACK = "ReleaseTrack"
    ISRC = "ExternalIdentifier.ISRC"
    UPC = "ExternalIdentifier.UPC"
    EAN = "ExternalIdentifier.EAN"
    GTIN = "ExternalIdentifier.GTIN"
    EXTERNAL_RECORDING_ID = "Recording.ExternalIdentifier.EXTERNAL"
    EXTERNAL_RELEASE_ID = "Release.ExternalIdentifier.EXTERNAL"
    CONTRIBUTOR = "RecordingContribution"
    PARTY = "Party/ArtistIdentity"
    LABEL = "Label"
    OWNERSHIP = "RightsClaim.master_ownership"
    ADMINISTRATION = "RightsClaim.master_administration"
    DISTRIBUTION = "RightsClaim.distribution"
    AGREEMENT = "Agreement"
    PROVENANCE = "SourceRecord.raw_payload"
    MANAGED_RECORDING = "ManagedRecording"
    MANAGED_RELEASE = "ManagedRelease"
    WORK = "Work/publishing"


def _json_value(value):
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is float:
        # allow_nan=False below rejects non-JSON NaN and infinities.
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict and all(type(k) is str for k in value):
        for item in value.values():
            _json_value(item)
        return
    raise TypeError("Payload must contain JSON values and string object keys.")


@dataclass(frozen=True)
class FrozenJSON:
    """Serialized semantic JSON snapshot; decode always returns a fresh copy."""

    text: str

    def __post_init__(self):
        if not isinstance(self.text, str):
            raise TypeError(
                "FrozenJSON stores immutable text, not bytes/buffers."
            )
        value = json.loads(self.text)
        _json_value(value)
        json.dumps(value, allow_nan=False)

    @classmethod
    def capture(cls, value):
        if isinstance(value, cls):
            return value
        _json_value(value)
        return cls(json.dumps(value, ensure_ascii=False, allow_nan=False))

    def decode(self):
        return json.loads(self.text)

    @property
    def fingerprint(self):
        canonical = json.dumps(
            self.decode(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SourceEnvelope:
    source_name: str
    source_version: str | None
    external_record_id: str | None
    source_locator: str | None
    raw_payload: FrozenJSON

    def __post_init__(self):
        if (
            not isinstance(self.source_name, str)
            or not self.source_name.strip()
        ):
            raise ValueError("A stable source identity is required.")
        for value in (
            self.source_version,
            self.external_record_id,
            self.source_locator,
        ):
            if value is not None and not isinstance(value, str):
                raise TypeError(
                    "Source identity/version/locator must be text."
                )
        object.__setattr__(
            self, "raw_payload", FrozenJSON.capture(self.raw_payload)
        )


@dataclass(frozen=True)
class FieldMapping:
    """Explicit reviewed interpretation, never inferred from a column name."""

    source_path: tuple
    concept: Concept
    namespace: str = ""

    def __post_init__(self):
        path = tuple(self.source_path)
        if not path or any(type(p) not in (str, int) for p in path):
            raise ValueError("Use a nonempty JSON path of keys/array indexes.")
        if any(type(p) is int and p < 0 for p in path):
            raise ValueError("Array indexes cannot be negative.")
        object.__setattr__(self, "source_path", path)
        object.__setattr__(self, "concept", Concept(self.concept))
        external = self.concept in (
            Concept.EXTERNAL_RECORDING_ID,
            Concept.EXTERNAL_RELEASE_ID,
        )
        if external != bool(self.namespace.strip()):
            raise ValueError("Only EXTERNAL IDs require a nonblank namespace.")


@dataclass(frozen=True)
class MappingPlan:
    source_name: str
    source_version: str | None
    schema_reference: str
    fields: tuple[FieldMapping, ...]

    def __post_init__(self):
        if not self.schema_reference.strip():
            raise ValueError("A reviewed schema/sample reference is required.")
        fields = tuple(self.fields)
        for field in fields:
            if not isinstance(field, FieldMapping):
                raise TypeError("Expected FieldMapping facts.")
        paths = [f.source_path for f in fields]
        if any(
            a == b[: len(a)]
            for i, a in enumerate(paths)
            for j, b in enumerate(paths)
            if i != j
        ):
            raise ValueError("Source mappings cannot duplicate or overlap.")
        if len({f.concept for f in fields}) != len(fields):
            raise ValueError("Conflicting mappings for one canonical concept.")
        object.__setattr__(self, "fields", fields)


@dataclass(frozen=True)
class MappingResult:
    source_path: tuple
    concept: Concept | None
    disposition: Disposition
    raw_value: FrozenJSON
    normalized_value: FrozenJSON | None = None
    reason: str = ""
    dependency: str | None = None
    proposed_status: str | None = None
    namespace: str = ""

    def __post_init__(self):
        object.__setattr__(self, "source_path", tuple(self.source_path))
        object.__setattr__(self, "disposition", Disposition(self.disposition))
        if self.concept is not None:
            object.__setattr__(self, "concept", Concept(self.concept))
        object.__setattr__(
            self, "raw_value", FrozenJSON.capture(self.raw_value)
        )
        if self.normalized_value is not None:
            object.__setattr__(
                self,
                "normalized_value",
                FrozenJSON.capture(self.normalized_value),
            )


@dataclass(frozen=True)
class CanonicalValue:
    concept: Concept
    value: FrozenJSON

    def __post_init__(self):
        object.__setattr__(self, "concept", Concept(self.concept))
        object.__setattr__(self, "value", FrozenJSON.capture(self.value))


@dataclass(frozen=True)
class MatrixEntry:
    area: str
    canonical_status: Readiness
    note: str
    source_status: Readiness = Readiness.UNKNOWN

    def __post_init__(self):
        object.__setattr__(
            self, "canonical_status", Readiness(self.canonical_status)
        )
        object.__setattr__(
            self, "source_status", Readiness(self.source_status)
        )


@dataclass(frozen=True)
class SourceProfile:
    source_name: str
    display_name: str
    notes: tuple[str, ...]
    matrix: tuple[MatrixEntry, ...]
    schema_status: str = "SAMPLE_REQUIRED"

    def __post_init__(self):
        object.__setattr__(self, "notes", tuple(self.notes))
        object.__setattr__(self, "matrix", tuple(self.matrix))


class ReimportAction(StrEnum):
    NEW_OBSERVATION = "NEW_OBSERVATION"
    REUSE_EXISTING = "REUSE_EXISTING"
    DUPLICATE_PAYLOAD_REVIEW = "DUPLICATE_PAYLOAD_REVIEW"
    UPSTREAM_REVISION_GAP = "UPSTREAM_REVISION_GAP"


@dataclass(frozen=True)
class ReimportAssessment:
    action: ReimportAction
    reason: str


@dataclass(frozen=True)
class ReadinessReport:
    source: SourceEnvelope
    mappings: tuple[MappingResult, ...]
    reimport: ReimportAssessment
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    manual_review: tuple[str, ...] = ()
    unsupported: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()

    def __post_init__(self):
        for name in (
            "mappings",
            "blockers",
            "warnings",
            "manual_review",
            "unsupported",
            "unknowns",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
