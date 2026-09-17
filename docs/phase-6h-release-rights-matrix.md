# Fase 6H – Release Rights Matrix og bulkregistrering

## Arbeidsflate og domenegrense

Bygger på 6G-baseline `823bf79` og 6A–6F-kontraktene. Den normale
brukeradressen er fortsatt `/v2/utgivelser/<uuid>/?tab=rights`. `release_detail`
delegerer denne fanen til `release_rights_views.overview`; dette er ikke en ny
synlig rights-applikasjon. Alle fire Release-fanene gjenbruker samme header,
return-kontekst, GUI-v2-shell og globale player.

Release er arbeidskontekst for underliggende Recordings. RightsClaims ligger
fortsatt på Recording. ManagedRelease er et separat katalogforhold, ikke
mastereierskap, administrasjon, distribusjonsrett eller clearance. Status og
relasjon vises read-only her; eksisterende redigering ligger under
Utgivelsesdetaljer. Recording-claims avleder ikke ManagedRelease-lifecycle.

Ingen modeller, migrasjoner, nye statuser eller persistente derived fields er
innført. Metadataautoritet, radiometadata, filgenerering og playback-backend er
uendret.

## Matrise og rettighetskontekst

`gui_v2/release_rights.py` er et read-only presentasjonslag. Én rad tilsvarer én
ReleaseTrack, ordnet etter sequence/id. Disc, side, track number og eventuell
utgivelsesspesifikk tittel presenteres uten å redigere Recording-identiteten.
En Recording kan derfor opptre på flere rader. Summaries beregnes én gang per
unik Recording; det finnes ingen samlet juridisk «albumandel».

Forvaltning bruker 6D `evaluate_management_state`, inklusive usikker historikk
og avvik mellom lagret og effektiv status. GET materialiserer ikke status.
Uten ManagedRecording skilles det mellom «Kun via denne utgivelsen», «Kun via
annen forvaltet utgivelse» og «Ikke forvaltet». Beskyttende utgivelser vises i
inspector. Katalogvern oppretter ingen management membership eller rettighet.

Mastereierskap bruker 6Cs konservative `classify_ownership`: heleid, deleid,
ikke eid, uavklart eller bestridt. Ukjent andel blir ikke null; en lokal prosent
vises bare når eksisterende summary gir en entydig andel.

Administrasjon og distribusjon viser lokale posisjoner som gjelder generelt
eller har `release_scope` lik gjeldende Release. Claims for andre Releases er
utelatt. 6Cs felles dato-, Release- og territorieprimitiver brukes; aktuelt
bekreftet grunnlag krever dagens inklusive gyldighetsdato og ikke-tomt
territorielt scope. Dette sier at grunnlag finnes i et scope, ikke at bruken er
klarert overalt. Uverifiserte, bestridte og kommende posisjoner merkes separat.
Inspector viser territorium, periode og generell/utgivelsesavgrenset rett.
Utløpte bekreftede posisjoner teller ikke som aktuelt bekreftet grunnlag.

Ownership kan etter 6C ikke være Release-scoped. Release-arbeidskontekst gjør
aldri et claim automatisk Release-scoped.

## Inspector, utvalg og modal

Aktiv rad og checkbox-utvalg er uavhengige. Inspector viser kompakt kontekst og
lenker til native Recording → Rettigheter (6F), med tilbakekobling til aktuell
Release, rad og filter. Den inneholder ikke decisions, superseding eller
claim-redigering. Lukket inspector frigjør tabellbredden.

Lokalt søk og filtre gjelder den lastede utgivelsen. «Velg alle synlige spor»
velger bare synlige rader; allerede valgte skjulte rader beholdes og teller
fortsatt i selection bar. Baren viser både sporplasser og unike innspillinger.
Bulk dedupliserer alltid på Recording. Rad/filter lagres i URL; checkbox-utvalg
er midlertidig sidetilstand og lagres ikke som draft.

Registreringen åpnes i native `<dialog>` i eksisterende fane. Første steg
registrerer én felles posisjon; andre steg viser planen før eksplisitt apply.
Avbryt/Escape bevarer matrix-state, og fokus returnerer til åpnerknappen.
Nettverksfeil beholder innskrevne verdier og deaktiverte blocker-handlinger.
Etter vellykket apply oppdateres fanen gjennom eksisterende `P7_V2.navigate`,
slik at player-shell beholdes. Nye claims vises som uverifiserte.

Det eneste nye URL-endepunktet er et internt modalfragment:

`/v2/utgivelser/<uuid>/rettigheter/registrering/` (`release_rights_bulk`).

GET henter form; POST med `stage=preview`, `edit` eller `apply` bruker samme
adapter. Ingen ny selvstendig Release-rights-side er nødvendig.

## Registrering, onboarding og provenance

Formen bygger på eksisterende 6E `ReleaseRightsClaimForm`. Lokal organisasjon
er låst som holder. Ett right type velges per batch. Mastereierskap har
redigerbar 100 %-standard og krever kjent andel. Administrasjon/distribusjon
krever eksplisitt valg mellom generell rett og bare denne utgivelsen, uten
ownership-andel. Status kan ikke velges. Grantor, territorier, gyldighetsperiode,
SourceRecord, Agreement, evidence strength og noter bruker eksisterende felt.

Bare eksisterende Recordings kan velges. Unmanaged Recordings krever eksplisitt
onboarding og eksisterende strenge onboarding-permissions. Resultatet er
ManagedRecording PENDING og ett lokalt UNVERIFIED claim. Manglende
MusicLibraryEntry for en eksisterende Recording opprettes av eksisterende
onboarding-service og forklares før apply. Det opprettes ingen ny Recording.
ManagedRelease eller en preview fører aldri til skjult onboarding.

`catalogue_sources.py` deler 6Gs defaultregel med 6H: tidligste eksplisitt
koblede kilde via Recording/MusicLibraryEntry-assertions, contributions eller
gjennomført ingest. Senere rescans erstatter ikke automatisk denne opprinnelsen.
Bare når alle unike valgte Recordings har samme kilde foreslås en felles
SourceRecord. Ulik eller delvis manglende provenance gir blank default.
Brukeren kan angi UUID til en annen eksisterende kilde for hele batchen.
Ingen SourceRecord opprettes eller opprinnelig provenance omskrives av dette.
Agreement/dokumentasjon bekrefter ikke rettigheten.

## Autorisert preview/apply og dublettkontroll

GUI bruker 6E `process_release_form` og workflowenes preview/apply. Signert plan
er bundet til actor, valgte unike Recordings, juridiske input, onboardingvalg,
konfigurasjon og revisjoner for Recording, ReleaseTrack, membership og claims.
Planen utløper etter eksisterende 30 minutter. Apply kontrollerer permissions
og beregner planen på nytt under Recording-first-låsing. Stale/tuklet plan
avvises. Hele batchen er atomisk; ingen delvis success eller automatisk retry.

6Es bulk-preflight er utvidet med konservativ kontroll av juridiske posisjoner:

* Eksakt samme Recording, type, holder, grantor, andel, territory mode/set,
  periode og Release-scope blokkeres også når eksisterende claim er historisk.
* SourceRecord, Agreement, evidence strength og noter skaper ikke en ny
  juridisk posisjon. Ny dokumentasjon tilhører eksisterende 6F-workflow.
* Mulig overlappende ikke-avvist/ikke-erstattet posisjon for samme holder/type
  blokkeres ved faktisk felles dato, territorium og Release-scope (6C).
* Disjunkte perioder/territorier eller separate Release-scopes kan registreres
  som selvstendige posisjoner. Ownership-konfliktkontrollen fra 6C beholdes.

En blokkering forklarer at posisjonen må avklares i Recording → Rettigheter.
Det gjøres ingen automatisk merging, superseding eller dokumentasjonsoppdatering.
Kontrollen er med hensikt konservativ: også en mulig legitim separat posisjon
kan kreve individuell behandling. Lavnivå import/system-services endres ikke.
Samme kontroll kjøres både i preview og ved apply; samtidige identiske applies
skal ikke gi additive duplikater. Dette har en PostgreSQL-låsetest.

## Tilgang, ytelse og avgrensninger

Release krever `catalogue.view_release`; matrix/inspector krever
`rights.view_rightsclaim`. Uten rights-view vises en tilgangsmelding, og øvrige
Release-faner fungerer. Registrering krever også `rights.add_rightsclaim` og
`GUI_V2_WRITES_ENABLED`. 6E håndhever actor-policyen på nytt. Kilde- og
avtalevalg begrenses av respektive view-permissions. Onboarding krever samme
strenge adgang som før. Den gamle inline rights-POST-flaten er stengt.

Recording-/artist-/identifier-data, claims, territorier, decisions, memberships
og beskyttende Releases hentes batchvis. Eierskap/lifecycle beregnes fra dette
grunnlaget. Query-skalering testes for flere Recordings, gjentatte sporplasser
og flere claims med relasjoner. Full filhistorikk lastes ikke.

Hele én utgivelse vises uten serverpaginering. Bulk er begrenset til 500 unike
Recordings av eksisterende 6E-service. JavaScript kreves for inspector og modal;
SourceRecord velges foreløpig med eksisterende UUID, uten ny global søkemotor.

Illustrasjon A styrte matrix/inspector; B kun selection bar; C den store
registreringsdialogen; D korte forklaringer. Oppdiktet toppmeny, TONO/NCB som
masteradministrasjon, «album rights», statusvalg, draft, mass-edit, bulk
confirmation, eksport og automatisk distribusjons-clearance ble ikke kopiert.
Eksisterende dark/light CSS-variabler, Release-tabs og footer er autoritative.

Global oppfølging/data quality er fortsatt 6I. Import readiness (6J), DDEX/CWR
(6K), Party/Work, økonomi og distribution/Delivery clearance er ikke bygget.

## Verifikasjon

Lokalt kjøres full CI-lik discovery med SQLite:
`coverage run --omit=manage.py manage.py test --noinput`.
Resultat: 621 tester kjørt, 13 hoppet over (PostgreSQL-spesifikke tester),
629 oppdaget av runneren. Målrettede 6H-/Release-/rights-/managed-regresjoner
inngår. Black 26.5.1 med prosjektets linjelengde 79, Django check,
migration-drift check og diff-check inngår i sluttkontrollen.

Nettleserkontroll bruker syntetisk, isolert minnedatabase: uavhengig inspector
og utvalg, gjentatt Recording, preview/apply, eksplisitt Release-scope,
onboarding-preview, avbryt/Escape/fokus, lys/mørk og bred/smal skjerm. Ingen
reelle rettighets- eller katalogdata brukes i denne kontrollen.

GitHub Actions er autoritativ for PostgreSQL 17.11/full suite og samtidighet.
Eksakt push-commit må ha grønne obligatoriske build-/foundation-jobber før
6H kan vurderes som CI-verifisert baseline. Python 3.15 er eksperimentell.
Fasen merges ikke og 6I startes ikke som del av denne bestillingen.
