# cisac:cwr:2.2-rev2 — readiness matrix

Concept locators only; no executable paths, serializer or full conformance claim.
Core means the declared P7 foundation subset, not all normative mandatory fields.

| Standard concept | Canonical concept | Direction | Disposition | Dependency / note |
|---|---|---|---|---|
| AGR/Agreement | work.agreements | BOTH | PHASE_8 | PHASE_8; Master Agreement documentation is not a publishing mandate. |
| ALT/Alternate Title | work.alternate_titles | BOTH | PHASE_8 | PHASE_8; Work titles, not Recording version labels. |
| NWR/ISWC **core** | work.iswc | BOTH | PHASE_8 | PHASE_8; Work identifier; ISRC is not interchangeable. |
| NWR/Submitter Work Number **core** | work.id | BOTH | PHASE_8 | PHASE_8; Future canonical Work ID, not Recording UUID. |
| NWR/Work Title **core** | work.title | BOTH | PHASE_8 | PHASE_8; Canonical Work integration pending; Recording.title is not Work title. |
| PER/Performing Artist | work.performers | BOTH | PHASE_8 | PHASE_7, PHASE_8; Identity and Work context required; does not create writer rights. |
| PWR/Publisher For Writer | work.publisher_writer_relations | BOTH | PHASE_8 | PHASE_7, PHASE_8; Explicit authorship/publishing chain; no inference from a Label. |
| REC/Recording Detail | work.recording_links | BOTH | PHASE_8 | PHASE_8; Recording data exist, but canonical Work→Recording association is pending. |
| SPT+OPT/Collection Shares and Territory **core** | work.publisher_collection_rights | BOTH | PHASE_8 | PHASE_8; Publishing mandate scope and collection shares await phase 8. |
| SPU+OPU/Ownership Shares **core** | work.publisher_shares | BOTH | PHASE_8 | PHASE_8; Publishing shares are independent of master ownership. |
| SPU+OPU/Publisher **core** | work.publishers | BOTH | PHASE_8 | PHASE_7, PHASE_8; Publisher relationships require Work domain and Party identity. |
| SWR+OWR/Ownership Shares **core** | work.writer_shares | BOTH | PHASE_8 | PHASE_8; Separate PR/MR/SR Work shares; never RightsClaim master share. |
| SWR+OWR/Writer **core** | work.writers | BOTH | PHASE_8 | PHASE_7, PHASE_8; Explicit Work authorship plus Party resolution required. |
| SWR+OWR/Writer Designation Code | work.writer_roles | BOTH | PHASE_8 | PHASE_7, PHASE_8; RecordingContribution composer/lyricist/arranger is insufficient. |
| SWR+OWR/Writer IPI identifiers | work.writer_identifiers | BOTH | PHASE_8 | PHASE_7, PHASE_8; No external IPI on current canonical Party; legacy Writer is separate. |
| SWT+OWT/Collection Shares and Territory | work.writer_collection_rights | BOTH | PHASE_8 | PHASE_8; Publishing collection rights are not master territory rights. |
| TER/Territory in Agreement | work.agreement_territories | BOTH | PHASE_8 | PHASE_8; Work agreement territories require phase 8, not claim copying. |
| XRF/Work ID Cross Reference | work.external_identifiers | BOTH | PHASE_8 | PHASE_8; Namespaced Work identifiers; current ExternalIdentifier targets only Recording or Release. |

## References

- [Pinned source 1](https://members.cisac.org/CisacPortal/consulterDocument.do?id=41804)
- [Pinned source 2](https://www.gema.de/documents/20121/1372120/CWR_version_2-2_functional-specifications.pdf/017e0f83-09d4-1764-6c51-c5e36557d5d4)

See [phase contract](../phase-6k-interoperability-foundation.md) for source revision limits,
disposition definitions, input contract, licensing, testing and deferred gaps.
