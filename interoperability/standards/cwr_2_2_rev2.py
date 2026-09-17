"""Work/publishing readiness, not a wrapper around the legacy CWR exporter."""

from interoperability.contracts import (
    Adapter,
    Dependency,
    Direction,
    Disposition,
    MappingEntry,
)

STANDARD_ID = "cisac:cwr:2.2-rev2"


def entry(path, concepts, notes, *, identity=False):
    return MappingEntry(
        STANDARD_ID,
        path,
        concepts,
        Direction.BOTH,
        Disposition.PHASE_8,
        notes,
        dependencies=(
            (Dependency.PHASE_7, Dependency.PHASE_8)
            if identity
            else (Dependency.PHASE_8,)
        ),
    )


ENTRIES = (
    entry(
        "NWR/Work Title",
        ("work.title",),
        "Canonical Work integration pending; Recording.title is not Work title.",
    ),
    entry(
        "NWR/Submitter Work Number",
        ("work.id",),
        "Future canonical Work ID, not Recording UUID.",
    ),
    entry(
        "NWR/ISWC",
        ("work.iswc",),
        "Work identifier; ISRC is not interchangeable.",
    ),
    entry(
        "ALT/Alternate Title",
        ("work.alternate_titles",),
        "Work titles, not Recording version labels.",
    ),
    entry(
        "SWR+OWR/Writer",
        ("work.writers",),
        "Explicit Work authorship plus Party resolution required.",
        identity=True,
    ),
    entry(
        "SWR+OWR/Writer IPI identifiers",
        ("work.writer_identifiers",),
        "No external IPI on current canonical Party; legacy Writer is separate.",
        identity=True,
    ),
    entry(
        "SWR+OWR/Writer Designation Code",
        ("work.writer_roles",),
        "RecordingContribution composer/lyricist/arranger is insufficient.",
        identity=True,
    ),
    entry(
        "SWR+OWR/Ownership Shares",
        ("work.writer_shares",),
        "Separate PR/MR/SR Work shares; never RightsClaim master share.",
    ),
    entry(
        "SWT+OWT/Collection Shares and Territory",
        ("work.writer_collection_rights",),
        "Publishing collection rights are not master territory rights.",
    ),
    entry(
        "SPU+OPU/Publisher",
        ("work.publishers",),
        "Publisher relationships require Work domain and Party identity.",
        identity=True,
    ),
    entry(
        "SPU+OPU/Ownership Shares",
        ("work.publisher_shares",),
        "Publishing shares are independent of master ownership.",
    ),
    entry(
        "SPT+OPT/Collection Shares and Territory",
        ("work.publisher_collection_rights",),
        "Publishing mandate scope and collection shares await phase 8.",
    ),
    entry(
        "PWR/Publisher For Writer",
        ("work.publisher_writer_relations",),
        "Explicit authorship/publishing chain; no inference from a Label.",
        identity=True,
    ),
    entry(
        "AGR/Agreement",
        ("work.agreements",),
        "Master Agreement documentation is not a publishing mandate.",
    ),
    entry(
        "TER/Territory in Agreement",
        ("work.agreement_territories",),
        "Work agreement territories require phase 8, not claim copying.",
    ),
    entry(
        "REC/Recording Detail",
        ("work.recording_links",),
        "Recording data exist, but canonical Work→Recording association is pending.",
    ),
    entry(
        "PER/Performing Artist",
        ("work.performers",),
        "Identity and Work context required; does not create writer rights.",
        identity=True,
    ),
    entry(
        "XRF/Work ID Cross Reference",
        ("work.external_identifiers",),
        "Namespaced Work identifiers; current ExternalIdentifier targets only"
        " Recording or Release.",
    ),
)

ADAPTER = Adapter(
    STANDARD_ID,
    ENTRIES,
    (
        "NWR/Work Title",
        "NWR/Submitter Work Number",
        "NWR/ISWC",
        "SWR+OWR/Writer",
        "SWR+OWR/Ownership Shares",
        "SPU+OPU/Publisher",
        "SPU+OPU/Ownership Shares",
        "SPT+OPT/Collection Shares and Territory",
    ),
    (
        "https://members.cisac.org/CisacPortal/consulterDocument.do?id=41804",
        "https://www.gema.de/documents/20121/1372120/"
        "CWR_version_2-2_functional-specifications.pdf/"
        "017e0f83-09d4-1764-6c51-c5e36557d5d4",
    ),
)
