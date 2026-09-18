"""Boundary for future authorized metadata providers; no network adapter yet.

Provider results must be source-backed proposals reviewed against the physical
release. This module deliberately cannot save canonical catalogue objects.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MetadataQuery:
    label: str = ""
    catalogue_number: str = ""
    title: str = ""
    artist: str = ""


@dataclass(frozen=True)
class MetadataProposal:
    provider: str
    source_identifier: str
    source_reference: str
    # Original spelling and credited_as belong in source assertions.
    assertions: tuple[tuple[str, str], ...]


class MetadataProvider(Protocol):
    def search(self, query: MetadataQuery) -> tuple[MetadataProposal, ...]: ...


def provider_availability():
    return tuple(
        {
            "name": name,
            "available": False,
            "reason": "Ikke konfigurert – krever godkjent API, tilgang og kildeformat.",
        }
        for name in ("NCB", "TONO", "Gramo")
    )
