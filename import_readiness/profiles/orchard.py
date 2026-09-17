from import_readiness.contracts import SourceProfile
from import_readiness.profiles import COMMON_MATRIX

PROFILE = SourceProfile(
    "orchard",
    "The Orchard",
    (
        "Distribution/metadata observations; presence at Orchard is not ownership.",
        "The repository's test year payload is synthetic, not an Orchard schema.",
    ),
    COMMON_MATRIX,
)
