from import_readiness.contracts import SourceProfile
from import_readiness.profiles import COMMON_MATRIX

PROFILE = SourceProfile(
    "klango",
    "Klango",
    (
        "Source may be wrong or weaker than canonical data.",
        "Preserve raw values; mismatches become assertions/manual review, never overwrite.",
    ),
    COMMON_MATRIX,
)
