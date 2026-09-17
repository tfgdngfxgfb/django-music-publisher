"""Known P7 capabilities, explicitly unknown upstream schemas."""

from import_readiness.contracts import MatrixEntry, Readiness

COMMON_MATRIX = (
    MatrixEntry(
        "Recording metadata",
        Readiness.READY,
        "Title, duration, language and kind; no write authority.",
    ),
    MatrixEntry(
        "Release metadata",
        Readiness.READY,
        "Title, type, date/year, catalogue number; year is not date.",
    ),
    MatrixEntry(
        "ReleaseTrack",
        Readiness.PARTIAL,
        "Match Recording/Release and preserve sequence/medium/side.",
    ),
    MatrixEntry(
        "ISRC",
        Readiness.READY,
        "Existing format normalization and uniqueness; match required.",
    ),
    MatrixEntry(
        "External IDs",
        Readiness.READY,
        "EXTERNAL + namespace; never canonical UUID.",
    ),
    MatrixEntry(
        "Artist/contributors",
        Readiness.PHASE_7,
        "Preserve credits; resolve Party/ArtistIdentity explicitly.",
    ),
    MatrixEntry(
        "Labels",
        Readiness.MANUAL_REVIEW,
        "Match label; label association is not ownership.",
    ),
    MatrixEntry(
        "Ownership",
        Readiness.WORKFLOW_REQUIRED,
        "Proposed UNVERIFIED position; share may be unknown.",
    ),
    MatrixEntry(
        "Administration",
        Readiness.WORKFLOW_REQUIRED,
        "Separate position; no ownership share or inferred exclusivity.",
    ),
    MatrixEntry(
        "Distribution",
        Readiness.WORKFLOW_REQUIRED,
        "Listing does not establish ownership or distribution clearance.",
    ),
    MatrixEntry(
        "Agreements",
        Readiness.WORKFLOW_REQUIRED,
        "Documentation, not automatic claim scope/verification.",
    ),
    MatrixEntry(
        "Provenance",
        Readiness.READY,
        "SourceSystem/ImportBatch/immutable SourceRecord before apply.",
    ),
    MatrixEntry(
        "ManagedRecording",
        Readiness.WORKFLOW_REQUIRED,
        "Explicit P7 onboarding; no source-driven membership.",
    ),
    MatrixEntry(
        "ManagedRelease",
        Readiness.WORKFLOW_REQUIRED,
        "Explicit catalogue relationship, independent of track rights.",
    ),
    MatrixEntry(
        "Party dependency",
        Readiness.PHASE_7,
        "Aliases, contributor IDs and identity deduplication deferred.",
    ),
    MatrixEntry(
        "Work/publishing",
        Readiness.PHASE_8,
        "Recording credits do not establish publishing shares.",
    ),
    MatrixEntry(
        "Source revision behavior",
        Readiness.GAP,
        "Same source ID + changed payload cannot overwrite SourceRecord.",
    ),
)


def all_profiles():
    from import_readiness.profiles.klango import PROFILE as klango
    from import_readiness.profiles.lumi import PROFILE as lumi
    from import_readiness.profiles.mudi import PROFILE as mudi
    from import_readiness.profiles.orchard import PROFILE as orchard

    return (mudi, orchard, klango, lumi)


def get_profile(source_name):
    return next(
        (p for p in all_profiles() if p.source_name == source_name), None
    )
