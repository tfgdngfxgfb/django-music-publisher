"""Immutable contracts containing loaded facts, never ORM objects."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


class Disposition(StrEnum):
    DIRECT = "DIRECT"
    DERIVED = "DERIVED"
    PHASE_7 = "PHASE_7"
    PHASE_8 = "PHASE_8"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    MISSING = "MISSING"
    LOSSY = "LOSSY"


class Direction(StrEnum):
    IMPORT = "IMPORT"
    EXPORT = "EXPORT"
    BOTH = "BOTH"


class Dependency(StrEnum):
    PHASE_7 = "PHASE_7"
    PHASE_8 = "PHASE_8"


def _immutable(value):
    if type(value) in (str, int, bool, date, type(None)):
        return value
    if type(value) is Decimal and value.is_finite():
        return value
    if type(value) is tuple:
        return tuple(_immutable(item) for item in value)
    raise TypeError("Facts must be immutable scalars/tuples, not ORM objects.")


@dataclass(frozen=True)
class Fact:
    concept: str
    value: object

    def __post_init__(self):
        if not isinstance(self.concept, str) or not self.concept.strip():
            raise ValueError("A canonical concept is required.")
        _immutable(self.value)


@dataclass(frozen=True)
class EntityFacts:
    """One explicitly chosen Recording/Release context, loaded by the caller."""

    facts: tuple[Fact, ...] = ()

    def __post_init__(self):
        values = tuple(self.facts)
        if any(not isinstance(fact, Fact) for fact in values):
            raise TypeError("Supply Fact instances, not model instances.")
        if len({fact.concept for fact in values}) != len(values):
            raise ValueError("Duplicate canonical fact.")
        object.__setattr__(
            self, "facts", tuple(sorted(values, key=lambda f: f.concept))
        )

    def get(self, concept):
        return next(
            (f.value for f in self.facts if f.concept == concept), None
        )


@dataclass(frozen=True)
class MappingEntry:
    standard_id: str
    standard_path: str
    canonical_concepts: tuple[str, ...]
    direction: Direction
    disposition: Disposition
    notes: str
    transform: str | None = None
    dependencies: tuple[Dependency, ...] = ()

    def __post_init__(self):
        if not self.standard_id.strip() or not self.standard_path.strip():
            raise ValueError(
                "Versioned standard ID and concept path required."
            )
        object.__setattr__(self, "direction", Direction(self.direction))
        object.__setattr__(self, "disposition", Disposition(self.disposition))
        concepts = tuple(self.canonical_concepts)
        if any(not isinstance(c, str) or not c.strip() for c in concepts):
            raise ValueError("Invalid canonical concept.")
        object.__setattr__(self, "canonical_concepts", concepts)
        object.__setattr__(
            self,
            "dependencies",
            tuple(sorted({Dependency(d) for d in self.dependencies})),
        )
        if self.disposition in (Disposition.DIRECT, Disposition.DERIVED):
            if not concepts:
                raise ValueError("Ready mappings must declare their facts.")
        if (self.disposition == Disposition.DERIVED) != bool(self.transform):
            raise ValueError("Only DERIVED mappings require a transform.")
        if self.disposition in (Disposition.PHASE_7, Disposition.PHASE_8):
            if Dependency(self.disposition.value) not in self.dependencies:
                raise ValueError("Phase disposition requires its dependency.")


@dataclass(frozen=True)
class Adapter:
    standard_id: str
    entries: tuple[MappingEntry, ...]
    core_paths: tuple[str, ...]
    references: tuple[str, ...]

    def __post_init__(self):
        entries = tuple(sorted(self.entries, key=lambda e: e.standard_path))
        paths = {entry.standard_path for entry in entries}
        if len(paths) != len(entries):
            raise ValueError("Duplicate standard concept path.")
        if any(e.standard_id != self.standard_id for e in entries):
            raise ValueError("Adapter and mapping versions differ.")
        core = tuple(sorted(set(self.core_paths)))
        if not core or not set(core) <= paths:
            raise ValueError("Declared core concepts must all have mappings.")
        object.__setattr__(self, "entries", entries)
        object.__setattr__(self, "core_paths", core)
        object.__setattr__(self, "references", tuple(self.references))


@dataclass(frozen=True)
class Assessment:
    entry: MappingEntry
    disposition: Disposition
    reason: str
    derived_value: object = None

    def __post_init__(self):
        object.__setattr__(self, "disposition", Disposition(self.disposition))
        _immutable(self.derived_value)


@dataclass(frozen=True)
class ReadinessReport:
    standard_id: str
    assessments: tuple[Assessment, ...]
    blockers: tuple[str, ...]
    unknown_facts: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "assessments", tuple(self.assessments))
        object.__setattr__(self, "blockers", tuple(self.blockers))
        object.__setattr__(self, "unknown_facts", tuple(self.unknown_facts))

    def paths(self, disposition):
        return tuple(
            a.entry.standard_path
            for a in self.assessments
            if a.disposition == Disposition(disposition)
        )

    @property
    def dependencies(self):
        return tuple(
            sorted({d for a in self.assessments for d in a.entry.dependencies})
        )
