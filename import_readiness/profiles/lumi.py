from import_readiness.contracts import SourceProfile
from import_readiness.profiles import COMMON_MATRIX

PROFILE = SourceProfile(
    "lumi",
    "LU-MI",
    (
        "Sparse historical observations are valid source evidence.",
        "Missing ISRC/date/artist identity/rights/digital file remain missing; never synthesize.",
    ),
    COMMON_MATRIX,
)
