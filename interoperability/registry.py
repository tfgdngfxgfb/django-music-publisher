"""A small immutable registry. New versions get new IDs and adapters."""

from dataclasses import dataclass

from interoperability.contracts import Adapter
from interoperability.standards.cwr_2_2_rev2 import ADAPTER as CWR
from interoperability.standards.rdrn_1_5 import ADAPTER as RDRN


@dataclass(frozen=True)
class Registry:
    adapters: tuple[Adapter, ...]

    def __post_init__(self):
        adapters = tuple(sorted(self.adapters, key=lambda a: a.standard_id))
        if len({a.standard_id for a in adapters}) != len(adapters):
            raise ValueError("Duplicate versioned standard ID.")
        object.__setattr__(self, "adapters", adapters)

    @property
    def standard_ids(self):
        return tuple(a.standard_id for a in self.adapters)

    def get(self, standard_id):
        for adapter in self.adapters:
            if adapter.standard_id == standard_id:
                return adapter
        raise ValueError(f"Unknown pinned standard: {standard_id}")


REGISTRY = Registry((RDRN, CWR))
