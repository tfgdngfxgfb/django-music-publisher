# ddex:rdr-n:1.5 — readiness matrix

Concept locators only; no executable paths, serializer or full conformance claim.
Core means the declared P7 foundation subset, not all normative mandatory fields.

| Standard concept | Canonical concept | Direction | Disposition | Dependency / note |
|---|---|---|---|---|
| HostSoundCarrier (association) | release.tracks | BOTH | LOSSY | ReleaseTrack links known carriers; it establishes no carrier-wide right. |
| HostSoundCarrier/DisplayArtistName | — | BOTH | MISSING | No authoritative Release-level artist credit; do not aggregate tracks. |
| HostSoundCarrier/LabelName | release.label_name | BOTH | DIRECT | Label text does not imply a rights holder. |
| HostSoundCarrier/NumberOfSoundRecordingsClaimedInCarrier | release.tracks | BOTH | MISSING | Track count is not the number claimed; claims need scoped evaluation. |
| HostSoundCarrier/ReleaseId/CatalogNumber | release.catalogue_number | BOTH | DIRECT | Catalogue number text; no global identity guarantee. |
| HostSoundCarrier/ReleaseId/ICPN | release.barcode, release.barcode_scheme | BOTH | DIRECT | Validated UPC/EAN/GTIN representability; leading zeroes retained. |
| HostSoundCarrier/ReleaseId/ProprietaryId | release.external_id, release.external_namespace | BOTH | LOSSY | Namespace authority requires bilateral mapping. |
| HostSoundCarrier/Title | release.title | BOTH | DIRECT | Release title, not the Recording title or a track override. |
| HostSoundCarrier/release date (concept) | release.release_date, release.release_year | BOTH | LOSSY | Date/year exist; carrier date is not original resource release date. Year-only data must keep year precision. |
| MessageHeader/source identity (concept) | source.system, source.locator | BOTH | LOSSY | Provenance is available; sender, message identity and DPID are not inferred. |
| P7/management membership (no standard equivalent) | management.recording, management.release | BOTH | NOT_APPLICABLE | Membership/catalogue authority is not a RightsController declaration. |
| SoundRecording/Duration **core** | recording.duration_ms | EXPORT | DERIVED | Exact millisecond conversion; no rounding. |
| SoundRecording/InitialProducer **core** | — | BOTH | MISSING | No canonical commissioning/first-fixation producer relation; RecordingContribution producer is not InitialProducer. |
| SoundRecording/LanguageOfPerformance | recording.language | BOTH | LOSSY | Catalogue language and broader language tags need semantic/AVS review; never substitute MusicLibraryEntry radio language. |
| SoundRecording/OriginalResourceReleaseDate | release.release_date, release.release_year | BOTH | MISSING | Earliest known Release is not proof of original publication. |
| SoundRecording/OtherContributor | recording.contributions | BOTH | PHASE_7 | PHASE_7; Non-performing identity and role taxonomy need explicit mapping. |
| SoundRecording/PerformingContributor | recording.contributions | BOTH | PHASE_7 | PHASE_7; Roles, parties, display identity and external IDs need resolution. |
| SoundRecording/ReferenceTitle/SubTitle | recording.version_designation | BOTH | LOSSY | A free version designation is not necessarily a subtitle. |
| SoundRecording/ReferenceTitle/TitleText **core** | recording.title | BOTH | DIRECT | Canonical title; no destructive normalization. |
| SoundRecording/ResourceReference | recording.id | BOTH | NOT_APPLICABLE | Message-local anchor belongs to a future serializer, not UUID. |
| SoundRecording/RightsController/PartyId **core** | claim.rights_holder | BOTH | PHASE_7 | PHASE_7; Party identifiers are missing; holder remains distinct from grantor. |
| SoundRecording/RightsController/RightSharePercentage | claim.share, claim.right_type | BOTH | LOSSY | Ownership share alone does not specify a delegated/controller share; NULL is unknown and admin/distribution has no ownership share. |
| SoundRecording/RightsController/RightShareUnknown | claim.share | BOTH | LOSSY | Unknown P7 ownership stays unknown; a future serializer must not omit share silently where the standard assumes 100 percent. |
| SoundRecording/RightsController/RightsControlType **core** | claim.right_type, claim.grantor | BOTH | LOSSY | P7 right type does not by itself establish the controller's role. |
| SoundRecording/RightsController/RightsStatement/Period | claim.valid_from, claim.valid_until | BOTH | LOSSY | Inclusive P7 claim validity is not automatically a delegation period; open bounds stay unknown/open, never fabricated. |
| SoundRecording/RightsController/RightsStatement/Territory | claim.territory_mode, claim.territories | BOTH | LOSSY | WORLD/INCLUDE/EXCLUDE is available, but delegation semantics and standard territory vocabulary must be reviewed. |
| SoundRecording/RightsController/RightsStatement/UseType **core** | claim.right_type, claim.grantor, claim.release_scope | BOTH | LOSSY | Master ownership, administration and distribution are independent; usage/delegation and Release constraints require a recipient contract. |
| SoundRecording/RightsController/provenance (concept) | claim.source_record, claim.agreement, claim.decisions | BOTH | LOSSY | SourceRecord, Agreement and decision history are separate internal evidence; no promised one-to-one standard payload field. |
| SoundRecording/SoundRecordingDetailsByTerritory/DisplayArtistName | recording.display_credit | BOTH | DIRECT | Loaded display text only; not a resolved contributor identity. |
| SoundRecording/SoundRecordingDetailsByTerritory/PLine **core** | — | BOTH | MISSING | No canonical P-line text/year; neither label nor ownership invents it. |
| SoundRecording/SoundRecordingId/ISRC **core** | recording.isrc | BOTH | DIRECT | Use the validated normalized ExternalIdentifier, not UUID. |
| SoundRecording/SoundRecordingId/ProprietaryId | recording.external_id, recording.external_namespace | BOTH | LOSSY | P7 namespace is not automatically a DDEX namespace authority. |
| SoundRecording/SoundRecordingType | recording.recording_kind | BOTH | LOSSY | Sound/video chooses a resource family, not a recording subtype. |

## References

- [Pinned source 1](https://rdrn.ddex.net/recording-data-and-rights-notification/)
- [Pinned source 2](https://service.ddex.net/dd/DD-RDRN-15/dd/rdrn_SoundRecording.html)
- [Pinned source 3](https://service.ddex.net/dd/DD-RDRN-15/dd/rdrn_RightsController.html)

See [phase contract](../phase-6k-interoperability-foundation.md) for source revision limits,
disposition definitions, input contract, licensing, testing and deferred gaps.
