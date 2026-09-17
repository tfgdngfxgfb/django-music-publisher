# Fase 6J – import readiness

Baseline: `9078b496d57e1e98d44f52737d71d5304f2acbed` (låst 6I).
Søskenfase til 6K; ingen avhengighet av interoperabilitetspakken.

## Forhåndsanalyse (skrevet før implementasjon)

Følgende er kontrollert i baselinens kode, ikke utledet fra mockuper:

| Område | Status | Eksisterende støtte / grense |
| --- | --- | --- |
| SourceSystem, ImportBatch, SourceRecord | KNOWN | Navngitt kilde, batch, lokator og immutable JSON. Ikke-tom ekstern post-ID er unik per kilde. |
| MetadataAssertion | KNOWN/PARTIAL | Objekt-/feltpåstand med originalverdi, normalisert verdi og beslutningshistorikk. Ikke en generell RightsClaim- eller ManagedRelease-assertion-modell. |
| Recording, Release, ReleaseTrack | KNOWN | Separate objekter, tittel, varighet, språk, krediteringer, utgivelsesår/-dato og sporstruktur. År er ikke full dato. |
| ExternalIdentifier | KNOWN | ISRC på Recording; UPC/EAN/GTIN på Release; EXTERNAL med namespace på begge. Egen canonical UUID beholdes. |
| Party, ArtistIdentity, RecordingContribution | PARTIAL/PHASE_7 | Party-navn/type, artistnavn og krediteringer finnes. Alias-/global identitetsmatching og eksterne person-ID-er er ikke et modent identitetslag. |
| RightsClaim, Agreement | KNOWN | Tre separate masterrettighetstyper, holder/grantor, andel, territorier, periode, release_scope, kilde og avtale. Avtale er dokumentasjon, ikke automatisk juridisk scope. |
| ManagedRecording / ManagedRelease | KNOWN | Separate medlemskap med eksplisitte workflows; ekstern katalogtilstedeværelse er ikke medlemskap. |
| 6B authority | KNOWN | Recording-/Release-vern vurderes separat; historisk medlemskap og via-release beskytter også. Radiometadata følger eksisterende FLAC-regler. |
| 6H/6I legal identity | KNOWN | `rights.positions.legal_position_identity()` krever ferdiglastede territorier. Kilde, dokumenter og evidens er ikke nye juridiske posisjoner. |
| 6I follow-up | KNOWN | Rene evalueringer av lastede fakta; ikke en importmotor. Import trenger ikke opprette oppgaver eller nye signalstatuser. |
| FLAC-ingest | KNOWN | Preview, kildesnapshot, gjenvalidering ved apply og objektvis katalogvern finnes. Batch-/item-avledede FLAC-kilde-ID-er er ikke en dokumentert upstream-revisjonsmodell. |
| Dublettmatching | PARTIAL | `catalogue.services.find_recording_candidates` finner kandidater fra ISRC/tittel/artist/varighet. Kandidat er ikke merge-autorisasjon. |
| Work/publishing | PHASE_8 | RecordingContribution composer/lyricist er ikke kanonisk Work-/publishing-rettighet. |
| Mudi/Orchard/Klango/LU-MI eksportformat | UNKNOWN / SAMPLE_REQUIRED | Reposøk fant omtale og syntetiske Orchard-årstall/Mudi-filbaner, men ingen verifisert eksportkontrakt. Ingen konkrete kolonner kan låses. |
| Samme upstream-ID med revidert payload | GAP | SourceRecord er immutable og ID-en unik. En fremtidig revisjons-/identitetskontrakt må avklares; ingen migrasjon i 6J. |

Inspeksjonsgrunnlag: `provenance/models.py`, `catalogue/models.py`,
`catalogue/validators.py`, `catalogue/services.py`, `catalogue/authority.py`,
`parties/models.py`, `rights/models.py`, `managed_music/models.py`,
`rights/positions.py`, `rights/followup.py`, `flac_ingest/adapter.py`,
`flac_ingest/services.py`, provenance-tester og `create_phase2_smoke_data`.

Ingen av de identifiserte gapene krever schema-endring for å levere readiness.
De krever senere kontrakts-/domeneavklaring før produksjonsimport.

## Levert grense og API

`import_readiness` er en vanlig Python-pakke, ikke en Django-app. Ingen
modeller, migrasjoner, registrerte jobs, credentials, nettverkskall eller GUI.
Kontraktene er frozen dataclasses/enums. JSON lagres som en immutable
tekst-snapshot; `decode()` gir en ny kopi. Unicode, null, falsk og 0 bevares.
Dette er semantisk JSON-bevaring, ikke bytebevaring av en original CSV/XML-fil.
Ikke-JSON-verdier avvises i stedet for å konverteres med `str()`.

`assess_import_readiness(source, plan=None, protected_concepts=(),
canonical_values=(), existing_sources=())` mottar bare ferdiglastede fakta.
Den gjør ingen ORM-oppslag og skriver ingenting. Et resultat har `source`,
`mappings`, `reimport`, `blockers`, `warnings`, `manual_review`, `unsupported`
og `unknowns`. Resultatet er en vurdering, aldri apply-autorisasjon.

De fire profilene beskriver **kanonisk kapasitet og ukjent kildesemantikk
hver for seg**. Ingen profil inneholder konkrete eksportkolonnenavn eller en
innebygd parser. Uten `MappingPlan` er alle kildefelt `UNKNOWN_SOURCE` og
`SAMPLE_REQUIRED` blokkerer ferdig mapping. Selv et felt som heter «isrc»
tolkes ikke automatisk.

En fremtidig adapter eller eksplisitt analyse kan levere `MappingPlan` med
eksakt source identity/version, dokumentert schema-/sample-reference og
entydige JSON-path → P7-concept-mappings. Kontrakten forutsetter at referansen
er faglig kontrollert; den laster eller verifiserer ikke eksterne dokumenter.
Overlappende paths, dupliserte målkonsepter og feil versjon avvises. Felter som
ikke er dekket blir fortsatt ukjente. Manglende felt fylles aldri ut.
Alle fixtures i testene er uttrykkelig syntetiske.

| Disposition | Betydning |
| --- | --- |
| DIRECT | Verdien kan uttrykkes som det eksplisitt valgte P7-konseptet. Ingen tillatelse til canonical write. |
| NORMALIZED | En enkel, eksplisitt normalisering er gjort, som trimming av tittel. Original beholdes. |
| MATCH_REQUIRED | Identifikator, objekt eller struktur må matches før eventuell apply. |
| ASSERTION_ONLY | Beskyttede eller avvikende metadata beholdes som kildepåstand for vurdering. |
| WORKFLOW_REQUIRED | Eksisterende rights-/management-/dokumentasjonsworkflow må behandle forslaget. |
| MANUAL_REVIEW | Manglende/ugyldig/ikke-avklart informasjon kan ikke brukes ukritisk. |
| UNSUPPORTED | Konseptet ligger utenfor dagens canonical domain, eksempelvis Work/publishing. |
| UNKNOWN_SOURCE | Kildens konkrete feltsemantikk er ikke fastslått. |

## Authority og normalisering

Caller henter 6Bs authority én gang og sender inn de beskyttede konseptene.
Dette inkluderer Recording-vern fra ManagedRecording, via ManagedRelease eller
eksisterende lokalt eierskapsvern, samt uavhengig Release-/ReleaseTrack-vern.
6J etablerer ingen egen authority-resolver og utleder ikke Release-vern fra
Recording-vern. Andre DB-authoritative felter må også oppgis eksplisitt.

Beskyttede katalogforslag blir `ASSERTION_ONLY`. Oppgitt canonical verdi som
avviker gir også assertion, selv uten eksplisitt vern. Dette gjelder særlig
svakere Klango-data. Det eksisterende skillet mot FLAC/OneTagger-radiometadata
endres ikke. Ingen last-modified-wins eller DB→FLAC-sync.

ISRC og UPC/EAN/GTIN bruker eksisterende `catalogue.validators`, inkludert
kontrollsiffer for strekkoder. EXTERNAL krever namespace, bevarer case og blir
aldri canonical UUID. Utgivelsesår forblir år; det konstrueres ingen 1. januar.
Varighet må være eksplisitt millisekunder før den deklareres som dette konseptet.

## Matching og juridiske posisjoner

`assess_recording_match(incoming, candidate)` klassifiserer ferdiglastede
match-signaler; den utfører ikke queries, velger ikke automatisk en vinner og
merger ikke. Lik gyldig ISRC eller EXTERNAL-ID i samme namespace er STRONG.
Motstridende ID-er i samme scheme/namespace gir CONFLICT/manual review selv om
navnet er likt. Tittel, artistcredit, varighet innen 3 sekunder og allerede
matchet Release-kontekst er kandidat-signaler. Ett slikt signal, også tittel
alene, er INSUFFICIENT. Dette er readiness for kandidatvurdering, ikke en ny
kanonisk duplicate-resolver; eksisterende ORM-candidateservice består.

Rights-data gir alltid `WORKFLOW_REQUIRED`, med foreslått status `unverified`.
Source status «confirmed» er bare raw data. Holder, grantor, rettighetstype,
andel, territorier, datoer, release_scope og Agreement må gjennom eksisterende
6C/6E-validering; 6J inneholder ingen alternativ scope-motor. Agreement-datoer
kopieres ikke til claim-perioder. Orchard-tilstedeværelse og labelinformasjon
beviser verken ownership eller management.

`group_legal_positions(loaded_positions)` gjenbruker
`rights.positions.legal_position_identity()` på ferdiglastede claim-fakta
(eksempelvis 6Is `ClaimFact`). Den returnerer grupper av inputindekser, aldri
summerte andeler eller nye claims. Samme juridiske posisjon med flere kilder
eller forskjellig evidens er én posisjon. Caller velger selv hvilke historiske
og aktuelle posisjoner som skal undersøkes; funksjonen er ikke en ny blocker
eller en status-/scope-engine. En fremtidig import må bruke dokumentasjonsflyt
for ekstra evidens, ikke opprette additive duplikatclaims.

ManagedRecording og ManagedRelease er `WORKFLOW_REQUIRED`, og source status
kopieres aldri til lifecycle. Brukerworkflowens 6E-membership-guard består.
Lavnivå import-/systemservices endres heller ikke.

## Provenance og idempotency

Fremtidig apply skal først bevare kilden gjennom SourceSystem, ImportBatch og
SourceRecord. Kildens source-version og lokator må følge den bevarte
envelope/payload-kontrakten; 6J oppretter ingen SourceRecord. Opprinnelig raw
payload og provenance kan ikke erstattes med senere dokumentasjon.

`assess_reimport(source, existing_sources)` krever at caller laster relevant
eksisterende kildehistorikk. En tom liste betyr bare «ingen oppgitt match»,
ikke at databasen er tom. Sammenligningen bruker kildeidentitet + ikke-tom
external ID og semantisk JSON-fingerprint (objektnøkkelrekkefølge irrelevant,
arrayrekkefølge betydningsfull). Metadata som locator er ikke identitetsbevis.

| Situasjon | Resultat |
| --- | --- |
| Samme kilde, ID og payload | REUSE_EXISTING: gjenbruk original kildepost. |
| Samme kilde/ID, endret payload | UPSTREAM_REVISION_GAP: behold gammel kildepost og stopp for avklaring. |
| Samme payload, annen/manglende ID | DUPLICATE_PAYLOAD_REVIEW: lik payload beviser ikke samme objekt. |
| Samme ID/payload hos annen kilde | NEW_OBSERVATION: kildeidentiteter slås ikke sammen. |
| Ingen treff i oppgitte snapshots | NEW_OBSERVATION: nytt observasjonsforslag, ingen write. |

Revisjonsgapet kan senere kreve en eksplisitt upstream-versjonskontrakt eller
domeneendring. Det løses ikke ved tilfeldig ny ID, overwrite, timestamp eller
ny modell i 6J. Endret source_version krever også kontroll av mappingplan;
uendret payload med samme eksterne ID gir ikke nytt canonical apply automatisk.

## Senere apply og åpne behov

`raw source → parsed proposal → preview → user/system decision → existing
domain workflow → canonical state` er den framtidige grensen. 6J leverer bare
readiness. Preview/apply må senere revalidere matching, authority, permissions,
stale state, juridisk scope og idempotency under etablerte transaksjoner.

PHASE_7: robust Party/ArtistIdentity, aliaser og eksterne contributor-ID-er.
PHASE_8: Work, authorship/publishing og shares; Recording-credit er utilstrekkelig.
Repoet har allerede eldre `music_publisher.Work`, `Writer`, `WriterInWork` og
`CWRExport`. PHASE_8 betyr å avklare canonical Recording→Work-integrasjon og
publishing-semantikk; det betyr ikke at repoet mangler all publishing-kode.
UNKNOWN/SAMPLE_REQUIRED: faktiske fire kildeeksporter, versjoner, ID-stabilitet,
feltbetydning og revisjonsatferd. GAP: upstream-revisjoner; i tillegg må en
framtidig bred rettighetsimport avklare lenking av flere dokumentasjonskilder
uten å overskrive original claim-provenance. Ingen ny Evidence-modell er laget.

Testene bruker `SimpleTestCase`, som forbyr databasequeries. Dermed verifiseres
både fravær av writes og fravær av skjulte ORM-oppslag i readiness/matching.
