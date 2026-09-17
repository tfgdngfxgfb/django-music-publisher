# 6K — standards interoperability foundation

## Preanalysis (before implementation, 2026-09-18)

Baseline: `9078b496d57e1e98d44f52737d71d5304f2acbed` (locked 6I).
Sibling of 6J, not dependent on it. No canonical/schema change is needed for
readiness. The following findings are implementation constraints.

| Inspected domain | Existing support | Readiness / limitation |
|---|---|---|
| `catalogue.Recording` | title, version, kind, millisecond duration, language | KNOWN; language accepts broader tags than RDR-N's performance-language vocabulary; sound/video kind is not recording subtype |
| `catalogue.ExternalIdentifier` | normalized ISRC; UPC/EAN/GTIN; EXTERNAL namespace + value | KNOWN; external IDs are not canonical UUIDs; DDEX namespace authority still needs agreement |
| `Release`, `ReleaseTrack`, `Label` | title, date or year, catalogue number, ordered tracks, label | PARTIAL host-carrier support; release-level artist credit and original-publication identity are not established |
| `RecordingContribution` | credited name, role, Party/ArtistIdentity, provenance | PARTIAL; producer is not a legal InitialProducer; composer is not a Work writer/share |
| `Party`, `ArtistIdentity` | person/organisation, name, party-bound display identity | PHASE_7: global/external party identifiers, alias resolution, complete contributor identity |
| `RightsClaim` | independent ownership/admin/distribution, holder and grantor, ownership share, date/territory/Release scope, status, evidence, provenance, Agreement | KNOWN P7 positions; LOSSY for external controller/delegation semantics, which need explicit bilateral mapping |
| `Agreement`, `RightsDecision` | documents/parties, immutable decisions | KNOWN documentation; not an external declaration, inferred grant or clearance |
| `SourceRecord`, `MetadataAssertion` | immutable raw source, source locator, assertions | KNOWN provenance; no standard message sender/message ID/version semantics; no last-modified-wins |
| `ManagedRecording`, `ManagedRelease` | lifecycle membership / independent catalogue relationship | NOT_APPLICABLE as proof of an external right or controller |
| `music_publisher.Work`, `Writer`, `WriterInWork`, CWR exports | legacy publishing subsystem exists, including ISWC/IPI and shares | PHASE_8 integration boundary: not a canonical Recording→Work contract; do not silently reuse it or duplicate/replace it |

Actual gaps: InitialProducer, P-line, original-release provenance and mature
Party/Work identity. These are deferred standards-independent domain questions,
not reasons to add fields in 6K. No model or migration will be introduced.

Pinned references checked before coding:

- [DDEX RDR-N 1.5 specification](https://rdrn.ddex.net/recording-data-and-rights-notification/)
- [RDR-N 1.5 dictionary, 2021-03-09](https://service.ddex.net/dd/DD-RDRN-15/dd/rdrn_SoundRecording.html)
- [CISAC specification listing CWR19-1070R1](https://members.cisac.org/CisacPortal/consulterDocument.do?id=41804)
- [CISAC CWR19-1070, 2.2 revision 2, official GEMA copy](https://www.gema.de/documents/20121/1372120/CWR_version_2-2_functional-specifications.pdf/017e0f83-09d4-1764-6c51-c5e36557d5d4?download=true&t=1688740733796&version=1.0)

The GEMA copy includes the September 2021 revision notes. The CISAC listing
identifies a February 2022 document revision but its download requires access.
The adapter locks the requested **2.2-rev2 protocol**, not a claim to have
implemented every later editorial correction or society-specific validation.
Mapping paths are conceptual locators, not executable XPath or CWR offsets.
Declared core coverage is this foundation's explicit subset, not certification
that an exported message meets every mandatory profile field.

## Implemented contract

`interoperability` is an ordinary Python package, not an installed Django app.
`contracts.py` defines frozen facts, mappings, adapters and reports;
`registry.py` locks `ddex:rdr-n:1.5` and `cisac:cwr:2.2-rev2`;
`readiness.py` assesses explicitly loaded canonical facts. The two independent
declarative adapters contain the matrices linked below. No dependency on 6J.

| Disposition | Meaning |
|---|---|
| DIRECT | Concept can represent the supplied canonical value without semantic inference; not an export permission or whole-message validation |
| DERIVED | An implemented, exact conversion; currently integer milliseconds to ISO duration, without rounding |
| PHASE_7 | Requires the future Party/identity contract |
| PHASE_8 | Requires future canonical Work/publishing integration; may additionally depend on phase 7 |
| NOT_APPLICABLE | No equivalent canonical-to-standard mapping is appropriate in this foundation |
| MISSING | Canonical concept/value is absent or the supplied direct fact is invalid |
| LOSSY | Related information exists, but meaning, granularity or namespace cannot be mapped safely without review |

IMPORT/EXPORT/BOTH describe representability direction, not implemented
import/export. The runtime API assesses **P7 facts**, not standard messages,
including for entries marked BOTH. Raw incoming messages require later adapters
and the established provenance/rights/authority workflow. No receive-side
validation or automatic canonical apply is implied.

```python
from interoperability.contracts import EntityFacts, Fact, Disposition
from interoperability.readiness import assess_rdrn_readiness

report = assess_rdrn_readiness(EntityFacts((
    Fact("recording.title", "Example"),
    Fact("recording.isrc", "NOABC2600001"),
    Fact("recording.duration_ms", 183501),
)))
report.paths(Disposition.DIRECT)
report.paths(Disposition.MISSING)
report.dependencies
report.blockers
```

Facts are immutable scalars/tuples; lazy ORM instances, dicts and lists as values
are rejected. No ORM read or write occurs. Callers must load one explicit
Recording/Release context and preserve association identity themselves; the API
does not infer cross-recording relationships. Unknown keys are reported.
Missing values never gain defaults. Supplying values cannot promote a declared
LOSSY/phase/MISSING gap to DIRECT. The original declared mapping remains on each
assessment even when a DIRECT field is missing in that particular object.

Reports, registry order and blocker order are deterministic. `blockers` covers
the adapter's declared core subset; other gaps remain visible in assessments.
There is deliberately no `can_export`/`compliant` flag. All mappings are concept
readiness, not an XSD/AVS, fixed-width, bilateral-profile or licensing validator.

The exact duration conversion is the only derived value returned. It is not an
XML or CWR message. Identifier checks reuse existing catalogue validators;
un-normalized/invalid facts are reported instead of quietly rewriting them.

## Boundaries and future work

- [RDR-N matrix](interoperability/rdrn-1.5.md): InitialProducer, P-line and
  original publication need explicit domain facts. Holder/grantor and the three
  right types remain distinct. RDR-N 1.5 uses RightsStatement (not the older
  DelegatedUsageRights structure); its usage/mandate meaning needs explicit
  mapping before an operational rights declaration.
- [CWR matrix](interoperability/cwr-2.2-rev2.md): PHASE_8 includes a deliberate
  integration decision about the existing legacy publishing subsystem, not a
  claim that the repository contains no Work/CWR implementation.
- No canonical field, model, migration, GUI, rights/lifecycle/authority change,
  message store, XML/CWR parser/generator, transport, scheduled job or live
  integration was added. Existing FLAC/OneTagger and 6A–6I contracts are unchanged.
- New standard versions are new Adapter instances/IDs. Future RDR-C/RDR-R/
  RDR-RCC/RIN/CAF/CRD are not implemented.

Gjeldende DDEX Implementation Licence må kontrolleres og oppfylles før
produksjonsmessig DDEX go-live. See the
[DDEX implementation guidance](https://kb.ddex.net/implementing-each-standard/recording-data-and-rights-standards-%28rdr%29/recording-data-and-rights-notification-%28rdr-n%29/).

## Verification

`interoperability.test_readiness` uses synthetic canonical facts and Django
SimpleTestCase, which prohibits database queries. It tests both matrices,
immutability, missing/invalid data, exact conversion, unknown facts, deterministic
reports, version locking, extensibility, duplicate paths/IDs and explicit core
coverage. Conservative tests prohibit producer/ownership/Work-share inference.
Final full-discovery/CI counts and exact commits are reported with delivery.
