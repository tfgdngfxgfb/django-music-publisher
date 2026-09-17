# Fase 6F: Recording → Rettigheter i GUI v2

## Avgrensning og forhåndsvurdering

Utgangspunkt: grønn 6E-baseline `ee1d4dc`. Eksisterende 6C-presentasjon,
6D-lifecycle og 6E-workflows er tilstrekkelige. Ingen modell eller migrasjon
er nødvendig. Dette er et presentasjons- og navigasjonslag, ikke en ny rights-
eller lifecycle-motor. Domenefilene i rights/managed_music er uendret.

Felles Recording-header, return-kontekst, tema, tabeller, knapper, intern
GUI-v2-navigasjon og global footer-player gjenbrukes. Rettigheter-fanen peker
nå til GUI v2 fra alle eksisterende Recording-arbeidsområder. Medvirkende og
Kilder/historikk beholder eldre arbeidsflate.

## Sider og ruter

Prefiks: `/v2/innspillinger/<recording_id>/rettigheter/`.

| Suffiks | URL-navn i gui_v2 | Innhold |
| --- | --- | --- |
| tomt | recording_rights | Sammendrag, lokal oppfølging og grupperte krav |
| registrer/ | recording_rights_register | Ny uverifisert posisjon |
| <claim_id>/ | recording_rights_claim | Juridisk scope, dokumentasjon, relasjoner og beslutninger |
| <claim_id>/dokumenter/ | recording_rights_document | Avtalekopling/dokumentasjonsstyrke og auditnotat |
| <claim_id>/vurder/ | recording_rights_decide | Tillatte verification-beslutninger |
| <claim_id>/erstatt/ | recording_rights_replace | En eller flere erstatningsposisjoner |

Claim-oppslag er alltid avgrenset til Recording i URL-en. Alle interne lenker
bevarer validert return-kontekst. Skjemaene er egne sider i samme shell.

## Presentasjon og kontrakt

`gui_v2/recording_rights.py` henter grunnlaget og lager rene presentasjonsdata.
`rights_views.py` håndterer HTTP/permissions og kaller autoriserte 6E-workflows.
`rights_forms.py` tilpasser eksisterende Rights-former, uten alternativ
juridisk validering. `rights.js` viser relevante felter og legger til rader i
en Django-formset; JavaScript gir ingen rettigheter eller autoritet.

- Forvaltning bruker 6D `evaluate_management_state()` med prefetchet grunnlag.
  Effektiv status vises; avvik fra lagret status er et oppfølgingssignal.
  GET reconciler ikke. Teknisk stored/effective-visning er sekundær for admin.
- Mastereierskap bruker 6C `classify_ownership()`: heleid, deleid, ikke eid,
  uavklart eller bestridt. NULL-andel vises aldri som null prosent. Varierende
  territorielle andeler presenteres ikke som én global prosent.
- Administrasjon/distribusjon viser antall aktuelle bekreftede lokale grunnlag
  fra `evaluate_management_basis()`, med henvisning til hvert kravs scope.
  Dette er ikke generell bruksautorisasjon eller distribution clearance.
- ManagedRelease-only vises som «Kun via forvaltet utgivelse», uten onboarding.
- UNVERIFIED og DISPUTED ligger i «Krever behandling», også ved historisk
  periode. Aktuelle og framtidige CONFIRMED ligger i «Bekreftede posisjoner»;
  framtidige er tydelig merket kommende. Utløpte CONFIRMED, REJECTED og
  SUPERSEDED ligger i historikk. Datoavgrensning følger 6C.
- Detaljsiden viser opprinnelig SourceRecord/SourceSystem ved lesetilgang,
  Agreement/dokumentreferanser ved riktige permissions, juridisk scope,
  kronologiske beslutninger med actor/notat og alle superseding-relasjoner.
  Dokumentreferansene innfører ingen ny filservering eller upload-workflow.

## Endringer og sikkerhet

Registrering bruker `register_rights_claim()`, dokumentasjon
`document_rights_claim()`, beslutninger `decide_rights_claim()` og splitting
`supersede_rights_claims()`. Produksjonsdomene og låserekkefølge er uendret.

Nye krav er alltid UNVERIFIED. Registreringsinngangen tilbys bare for
ManagedRecording. GET uten medlemskap returnerer til oversikten; POST
revalideres i 6E. Ingen skjult onboarding. Historikk og dokumentasjon kan
fortsatt åpnes etter eksplisitt tilbakeføring. 6E blokkerer nye aktuelle eller
framtidige lokale grunnlag uten medlemskap, også ved decision/superseding.

Decision-valg følger eksisterende transition matrix; SUPERSEDED er terminalt.
Dokumentasjon endrer ikke juridisk scope/status eller original provenance.
Splitting låser Recording/right_type, oppretter 1–20 uverifiserte posisjoner
og krever eksplisitt konsekvensbekreftelse. Hele operasjonen er atomisk i 6E.
Feil rettighetstype korrigeres ved avvisning og ny separat posisjon, ikke ved
å endre typen under superseding.

Lesing krever `catalogue.view_recording` og `rights.view_rightsclaim`.
Registrering krever add_rightsclaim, dokumentasjon change_rightsclaim,
beslutninger decide_rightsclaim, splitting add + decide. Avtaleendring krever
dessuten manage_agreement og view_agreement. Kilde-/avtalevalg skjules ved
manglende lesetilgang; eksisterende referanser bevares. Dokumentreferanser
krever view_agreement, view_agreementdocument og view_fileasset. Alle POST
krever `GUI_V2_WRITES_ENABLED`; CSRF og workflow-permissions gjelder fortsatt.

## Visuell retning og ytelse

A gir oversiktens fire sammendrag og tre grupper; E gir detaljstruktur;
D gir registreringsfeltene; C gir original til venstre / nye posisjoner til
høyre. B viser Release-bulk og er bevisst utsatt til 6H. Bildenes bokstaver i
bestillingsteksten samsvarer ikke helt med de vedlagte filenes innhold.

Ingen oppdiktet toppmeny, persondata, TONO-semantikk, rettighetstype,
konfliktfrihetsstempel, draft-status eller separat spiller er innført.
Registrering er en vanlig GUI-v2-side; ingen ny modal-/draft-motor.
Splitting viser redigerbare posisjoner og konsekvensbekreftelse samlet;
separat preview-steg er ikke innført. Tema bruker eksisterende CSS-variabler.
Grid kollapser på smalere bredder; tabeller har lokal overflow og kontroller
har labels og synlig tastaturfokus.

Claims lastes med select_related for parter, avtale, kilde og Release-scope,
og prefetch for territorier og beslutninger/actor. Samme grunnlag brukes av
sammendrag og tabeller. Dokumentlisten hentes bare på detaljsiden og med
lesetilgang. Query-test sammenligner 1 og 36 claims og tillater høyst én ekstra
query; det er ingen query per claim. Svært lange kravlister er ikke paginert i
denne første Recording-avgrensede flaten.

## Verifikasjon og videre arbeid

`gui_v2.test_recording_rights` dekker state/gruppering, fem ownership-kategorier,
ukjent/varierende andel, Release-scope, history uncertainty, dokumenttilgang,
full scope-registrering, decisions, dokumentasjon/provenance, atomisk splitting,
terminal status, permission-denials, writes-flag, historisk tilbakeføring,
native tabs/return/footer, read-only GET og query-skalering.

Full SQLite-discovery kjøres med samme coverage-form som CI, sammen med Black,
Django-check, migration drift og diff-check. GitHub CI verifiserer PostgreSQL
17.11 og concurrency. Faktisk lydavspilling er ikke manuelt demonstrert med
lydfil i det isolerte visuelle testmiljøet; eksisterende playback-regresjoner
inngår i testsuiten, og player-/navigation-koden er uendret.

6G skal gi eksplisitte onboarding-/return-/legacy-correction-handlinger.
6H skal presentere eksisterende bulk preview/apply fra Release. 6I skal samle
oppfølging på tvers av katalogen. Disse fasene er ikke startet her. Senere
standardadaptere for RDR-N/CWR påvirkes ikke; ingen nye standardfelt eller
annen utvidelse av kjernedomenet er innført.
