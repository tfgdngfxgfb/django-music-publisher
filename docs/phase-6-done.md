# Fase 6 — kanonisk fundament for masterrettigheter

Fase 6 etablerer ett sammenhengende domene for katalog, kilder,
masterrettighetsposisjoner og P7s forvaltning. Denne korte kontrakten gjelder
implementasjonen på `feature/phase-6-consolidation`. Detaljer og avgrensninger
står i [6A](phase-6a-management-semantics.md)–[6K](phase-6k-interoperability-foundation.md)
og [konsolideringsrapporten](phase-6jk-consolidation.md).

**Status: DONE/locked for kodebaseline `54a7c2b970064ca5045046841f5dca95b692440b`.**
Dette omfatter 6A–6K og den samlet verifiserte 6J/6K-integrasjonen, med
readiness-avgrensningene nedenfor. Master er ikke merget.

## Kanonisk modell og juridisk kunnskap

`Recording` har stabil P7-UUID og eksisterer uavhengig av ISRC, Release, Work,
fil, eier og forvaltningsmedlemskap. Eksterne identifikatorer er navngitte
identifikatorer, ikke erstatninger for UUID-en. `RightsClaim` uttrykker én
selvstendig posisjon i mastereierskap, administrasjon **eller** distribusjon.
Holder, eventuell grantor, ownership-andel, territorium, inklusive datoer,
status og eventuell lovlig Release-avgrensning holdes adskilt. Flere
kildedokumenter gjør ikke én posisjon til flere additive rettigheter. Systemet
kan avdekke eksakte dubletter; det kan også beholde historiske posisjoner.

Den felles 6C-motoren vurderer konkret rett og ownership i dato-, territorie-
og Release-kontekst. Ownership er alltid Recording-level. Generelle claims
gjelder også i en konkret Release-kontekst, mens Release-scoped administrasjon
og distribusjon bare gjelder den angitte Release-en. `WORLD`, `INCLUDE` og
`EXCLUDE` har felles semantikk. Ownership er konservativt: Heleid, Deleid,
Ikke eid, Uavklart eller Bestridt. Manglende P7-claim er ikke bevis på at
andre eier 100 %, og ukjent andel er aldri 0 %.

En historisk `on_date`-vurdering uttrykker hva **dagens kanoniske
rettighetskunnskap** sier gjaldt på den juridiske datoen. Immutable
`RightsDecision` og original `SourceRecord` bevarer samtidig tidligere
beslutninger og hva kildene faktisk sa; senere korreksjoner skriver dem ikke om.
`VerificationStatus` og dokumentasjonsstyrke er forskjellige dimensjoner.
Nyregistrering i normal brukerworkflow starter som `UNVERIFIED` og krever en
separat beslutning for bekreftelse.

## Forvaltning og autoritet

`ManagedRecording` er et eksplisitt medlemskap knyttet til
`MusicLibraryEntry → Recording`, ikke en separat katalogpost. 6D beregner
`PENDING` ved reell onboarding, `ACTIVE` ved aktuelt bekreftet kvalifiserende
P7-grunnlag og `INACTIVE` etter faktisk historisk forvaltning uten aktuelt
grunnlag. Effective og lagret status kan avvike; `history_uncertain` uttrykker
usikker arv uten å finne opp en rettighet. Status er ikke bruksautorisasjon.
Tilbakeføring til Musikkarkivet er en eksplisitt autorisert handling for
aldri-aktiv onboarding uten gjenværende plausibelt grunnlag. Rettighets- og
kildehistorikk beholdes.

`ManagedRelease` er et eget katalogforhold og bekrefter verken ownership
eller rettigheter for hvert spor. En Recording kan være beskyttet *kun via
forvaltet utgivelse* uten å bli `ManagedRecording`. 6B gir objektvis vern for
kanoniske Recording-, Release- og ReleaseTrack-data. Teknisk filobservasjon og
Musikkarkivets radiometadata følger fortsatt sine egne fil-/ingestregler.
Katalogvern gir ikke i seg selv DB→FLAC-writeback eller juridisk bruksrett.

## Arbeidsflyt og oppfølging

6E gir autoriserte workflows for onboarding, registrering, decisions,
dokumentasjon, superseding og Release-bulk. Juridiske korreksjoner bevarer
tidligere claims og beslutninger; én posisjon kan erstattes av flere.
Normal GUI-bruk går gjennom disse workflowene. 6F viser rettigheter for én
Recording, 6G håndterer faktiske `ManagedRecording`-medlemskap, og 6H ligger
i den eksisterende **Utgivelse → Rettigheter**-fanen. Release er arbeidskontekst;
juridisk `release_scope` velges særskilt. Bulk bruker preview, signert plan
og atomisk apply, med eksplisitt onboarding og nye `UNVERIFIED` claims.

6I avleder global oppfølging fra kanoniske data uten lagret task-/issue-status.
Kandidater og ferdiglastede fakta går gjennom tilgangsfilter før ren
signalevaluering, telling, filtrering og paginering. Signalene endres når
underliggende data endres. Avsluttede historiske claims utløser ikke i seg
selv varige operative varsler. INACTIVE ManagedRelease er utelatt fra vanlig
kataloggjennomgang, mens 6B-vernet fortsatt gjelder.

## Eksterne kilder og standarder

6J behandler Mudi, The Orchard, Klango og LU-MI som kilder til immutable
observasjoner og forslag, med eksplisitt matching og eksisterende domain
workflows før eventuell kanonisk endring. De konkrete eksportformatene er
fortsatt `UNKNOWN/SAMPLE_REQUIRED`; de fire profilene dikter ikke opp felt.
Samme kilde-ID med endret innhold er et dokumentert upstream-revisjonsgap.
Ingen produksjonsimport, automatisk overwrite, auto-merge eller rights-
bekreftelse følger av readiness-laget.

6K har versjonerte, rene konseptadaptere for DDEX RDR-N 1.5 og CISAC CWR
2.2 revision 2. Noen katalogkonsepter er direkte representerbare; flere
rights-/contributor-konsepter er `LOSSY`, `MISSING` eller `PHASE_7`.
CWR-/publishing-konsepter er markert `PHASE_8`, med fase 7 også der
identitetsavklaring trengs. Eksisterende legacy Work/CWR-funksjoner i
`music_publisher` består, men utgjør ikke en ferdig kanonisk
Recording→Work-kobling. Adapterne genererer ingen standardmeldinger og gjør
ikke P7s kanoniske modeller standardspesifikke.

Fase 7 håndterer moden Party-/ArtistIdentity-avklaring. Fase 8 håndterer
kanonisk Work/publishing-integrasjon. Operativ kildeimport og
standardtransport krever senere arbeid og representative data.

## Verifikasjonsgrunnlag

6J og 6K ble utviklet som søsken fra låst 6I-commit, gjennomgått hver for
seg og integrert uten konflikter eller semantiske endringer. Samlet lokal
SQLite-discovery kjørte **702 tester, 13 hoppet over**. Black 26.5.1,
Django-check, migrasjonskontroll og diff-check var grønne. GitHub Actions
på den integrerte kodecommitten er den autoritative PostgreSQL 17.11-
verifikasjonen; konkrete kjøringer og eventuell eksperimentell Python 3.15-
status oppgis i konsolideringsrapporten.
