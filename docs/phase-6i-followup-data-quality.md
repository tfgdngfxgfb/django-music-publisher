# Fase 6I – Oppfølging og datakvalitet

Baseline: `007194c911dcf7e366489aec7843bd96f0d68965` (6H).
Branch: `feature/phase-6i-followup-data-quality`.

## Kontrakt

`/v2/oppfolging/` er en native, read-only GUI-v2-arbeidsflate. Den avleder
oppfølgingspunkter fra kanoniske data i 6A–6H og sender brukeren til 6F
(Recording → Rettigheter), 6G (Forvaltet musikk) eller eksisterende 6H-fane
`/v2/utgivelser/<uuid>/?tab=rights`. Returkontekst følger lenkene.

GET utfører ingen reconciliation, registrering, beslutning, audit eller
autokorrigering. POST på 6I-ruten avvises med 405. Ingen signaler eller items
lagres. Ingen Task/Issue/Event-modell, migrasjon, ny lifecycle-status, draft,
assignment, snooze, dismissal eller «marker som løst» er innført.

Forvaltningsmedlemskap, katalogvern og rettigheter er fortsatt forskjellige
begreper. Ingen signaler eller grønne kort representerer use-/Delivery-clearance.

## Kandidater og ren evaluering

`rights/followup_queries.py` er ORM-grensen. `Visibility` avklarer tilganger før
kandidater lastes. Recording-kandidater avgrenses med SQL Exists til lokale
åpne aktuelle/framtidige claims, eksisterende ManagedRecording, eller åpne
claims på Recordings beskyttet via ManagedRelease. Hele Musikkarkivet lastes
ikke. Release-kandidater er bare ACTIVE/PENDING ManagedRelease.

Recordings lastes i grupper på 200 med claims, territorier, medlemskap,
ReleaseTrack-relasjoner og katalogidentitet. Beslutningshistorikk lastes samlet
når management-visning er tillatt. 6Ds `evaluate_management_state()` beregner
historikk/status på dette ferdiglastede grunnlaget. Release review lastes i
grupper på 50; claims for tilhørende unike Recordings lastes i grupper på 200.
Ingen gjenbruk av hele GUI-matrisen per Release og ingen per-row query-loop.

`rights/followup.py` inneholder frozen dataclasses for fakta, FollowUpSignal og
FollowUpItem. Fakta inneholder verdier og tuples, ikke levende ORM-relasjoner.
Evaluatorene gjør null databaseoppslag. TerritoryFact/ClaimFact støtter det
ferdiglastede protokollet til de eksisterende rene 6C-primitivene.

Den samlede listen er en live derived view. Avledede items beholdes i minnet
for global telling, søk, sortering og paginering. Minnebruken vokser med antall
kandidater som faktisk gir signaler; dette er ikke en persistent eller frosset
snapshot-kø. Chunking begrenser ORM-batchene, ikke det totale antall resultater.

## Delte domenefunksjoner

`rights/positions.py` tilbyr:

- `legal_position_identity(claim)`: Recording, type, holder, grantor, share,
  territory mode/territories, datoer og release_scope. Dokumentasjon, Agreement,
  kilde og evidence er ikke juridisk identitet. Territorier må være lastet.
- `claims_applicable_in_release_context(claims, release, local)`: relevante
  lokale claims med ikke-tomt territorium, generelt eller scoped til angitt
  Release. Dato og bekreftelse velges eksplisitt av kalleren.

6Hs `_position_blocker()` bruker identiteten for eksakte dubletter, fortsatt
inkludert historiske claims i manuell registreringskontroll. Den eksisterende
bredere overlap-blockeren er beholdt uendret. 6I gjør bare eksakt identitet til
dublettsignal. Den rene Release-utvelgelsen er delt med 6Hs `contextual_basis()`;
counts og claim-presentasjon blir fortsatt laget i GUI-laget.

6Es medlemskapsguard er uendret. Den beskytter også UNVERIFIED/DISPUTED live
grunnlag. 6Is varsel uten membership er bevisst smalere: CONFIRMED current/future.

## Signaler i v1

| Primærkategori | Kode | Regel |
| --- | --- | --- |
| Krever behandling | `claim.unverified` | Aktuell/framtidig lokal uverifisert posisjon |
| Krever behandling | `claim.disputed` | Aktuell/framtidig lokal bestridt posisjon |
| Krever behandling | `management.history_uncertain` | 6D kan ikke fastslå historikken |
| Krever behandling | `management.pending_without_basis` | Effektiv PENDING uten plausibelt grunnlag eller bekreftet historikk |
| Datakvalitet | `management.local_basis_without_membership` | Aktuelt/framtidig bekreftet lokalt grunnlag uten ManagedRecording |
| Datakvalitet/info | `management.needs_reconciliation` | Kjent effective status avviker fra stored; «Venter på systemoppdatering» |
| Datakvalitet | `ownership.conflict` | 6C finner faktisk samtidig >100 % bekreftet ownership i et territorium nå/framtidig |
| Datakvalitet | `ownership.local_share_unknown` | Relevant lokal ownership med NULL-andel |
| Datakvalitet | `evidence.not_assessed` | Aktuell/framtidig bekreftet lokal posisjon, evidence ikke vurdert |
| Datakvalitet | `evidence.weak` | Tilsvarende med svak evidence |
| Datakvalitet | `scope.release_mismatch` | Åpen aktuell/framtidig scoped posisjon mangler Recording på sin Release |
| Datakvalitet | `claim.duplicate_position` | Minst to åpne aktuelle/framtidige claims med eksakt juridisk identitet |
| Kommende | `claim.expiring` | Aktuelt CONFIRMED lokalt claim utløper innen horisonten |
| Kommende | `claim.starts_soon` | Framtidig CONFIRMED lokalt claim starter innen horisonten |
| Kataloggjennomgang | `release.managed_only_recordings` | Unike innspillinger på ACTIVE/PENDING ManagedRelease uten ManagedRecording |
| Kataloggjennomgang | `release.without_current_local_basis` | Unike innspillinger uten aktuelt bekreftet lokalt grunnlag i denne Release-konteksten |

Ingen generell `ownership.unresolved` eller tredjeparts-completeness-kø.
Tredjepartsposisjoner brukes når de gjelder det relevante P7-katalogobjektet,
for eksempel reell ownership-konflikt, eksakt dublett eller scope-avvik.

Dato er Django lokal dato. Gyldighetsgrenser og horisonter er inklusive.
Horisonten er 30/90/180 dager (standard 90). Uverifisert/bestridt status gir
ikke kommende-signaler eller evidence-varsler. PROBABLE/STRONG/DOCUMENTED
utløser ikke evidence-varsel. NULL ownership-andel vises som ukjent, aldri 0 %.

Ownership-konflikt bruker 6Cs reelle territorie-/tidsoverlapp. Evalueringskopier
har start tidligst i dag, slik at en gammel konflikt ikke skjuler en framtidig
konflikt. Kanoniske claims endres ikke. Ett item viser det første konkrete
overlappsvitnet per Recording; flere uavhengige konflikter på samme Recording
er ikke en ny full konfliktresolver i GUI-et.

## Historikk og bortfall

REJECTED/SUPERSEDED gir ikke vanlige duplicate-/mismatch-varsler. Utløpte
bekreftede posisjoner gir heller ikke normale evidence-/katalogavvik. Alle
statuser og immutable decisions inngår fortsatt i 6Ds management-historikk.

Historiske uavklarte claims kan vises som underlag i inspector for et konkret
`management.history_uncertain`-item. De gis ikke eget normalt current-signal.
Dermed oppstår ingen permanent kø bare fordi historisk katalogstruktur endres.
History uncertainty og reconciliation-avvik er gjensidig utelukkende etter 6D.

Når UNVERIFIED bekreftes, forsvinner `claim.unverified`. Samme claim-item kan
bestå som `evidence.not_assessed` eller kommende utløp. Hele itemet forsvinner
først når ingen relevante signaler består. Tilsvarende gjelder onboarding,
legacy correction, beslutninger og reparasjon av ReleaseTrack-relasjoner.
6I utfører ingen av disse endringene selv.

## Release review

Aktuelt lokalt grunnlag her er CONFIRMED current ownership **eller** current
administration/distribution som er generell eller scoped til denne Release.
Rett til en annen Release teller ikke. Ownership krever ikke Release-scope.

Gjentatte sporplasser telles én gang per Recording. Ett Release-item kan ha
begge review-signaler. INACTIVE ManagedRelease utelates fra normal review, men
6Bs eksistensbaserte katalogvern for alle ManagedRelease-statuser er uendret.

## Aggregering, tilgang og paginering

Ett item per claim med flere signaler; ett per management-medlemskap/Recording
uten medlemskap; ett per ownership-konfliktgrunnlag per Recording; ett per
eksakt dublettgruppe; ett per Release. Tekniske nøkler er stabile med target-ID
og eventuelt digest av juridisk identitet/deltakende claims.

Primærkategori: behandling > datakvalitet > kommende > kataloggjennomgang.
Sekundære signaler står i inspector. Kategoritall er gjensidig utelukkende.
Standardrekkefølge: strukturelle motsigelser, behandling, datakvalitet,
kommende dato, review/info; deretter tittel og stabil nøkkel. Dette er en
teknisk rekkefølge, ikke en ny virksomhetsscore.

Tilgangsgrensen er før evaluering/aggregering/telling/filtervalg/paginering:

- Workspace: `rights.view_rightsclaim`.
- Claim/Recording: dessuten `catalogue.view_recording`.
- Management: dessuten `managed_music.view_managedrecording`.
- Release review: `catalogue.view_release` + `managed_music.view_managedrelease`.
- Holder/grantor-navn: `parties.view_party`. Kanonisk Recording-kreditering
  følger Recording-view, som ellers i GUI v2.
- Kildeposter og kildesystemvalg: begge provenance view-permissions.
- Agreement-navn: `rights.view_agreement`.
- Release-scope viser bare generisk «Utgivelsesavgrenset» uten Release-view.

En bruker med kun Release-review-tilgang får aggregert Release-informasjon,
ikke innspillingstitler eller claim-inspector. Skjulte detaljnavn deltar verken
i søk eller filtervalg. Filtervalg utledes fra tillatte items, aldri globale
SourceSystem-/Party-/Agreement-tabeller.

Rekkefølge: tillatte kandidater → evaluering → items → kategoritall → brukerens
filtre → deterministisk sort → 25 items per side. Samme kanoniske data gir
samme rekkefølge. Endringer mellom sidevisninger kan naturlig flytte resultater.

## GUI og fasegrenser

Eksisterende GUI-v2-shell, navigasjon, CSS-variabler, light/dark og global player
er gjenbrukt. Kontroll-arbeidsflaten er beholdt. Hjem får kun et adgangsstyrt
workspace-kort; Hjem kjører aldri signalmotoren eller viser globale 6I-counts.

Fire klikkbare summary-kort og category tabs deler kategori. Kompakt søk,
objekttype, egenskap og horisont suppleres av en native dialog/drawer med
rettighetstype, signal, tillatte kildesystemer og sortering. Dialogen har labels,
Escape og fokusretur. Valgt rad åpner typeavhengig inspector med fakta og
lenker til eksisterende arbeidsflater, ingen beslutningsknapper i 6I.

Illustrasjonene brukes som list/inspector- og filterreferanser. Oppdiktet
topp-/sidenavigasjon, bulk-checkboxer, tasks og persistence er ikke kopiert.
Ingen 6J-import, 6K-adapter, Party/Work-endring, clearance eller scheduler.

## Verifikasjon

`rights.test_followup` tester de rene signalreglene med null queries, identitet,
scope, status/evidence, inklusive datoer, aggregering og stabile nøkler.
`gui_v2.test_followup` tester permissions, skjulte detaljer/filtervalg,
read-only snapshots, POST-avvisning, lenker, canonical signalbortfall,
legacy correction, eksplisitt onboarding, den uendrede manuelle membership-
guarden, historikk, INACTIVE-vern og Home uten signalmotor.

Query-skalering er eksplisitt testet ved 1→100 claims, 1→100 ManagedRecordings
og 1→50 ManagedReleases med spor: samme query-antall innen én batch.
Antallet kan øke ved batchgrenser, ikke per enkelt objekt i batchen.

Visuell kontroll: isolert SQLite-minnedatabase med syntetiske data; lyst og
mørkt tema, desktop og 780 CSS-piksler, GET-filterdialog, Escape/fokus og
global player-shell. Ingen operativ database ble endret for denne kontrollen.

Full lokal discovery bruker `coverage run --omit=manage.py manage.py test` med
SQLite. Black 26.5.1/79, systemcheck, migration drift og diff-check kreves.
GitHub Actions på den rapporterte HEAD er autoritativ PostgreSQL 17.11/full
suite-verifikasjon. Fasens sluttstatus rapporteres først etter obligatorisk CI.

Lokal sluttkontroll: 657 tester kjørt, 13 hoppet over (665 discovered), alle
grønne. Black 26.5.1, systemcheck, migration drift og staged diff-check er grønne.
Forvaltningslenker bruker Recording-UUID for entydig utvalg også ved like titler;
6G har fått tilsvarende UUID-søk og en lenke tilbake til opprinnelig arbeidsflate.
