# Mudi – import readiness

Status: **UNKNOWN / SAMPLE_REQUIRED** for konkret eksportformat.

Katalogmetadata, utgivelsesstruktur, ISRC, artistcredits, mastereierskap, administrasjon/distribusjon, avtaler, provenance og eksterne ID-er er vurdert som separate konsepter. Ingen konkret eksportkolonne er kjent.

## Matrix

Canonical kapasitet er vurdert mot låst 6I. UNKNOWN i kildekolonnen betyr at vi ikke har dokumentasjon på om/hvordan kilden uttrykker konseptet; det er ikke en bekreftet source mapping.

| Område | Canonical readiness | Kildesemantikk | Vurdering |
| --- | --- | --- | --- |
| Recording metadata | READY | UNKNOWN | Title, duration, language and kind; no write authority. |
| Release metadata | READY | UNKNOWN | Title, type, date/year, catalogue number; year is not date. |
| ReleaseTrack | PARTIAL | UNKNOWN | Match Recording/Release and preserve sequence/medium/side. |
| ISRC | READY | UNKNOWN | Existing format normalization and uniqueness; match required. |
| External IDs | READY | UNKNOWN | EXTERNAL + namespace; never canonical UUID. |
| Artist/contributors | PHASE_7 | UNKNOWN | Preserve credits; resolve Party/ArtistIdentity explicitly. |
| Labels | MANUAL_REVIEW | UNKNOWN | Match label; label association is not ownership. |
| Ownership | WORKFLOW_REQUIRED | UNKNOWN | Proposed UNVERIFIED position; share may be unknown. |
| Administration | WORKFLOW_REQUIRED | UNKNOWN | Separate position; no ownership share or inferred exclusivity. |
| Distribution | WORKFLOW_REQUIRED | UNKNOWN | Listing does not establish ownership or distribution clearance. |
| Agreements | WORKFLOW_REQUIRED | UNKNOWN | Documentation, not automatic claim scope/verification. |
| Provenance | READY | UNKNOWN | SourceSystem/ImportBatch/immutable SourceRecord before apply. |
| ManagedRecording | WORKFLOW_REQUIRED | UNKNOWN | Explicit P7 onboarding; no source-driven membership. |
| ManagedRelease | WORKFLOW_REQUIRED | UNKNOWN | Explicit catalogue relationship, independent of track rights. |
| Party dependency | PHASE_7 | UNKNOWN | Aliases, contributor IDs and identity deduplication deferred. |
| Work/publishing | PHASE_8 | UNKNOWN | Recording credits do not establish publishing shares. |
| Source revision behavior | GAP | UNKNOWN | Same source ID + changed payload cannot overwrite SourceRecord. |

## KNOWN / UNKNOWN / SAMPLE_REQUIRED / GAP

- KNOWN: de eksisterende P7-konseptene og valideringene ovenfor.
- UNKNOWN: konkrete felt, typer, kodeverk, ID-stabilitet, nullverdier og upstream-revisjoner.
- SAMPLE_REQUIRED: faktisk eksport med versjon og feltforklaring, med dokumentert representativitet. Inntil dette foreligger finnes ingen innebygd MappingPlan.
- GAP: samme source + external ID med endret payload kan ikke overskrive immutable SourceRecord; avklar revisjonskontrakt før apply.
- PHASE_7: identiteter, aliaser og eksterne person-/contributor-ID-er.
- PHASE_8: canonical Work/publishing-integrasjon; eksisterende legacy publishing-kode er ikke i seg selv en Recording→Work-kontrakt.

En eksplisitt, versjonstilpasset MappingPlan kan senere beskrive kontrollerte kildefelt. Readiness bruker eksisterende ISRC/strekkode-normalisering og namespaced EXTERNAL-ID, men utfører ingen matching-queries, canonical writes, nettverk eller importer.

Rights blir foreslåtte UNVERIFIED-posisjoner via WORKFLOW_REQUIRED. Forvaltning krever eksplisitt workflow. Flere dokumenter er ikke additive claims. Se [felles kontrakt](../phase-6j-import-readiness.md) for provenance, authority, matching, idempotency og preview/apply-grense.
