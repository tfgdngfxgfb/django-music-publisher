"""RDR-N 1.5 concept coverage, intentionally below message-conformance scope."""

from interoperability.contracts import (
    Adapter,
    Dependency,
    Direction,
    Disposition,
    MappingEntry,
)

STANDARD_ID = "ddex:rdr-n:1.5"


def entry(path, concepts, disposition, notes, *, transform=None, phase7=False):
    return MappingEntry(
        STANDARD_ID,
        path,
        concepts,
        Direction.EXPORT if transform else Direction.BOTH,
        disposition,
        notes,
        transform,
        (Dependency.PHASE_7,) if phase7 else (),
    )


ENTRIES = (
    entry(
        "SoundRecording/SoundRecordingId/ISRC",
        ("recording.isrc",),
        Disposition.DIRECT,
        "Use the validated normalized ExternalIdentifier, not UUID.",
    ),
    entry(
        "SoundRecording/SoundRecordingId/ProprietaryId",
        ("recording.external_id", "recording.external_namespace"),
        Disposition.LOSSY,
        "P7 namespace is not automatically a DDEX namespace authority.",
    ),
    entry(
        "SoundRecording/ResourceReference",
        ("recording.id",),
        Disposition.NOT_APPLICABLE,
        "Message-local anchor belongs to a future serializer, not UUID.",
    ),
    entry(
        "SoundRecording/ReferenceTitle/TitleText",
        ("recording.title",),
        Disposition.DIRECT,
        "Canonical title; no destructive normalization.",
    ),
    entry(
        "SoundRecording/ReferenceTitle/SubTitle",
        ("recording.version_designation",),
        Disposition.LOSSY,
        "A free version designation is not necessarily a subtitle.",
    ),
    entry(
        "SoundRecording/Duration",
        ("recording.duration_ms",),
        Disposition.DERIVED,
        "Exact millisecond conversion; no rounding.",
        transform="milliseconds_to_duration",
    ),
    entry(
        "SoundRecording/LanguageOfPerformance",
        ("recording.language",),
        Disposition.LOSSY,
        "Catalogue language and broader language tags need semantic/AVS review;"
        " never substitute MusicLibraryEntry radio language.",
    ),
    entry(
        "SoundRecording/SoundRecordingType",
        ("recording.recording_kind",),
        Disposition.LOSSY,
        "Sound/video chooses a resource family, not a recording subtype.",
    ),
    entry(
        "SoundRecording/PerformingContributor",
        ("recording.contributions",),
        Disposition.PHASE_7,
        "Roles, parties, display identity and external IDs need resolution.",
        phase7=True,
    ),
    entry(
        "SoundRecording/OtherContributor",
        ("recording.contributions",),
        Disposition.PHASE_7,
        "Non-performing identity and role taxonomy need explicit mapping.",
        phase7=True,
    ),
    entry(
        "SoundRecording/InitialProducer",
        (),
        Disposition.MISSING,
        "No canonical commissioning/first-fixation producer relation;"
        " RecordingContribution producer is not InitialProducer.",
    ),
    entry(
        "SoundRecording/RightsController/PartyId",
        ("claim.rights_holder",),
        Disposition.PHASE_7,
        "Party identifiers are missing; holder remains distinct from grantor.",
        phase7=True,
    ),
    entry(
        "SoundRecording/RightsController/RightsControlType",
        ("claim.right_type", "claim.grantor"),
        Disposition.LOSSY,
        "P7 right type does not by itself establish the controller's role.",
    ),
    entry(
        "SoundRecording/RightsController/RightShareUnknown",
        ("claim.share",),
        Disposition.LOSSY,
        "Unknown P7 ownership stays unknown; a future serializer must not"
        " omit share silently where the standard assumes 100 percent.",
    ),
    entry(
        "SoundRecording/RightsController/RightSharePercentage",
        ("claim.share", "claim.right_type"),
        Disposition.LOSSY,
        "Ownership share alone does not specify a delegated/controller share;"
        " NULL is unknown and admin/distribution has no ownership share.",
    ),
    entry(
        "SoundRecording/RightsController/RightsStatement/UseType",
        ("claim.right_type", "claim.grantor", "claim.release_scope"),
        Disposition.LOSSY,
        "Master ownership, administration and distribution are independent;"
        " usage/delegation and Release constraints require a recipient contract.",
    ),
    entry(
        "SoundRecording/RightsController/RightsStatement/Period",
        ("claim.valid_from", "claim.valid_until"),
        Disposition.LOSSY,
        "Inclusive P7 claim validity is not automatically a delegation period;"
        " open bounds stay unknown/open, never fabricated.",
    ),
    entry(
        "SoundRecording/RightsController/RightsStatement/Territory",
        ("claim.territory_mode", "claim.territories"),
        Disposition.LOSSY,
        "WORLD/INCLUDE/EXCLUDE is available, but delegation semantics and"
        " standard territory vocabulary must be reviewed.",
    ),
    entry(
        "SoundRecording/RightsController/provenance (concept)",
        ("claim.source_record", "claim.agreement", "claim.decisions"),
        Disposition.LOSSY,
        "SourceRecord, Agreement and decision history are separate internal"
        " evidence; no promised one-to-one standard payload field.",
    ),
    entry(
        "SoundRecording/OriginalResourceReleaseDate",
        ("release.release_date", "release.release_year"),
        Disposition.MISSING,
        "Earliest known Release is not proof of original publication.",
    ),
    entry(
        "SoundRecording/SoundRecordingDetailsByTerritory/PLine",
        (),
        Disposition.MISSING,
        "No canonical P-line text/year; neither label nor ownership invents it.",
    ),
    entry(
        "SoundRecording/SoundRecordingDetailsByTerritory/DisplayArtistName",
        ("recording.display_credit",),
        Disposition.DIRECT,
        "Loaded display text only; not a resolved contributor identity.",
    ),
    entry(
        "HostSoundCarrier (association)",
        ("release.tracks",),
        Disposition.LOSSY,
        "ReleaseTrack links known carriers; it establishes no carrier-wide right.",
    ),
    entry(
        "HostSoundCarrier/ReleaseId/ICPN",
        ("release.barcode", "release.barcode_scheme"),
        Disposition.DIRECT,
        "Validated UPC/EAN/GTIN representability; leading zeroes retained.",
    ),
    entry(
        "HostSoundCarrier/ReleaseId/CatalogNumber",
        ("release.catalogue_number",),
        Disposition.DIRECT,
        "Catalogue number text; no global identity guarantee.",
    ),
    entry(
        "HostSoundCarrier/ReleaseId/ProprietaryId",
        ("release.external_id", "release.external_namespace"),
        Disposition.LOSSY,
        "Namespace authority requires bilateral mapping.",
    ),
    entry(
        "HostSoundCarrier/Title",
        ("release.title",),
        Disposition.DIRECT,
        "Release title, not the Recording title or a track override.",
    ),
    entry(
        "HostSoundCarrier/DisplayArtistName",
        (),
        Disposition.MISSING,
        "No authoritative Release-level artist credit; do not aggregate tracks.",
    ),
    entry(
        "HostSoundCarrier/LabelName",
        ("release.label_name",),
        Disposition.DIRECT,
        "Label text does not imply a rights holder.",
    ),
    entry(
        "HostSoundCarrier/release date (concept)",
        ("release.release_date", "release.release_year"),
        Disposition.LOSSY,
        "Date/year exist; carrier date is not original resource release date."
        " Year-only data must keep year precision.",
    ),
    entry(
        "HostSoundCarrier/NumberOfSoundRecordingsClaimedInCarrier",
        ("release.tracks",),
        Disposition.MISSING,
        "Track count is not the number claimed; claims need scoped evaluation.",
    ),
    entry(
        "MessageHeader/source identity (concept)",
        ("source.system", "source.locator"),
        Disposition.LOSSY,
        "Provenance is available; sender, message identity and DPID are not inferred.",
    ),
    entry(
        "P7/management membership (no standard equivalent)",
        ("management.recording", "management.release"),
        Disposition.NOT_APPLICABLE,
        "Membership/catalogue authority is not a RightsController declaration.",
    ),
)

ADAPTER = Adapter(
    STANDARD_ID,
    ENTRIES,
    (
        "SoundRecording/SoundRecordingId/ISRC",
        "SoundRecording/ReferenceTitle/TitleText",
        "SoundRecording/Duration",
        "SoundRecording/InitialProducer",
        "SoundRecording/RightsController/PartyId",
        "SoundRecording/RightsController/RightsControlType",
        "SoundRecording/RightsController/RightsStatement/UseType",
        "SoundRecording/SoundRecordingDetailsByTerritory/PLine",
    ),
    (
        "https://rdrn.ddex.net/recording-data-and-rights-notification/",
        "https://service.ddex.net/dd/DD-RDRN-15/dd/rdrn_SoundRecording.html",
        "https://service.ddex.net/dd/DD-RDRN-15/dd/rdrn_RightsController.html",
    ),
)
