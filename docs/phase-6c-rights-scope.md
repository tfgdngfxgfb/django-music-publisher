# Fase 6C – felles scope for masterrettigheter

Normativ kontrakt, 17. september 2026. Bygger på grønn 6B (`56120c0`).
6C beregner registrert rettighetskunnskap. Resultatet er ikke en generell
bruksautorisasjon, Delivery-klarering eller automatisk lifecycle-handling.

## Forhåndsvurdering

`RightsClaim` hadde allerede type, holder, periode, territorier, status,
dokumentasjon og superseding. Valideringen summerte bare identiske perioder og
territoriesignaturer. `rights.summaries` hadde en separat forenklet beregning,
mens 6A-lifecycle og 6B-authority bevisst brukte begrensede egne regler.

Registrerings-/superseding-tjenestene og eksisterende demo/testdata representerer
claims som selvstendige rettighetsposisjoner, ikke additive dokumentkopier.
Den aktive lokale SQLite-databasen ble kontrollert lesende: ingen RightsClaims,
dermed ingen historiske duplikater eller non-ownership-andeler å konvertere der.
Dette er ikke en audit av eventuelle andre installasjoners data.

Ett claim er én selvstendig rettighetsposisjon. Flere dokumenter som bekrefter
samme posisjon skal knyttes gjennom dagens kilde-/avtalegrunnlag, ikke registreres
som flere additive eierinteresser. Separate interesser for samme holder summeres.
Korrigering av en posisjon skjer ved superseding. Ingen evidence-redesign innføres.

Et valgfritt FK til Release er minste korrekte representasjon av det konkrete
behovet. `rights/0004_release_scope` legger bare til `release_scope` med PROTECT
og constraint som tillater feltet bare for administration/distribution.
Ingen eksisterende claims gis Release-scope automatisk; NULL viderefører dagens
generelle scope. Historikken og kanoniske identiteter beholdes.

## Felles motor og API

`rights/scope.py` inneholder rene scope-primitiver og dataclasses. Query-wrappere:

```python
resolve_right(recording, right_type, *, territory, on_date=None,
              release=None, claims=None, local_organization=...)
resolve_ownership(recording, *, territory, on_date=None,
                  release=None, claims=None, local_organization=...)
resolve_rights_for_recordings(recording_ids, right_type, *, territory,
                             on_date=None, release=None, local_organization=...)
resolve_management_basis(recording, *, on_date=None, claims=None,
                         local_organization=...)
```

Utelatt `local_organization` leser RightsConfiguration. Eksplisitt `None` betyr
at lokal organisasjon ikke er kjent, og gir ingen positiv lokal konklusjon.
Recording/Release kan angis som modellobjekt eller UUID.

`RightResolution` har kontekst og alle relevante claims, med separate grupper
for confirmed/disputed/unverified og lokal/annen holder. `has_local_confirmed`,
`has_local_pending` og `has_dispute` er avledede hjelpere. Claims skjules ikke
bak én boolsk konklusjon. `OwnershipResolution` beholder dette rike resultatet,
kjente lokale/andre andeler og claims med ukjent andel i tillegg til kategori.

`evaluate_right`, `evaluate_ownership`, `evaluate_management_basis` og
`claim_applies` kan brukes direkte med ferdig lastede claims. Rene territorie-
funksjoner krever prefetchet territorieliste for INCLUDE/EXCLUDE; de utfører
ingen skjult query per claim. `prepare_claims` er en eksplisitt loader for
eksisterende callers. `claims_for_recordings` henter claims/territorier i to
spørringer; batch-resolveren gjør i tillegg høyst én konfigurasjonsspørring.
Ingen avledede resultater lagres som nye statusfelt.

## Dato, status og territorium

- Fra- og tildato er inklusive. NULL betyr åpen grense, ikke ukjent dagsdato.
- Standarddato er `timezone.localdate()`.
- `on_date` betyr hva **dagens kanoniske kunnskap** sier gjaldt på den juridiske
  datoen. Sen bekreftelse kan dokumentere en eldre periode. Dette rekonstruerer
  ikke hva databasen trodde tidligere.
- CONFIRMED kan inngå i bekreftet resultat. UNVERIFIED og DISPUTED beholdes
  separat. REJECTED og SUPERSEDED utelates også ved historiske spørsmål.
- `evidence_strength` påvirker ikke applicability eller bekreftet status.
- WORLD/INCLUDE/EXCLUDE evalueres mot samme gyldige tobokstavs landunivers som
  eksisterende `music_metadata`/Territory-validering. EXCLUDE er det eksakte
  komplementet i dette universet. Landkoden trenger ingen database-rad.
- Konkret resolution krever et gyldig territorium. `territory=None` avvises.

Ownership, administration og distribution løses uavhengig. Avtaletype gir
ingen avledede rettigheter. Modellen har ingen eksklusivitetsregel for
administrasjon/distribusjon: en annen parts claim opphever ikke en lokal claim.
Fravær av claim betyr manglende registrert grunnlag, ikke bevist fravær av rett.

## Generell og Release-avgrenset rett

`release_scope=NULL` gjelder generelt og også i hver konkret Release-kontekst.
`release_scope=Y` gjelder bare ved spørsmål om Y. `release=None` i konkret
resolver betyr generell Recording-kontekst, aldri «enhver utgivelse».
Mastereierskap kan ikke være Release-avgrenset; administration/distribution kan.

Ved opprettelse kreves at Recording finnes på Release. En senere retting eller
sletting av ReleaseTrack påvirker ikke claimets juridiske scope, beslutninger
eller resolution. En slik mismatch er mulig oppfølging for 6I. Release kan ikke
slettes så lenge et claim refererer til den.

Feltet er immutable som øvrig scope. Superseding kan korrigere det, med samme
Recording og right type. Når scope utelates ved superseding, bevares tidligere
scope for kompatibilitet med eksisterende skjemaer. Eksplisitt None gjør et nytt
erstatningsclaim generelt; det gamle blir stående som historikk.

`create_release_rights_claims` bruker fortsatt Release som arbeidskontekst.
Juridisk Release-scope må oppgis uttrykkelig og må da være den samme Release.
Eksisterende skjemaer introduserer ikke feltet nå. Eksisterende kravvisninger
viser avgrensningen og admin viser feltet read-only, så nye avgrensede claims
ikke fremstår som generelle. Dette er kompatibilitet, ingen ny Rights-GUI.

ManagedRelease gir aldri claims eller rettigheter. 6B-kategorien «Kun via
forvaltet utgivelse» består fram til faktisk ManagedRecording-onboarding.
Release-scoped administration/distribution gir heller ikke Recording-writeback.

## Ownership og overlapp

Share brukes bare på ownership; model clean og dermed tjenester/skjemaer avviser
andel på administration/distribution. NULL ownership-share er tillatt og blir
aldri null prosent. Eksisterende 0–100-range og direkte onboarding-krav beholdes.

`validate_confirmed_ownership_total` bruker `find_ownership_conflict` under den
eksisterende Recording-låsen. For få claims per Recording er en eksakt,
oversiktlig enumerering tilstrekkelig: undersøk hver startdato (og åpen start)
og hvert gyldig land. Summen kan bare øke ved en startdato. Bare claims som
faktisk er samtidige i samme land summeres. Dette fanger også tre eller flere
claims og unngår falske konflikter fra rene parvise overlapp uten felles scope.
En konflikt oppgir vitneland, dato og sum. Ukjente andeler tas ikke som null eller
som bevis for >100; de bevares som usikkerhet i resolution.

For én konkret dato/land:

| Registrert relevant grunnlag | Kategori |
| --- | --- |
| Lokal kjent 100 %, ingen konflikt/ukjent bekreftet andel | Heleid |
| Lokal kjent positiv andel under 100 %, uten konflikt/ukjent andel | Deleid |
| Andre bekreftet 100 %, intet lokalt pending-grunnlag eller konflikt | Ikke eid |
| Ingen claims, ukjent lokal organisasjon eller ukjente bekreftede andeler | Uavklart |
| Bestridt applicable ownership, kjent sum >100, eller mettet 100 % med ytterligere ukjent bekreftet andel | Bestridt |

Bekreftede claims vurderes defensivt også om historiske data har konflikter som
den nye bekreftelsestjenesten ville avvist. 60 % WORLD + 50 % NO er dermed
bestridt i NO, men ikke automatisk i SE. En ubekreftet claim er ikke en bekreftet
eierandel, og holdes tilgjengelig i resultatet for videre vurdering.

## Eksisterende summaries og avgrensning mot 6A/6B/6D

`rights.summaries` bruker motoren, ikke en alternativ datologikk. Den globale
GUI-oppsummeringen konkluderer Heleid/Ikke eid bare når det konkrete resultatet
er slik i alle land. Lokalt kjent eierskap i bare deler av verden vises Deleid.
Konflikt ett sted gir Bestridt; ukjent bekreftet andel gir Uavklart. Dagens
tidligere «Deleid» for kun ukjent lokal andel er bevisst strammet inn.
Territorielt ulike andeler vises ikke som én fiktiv summert global prosent.
Land med identiske applicable claims evalueres samlet; batch henter fortsatt
claims og territorier i to spørringer.

De eldre `has_local_confirmed_right` og `local_confirmed_right_recording_ids`
er oversiktsflagg for et **generelt** bekreftet claim i et ikke-tomt territorielt
scope. De beviser ingen konkret bruksrett. Release-only claims gir ikke disse
generelle flaggene. Bruk konkret resolver eller følgende eksplisitte helper:

`resolve_management_basis` spør etter aktuelt lokalt ownership/admin/distribution
i **noe ikke-tomt territorium og enhver Release-kontekst**. Den returnerer
bekreftede, bestridte og ubekreftede grunnlag separat, men endrer ingen data.
Den er ikke historikkrekonstruksjon og vurderer ikke fremtidige claims som
aktuelt grunnlag. 6D må koble denne kunnskapen til 6As separate onboarding- og
historikkregler.

6A `management_state`/`refresh_management_status` og 6B `catalogue.authority`
endres ikke i 6C. Eksisterende rights-beslutninger kan fortsatt oppdatere status
på allerede eksisterende medlemskap gjennom 6A. Resolverne selv oppretter,
sletter eller oppdaterer aldri ManagedRecording. Eksplisitt onboarding gjennom
eksisterende bulk-service består når caller ber om det. Ordinær FLAC/OneTagger-
radioautoritet, ingest-vern og writeback-gates er uendret.

## Tester og videre arbeid

`rights/test_scope.py` dekker scope-primitiver, status, retrospective kunnskap,
Release-integritet/superseding/historikk, ukjent share, konkrete og globale
eierskapsresultater, multi-claim-overlapp og konstant query-antall. Eksisterende
rights-, managed_music-, catalogue-, ingest-, library-, GUI- og Workbench-tester
brukes for 6A/6B-regresjon. Den tilsiktede schemaendringen testes på SQLite;
GitHub CI er autoritativ for PostgreSQL 17.11. Ingen lokal PostgreSQL-kjøring.

Lokal verifikasjon på Python 3.14.3: 47 rights-tester og 271 relevante
regresjonstester bestått. Etter siste kompatibilitetsendring ble rights,
Workbench og GUI v2 kjørt samlet på nytt: 171 tester bestått. Black 26.5.1
(79 tegn), Django systemcheck, migrasjonsdriftkontroll og `git diff --check`
bestått. Migrasjonen ble kjørt i testdatabasene; brukerens lokale katalogdatabase
er ikke endret av denne verifikasjonen.

Før 6D må det avgjøres hvordan lifecycle skal behandle aktuelle tvister versus
bekreftede grunnlag, fremtidige perioder og historisk forvaltning, og når lagret
status skal oppdateres ved datoovergang. 6A-historikk skal ikke erstattes av en
scope-resolver som bare ser dagens kanoniske claims. Senere GUI må etterspørre
konkret land/dato/Release for bruksspørsmål. Ingen distribution-clearance eller
Delivery-clearance følger automatisk av denne fasen.
