# Fase 6D – scope-aware forvaltningslivssyklus

Normativ kontrakt, 17. september 2026. Bygger på CI-verifisert 6C `a67ed6b`,
[6A](phase-6a-management-semantics.md) og
[6B](phase-6b-object-authority.md). Ingen nye modeller eller migrasjoner.

## Forhåndsvurdering mot eksisterende kode

- 6A hadde korrekt medlemskapsvern, beslutningsbasert historikk, eksplisitt
  tilbakeføring, legacy-usikkerhet og Recording-lås før statusendring.
- Den separate current-beregningen brukte bare status/dato og manglet 6Cs
  ikke-tomme territorielle scope. Disputed-grunnlag manglet eget oppfølgingsflagg.
- Rights-beslutninger og superseding kalte allerede status-refresh. Disse
  integrasjonspunktene beholdes uten endring av rights-tjenestene.
- Tilbakeføring hadde egne betingelser ved siden av lifecycle-resultatet.
- Alle ManagedRecording-saves utløste FLAC-sync-markering. Status alene endrer
  ikke 6B-autoritet; suppression finnes allerede og kan brukes rundt slike saves.
- GUI/Workbench viser medlemskap eller lagret status. De undersøkte kallene
  bruker ikke lifecycle-status som konkret bruksrett. Delivery baserer sin
  metadatahåndtering på medlemskap og snapshots, ikke ACTIVE som rettighetsbevis.
- Datooverganger manglet en batchoppdatering. Claims, territorier og beslutninger
  kan lastes samlet, mens vanlige CanonicalModel-saves må beholdes for revisjon
  og validering. Det er ingen grunn til schemaendring eller ny scheduler.

## Fire adskilte begreper

1. RightsClaim, immutable RightsDecision og juridisk scope er grunnlaget.
2. 6Cs management basis beskriver aktuelle lokale rettighetsposisjoner i noe
   ikke-tomt territorium og enhver juridisk Release-kontekst.
3. ManagedRecording er eksplisitt etablert medlemskap. Det opprettes og avsluttes
   gjennom egne workflows, aldri som en bivirkning av resolution/reconciliation.
4. ManagedRecording.status er materialisert operativ tilstand, ikke juridisk
   evidens eller beviset på tidligere forvaltning.

`ACTIVE` er ikke use-clearance. Bruk `resolve_right()` for et konkret spørsmål
om rettighetstype, dato, territorium og Release. 6D innfører ingen ny
distribusjons-/Delivery-klarering.

## Effektiv status

| Grunnlag | Effektiv status |
| --- | --- |
| Aktuelt bekreftet lokalt grunnlag | ACTIVE |
| Ingen aktuell bekreftelse, men faktisk bekreftet historikk | INACTIVE |
| Verken aktuell bekreftelse eller historikk | PENDING |
| Lagret ACTIVE/INACTIVE uten sikkert aktuelt eller historisk grunnlag | Ukjent (`status=None`), behold lagret status |

Current beregnes med 6Cs `evaluate_management_basis()` over lastede claims.
Ownership, administration og distribution for konfigurert lokal organisasjon
kvalifiserer. WORLD/INCLUDE/EXCLUDE følger samme motor og landunivers som 6C.
Et tomt territorielt scope kvalifiserer verken nå, som plausibelt grunnlag eller
som historikk. Tredjepartskrav er ikke P7-grunnlag.

Bekreftet Release-scoped administration/distribution kan holde medlemskapet
ACTIVE uten å gi generell rettighet. Senere fjerning/endring av ReleaseTrack
fjerner ikke claimets juridiske betydning eller historikk. ManagedRelease
endrer verken claims eller denne vurderingen, og statusen på ManagedRelease
reconciles aldri av 6D.

Lokale DISPUTED claims er plausible og krever oppfølging, men er ikke bekreftet
current basis. Med historikk blir resultatet INACTIVE, uten historikk PENDING.
Et annet aktuelt bekreftet lokalt claim beholder ACTIVE, også med follow-up.
En samlet ownership-konflikt hos 6C opphever ikke automatisk et CONFIRMED lokalt
claim; lifecycle er ikke rettighetsklarering. Den konflikten hører til senere
rights-oppfølging og gir ikke automatisk lifecycle-flagg for lokal DISPUTED.

## Fremtid, utløp og faktisk historikk

Fra- og tildato er inklusive; manglende grense er åpen. Standarddato er
`timezone.localdate()`. Future CONFIRMED gir PENDING eller historisk INACTIVE,
og kan gi ACTIVE fra startdato uten ny beslutning. Dagen etter siste gyldighetsdag
kan status bli INACTIVE. Et annet aktuelt grunnlag hindrer et falskt opphold.

Historikk bruker fortsatt RightsDecision, også for claims som nå er REJECTED
eller SUPERSEDED. Den erstattes ikke med et historisk kall til 6C, som beskriver
dagens kanoniske rettighetskunnskap og derfor svarer på et annet spørsmål.

- Retrospektiv bekreftelse av en avsluttet periode etablerer historisk forvaltning.
- Senere dispute, rejection eller superseding sletter ikke virksom bekreftelse.
- Future CONFIRMED som avvises/erstattes før start, etablerer ikke historikk.
- En bekreftet periode som både startet og utløp mellom to kjøringer, gir
  historikk selv om lagret status aldri rakk å være ACTIVE.
- Legacy CONFIRMED uten beslutningspost støttes som i 6A når perioden har startet.
- DOCUMENTED åpner/lukker ikke bekreftelsesperioder.

Beslutningene behandles kronologisk etter `created_at`, deretter UUID ved likt
tidspunkt. Historikk viderefører 6As lokale kalenderdager og retrospektive
semantikk; 6Cs `periods_overlap()` brukes mot perioden fram til relevant dato.
`on_date` er en vurderingsdato, ikke full rekonstruksjon av en tidligere database.

UNVERIFIED og DISPUTED med ikke-tomt scope er plausible uansett om perioden er
historisk, aktuell eller fremtidig. Future CONFIRMED er også plausibelt.
REJECTED/SUPERSEDED er ikke gjenværende plausible grunnlag, men kan bære historikk.

## Read-only API og felles tilbakeføringsregel

`management_state(managed, on_date=None)` bruker
`management_states(managed_records, on_date=None)`, som gir resultater keyed på
ManagedRecording UUID. Begge leser bare; ingen saves eller locks ved vanlig GET.
Objektene representerer den lastede medlemskapssnapshoten. Kallere som trenger
ny lagret status må laste medlemskapet på nytt.

`evaluate_management_state(managed, claims, local_organization=..., on_date=...)`
er ren evaluering. LibraryEntry, territorier og beslutninger må være lastet;
manglende prefetchede territorier/beslutninger avvises fremfor skjult N+1.

ManagementState inneholder:

- `status`: effektiv PENDING/ACTIVE/INACTIVE, eller None ved usikker legacy;
- `stored_status`;
- `has_current_basis`, `has_confirmed_history`;
- `has_unverified_basis`, `has_disputed_basis`, `has_future_confirmed_basis`;
- `history_uncertain`, `requires_follow_up`;
- avledet `has_pending_basis` (6A-kompatibilitet: ubekreftet/bestridt/fremtidig),
  `has_plausible_basis`, `needs_reconciliation`, `can_return_to_music_library`.

Unverified/disputed-flaggene inkluderer alle perioder, mens current følger 6C.
Follow-up gjelder usikker historikk, lokale disputed-grunnlag eller PENDING uten
plausibelt grunnlag. Normal UNVERIFIED-onboarding trenger ikke ekstra flagg.

`return_to_music_library()` bruker `can_return_to_music_library`. Dette krever
lagret og effektiv PENDING, ingen current, ingen bekreftet historikk, ingen
plausible grunnlag og ingen usikkerhet. Eksisterende permission, begrunnelse,
Recording-lås og audit SourceRecord beholdes. Recording, MusicLibraryEntry,
claims, beslutninger og provenance bevares. Ingen reconciliation kaller return.
Manglende lokal organisasjon sperrer evaluering/tilbakeføring med valideringsfeil;
enkelt-refresh beholder tidligere no-op ved manglende konfigurasjon.

## Materialisering og concurrency

`refresh_management_status(recording, on_date=None)` låser Recording før
ManagedRecording, leser grunnlaget på nytt og skriver bare et sikkert statusavvik.
Returnert state beskriver evalueringen før eventuell materialisering.
Ingen medlemskap opprettes/slettes, ingen claims/beslutninger oppdateres, og det
opprettes ingen falsk rettighetsevidens. Vanlig CanonicalModel-revisjon beholdes.

`reconcile_management_statuses(on_date=None, batch_size=200, dry_run=False)`
går gjennom eksisterende medlemskap med UUID-keyset-pagination og 1–500 per batch.
Hver batch har egen transaksjon: lås Recordings i UUID-rekkefølge, deretter
medlemskap, så last claims/territorier/beslutninger. Dette følger eksisterende
decision-/superseding-/return-låserekkefølge. Medlemskap leses igjen etter lås;
et medlemskap som ble tilbakeført i mellomtiden gjenopprettes aldri.

Grunnlaget hentes med tre queries per batch og lokal organisasjon én gang per
kjøring. Read-only batch-API bruker fire queries med allerede lastet LibraryEntry,
eventuelt én ekstra samlet query for disse. Canonical saves koster per faktisk
endret rad; dette beholdes for integritet, ikke erstattet med bulk_update.
Ingen filhistorikk eller hele katalogen lastes i minnet.

Resultatet har `on_date`, `dry_run`, `examined`, `needs_reconciliation`, `updated`,
`uncertain` og `requires_follow_up`. Dry-run bruker samme låser og vurdering,
men skriver ingenting; updated er da 0. En kjøring kan gjenopptas ved å kjøre
kommandoen igjen hvis en senere batch feiler. Allerede riktige rader er no-op.
Ny onboarding under kjøring kan tas med denne eller neste kjøring.

Status-only saves bruker eksisterende `suppress_automatic_flac_sync()` innenfor
en avgrenset context. PENDING/ACTIVE/INACTIVE har samme 6B-katalogvern.
Opprettelse av medlemskap, katalogendringer og rights-beslutninger beholder sine
ordinære signaler. Signal-/authority-reglene i 6B er ikke redesignet.

## Kommando og drift

Fra repository-root, med prosjektets vanlige databasekonfigurasjon:

```powershell
.\.venv\Scripts\python.exe manage.py reconcile_management_statuses --dry-run
.\.venv\Scripts\python.exe manage.py reconcile_management_statuses
.\.venv\Scripts\python.exe manage.py reconcile_management_statuses --dry-run --on-date 2027-01-01 --batch-size 200
```

Uten dry-run materialiserer også `--on-date` den angitte datoens status. Vanlig
daglig drift skal utelate datoen. Bruk dry-run ved inspeksjon av andre datoer.
Ekstern drift kan senere kjøre kommandoen daglig; ingen scheduler er installert.
Brukerens lokale katalogdatabase oppdateres ikke som del av implementasjonstestene.

Eksisterende GUI-lister viser fortsatt materialisert status. Det nye read-only
API-et er tilgjengelig for senere GUI-integrasjon; ingen skriver status på GET.
Manglende planlagt kjøring kan derfor gi eldre lagret status i disse listene.

## Verifikasjon og videre grense

Kontraktstester ligger i `managed_music/test_scope_lifecycle.py` sammen med
eksisterende 6A-tester. De dekker datooverganger, historikk, tvister, Release-scope,
return, read-only state, idempotens, sync-suppression og konstant query-antall.
`test_lifecycle_locking.py` verifiserer samtidige rights-beslutninger og eksplisitt
tilbakeføring mot batchens Recording-lås på PostgreSQL i CI; SQLite hopper over
disse faktiske row-lock-testene.

6A/6B/6C-, ingest-, library-, GUI- og Workbench-regresjon kjøres på SQLite.
PostgreSQL 17.11/full-suite er autoritativ i GitHub Actions. Ingen lokal PostgreSQL.

Lokal kontroll på Python 3.14.3: 345 relevante tester kjørt, 343 bestått og de to
PostgreSQL-låsetestene hoppet over. Black 26.5.1 (79 tegn) på alle sju nye/endrede
Python-filer, `manage.py check`, `makemigrations --check --dry-run` og
`git diff --check` bestått. Ingen schemaendring oppdaget.

6E kan bruke samme state til kontrollert onboarding og tilbakeføring, inkludert
forståelig håndtering av usikker legacy-historikk. Endelig Rights/Managed Music
GUI, 6I-arbeidskø, kontraktsanalyse og konkret bruksklarering bygges ikke her.
Lokal organisasjonsidentitet må fortsatt ikke byttes ukritisk; 6D innfører ingen
ny operatørhistorikk eller gjetning om tidligere P7-identitet.
