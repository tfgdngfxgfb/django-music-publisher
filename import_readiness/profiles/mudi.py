from import_readiness.contracts import SourceProfile
from import_readiness.profiles import COMMON_MATRIX

PROFILE = SourceProfile(
    "mudi",
    "Mudi",
    (
        "No verified source schema/sample in the baseline.",
        "Catalogue, release structure, credits, rights and agreements require explicit field interpretation.",
    ),
    COMMON_MATRIX,
)
