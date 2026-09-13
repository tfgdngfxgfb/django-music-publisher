# Fase 2.5 — arbeidsgrensesnitt

## Levert

Det nye `workbench`-laget gir innloggede katalogbrukere en felles norsk programramme med lyst/mørkt tema og arbeidsflater for Musikkarkiv, Forvaltet musikk, Utgivelser, Artister/personer, Kontroll, Filer og Hjelp. Menyer, handlinger og direkte adresser bruker eksisterende Django-tillatelser på serversiden. Django-admin og musikkforlaget er fortsatt tilgjengelig etter tilgang.

Programrammen bruker en fast, ikonbasert arbeidsmeny på større skjermer og en tastaturvennlig uttrekksmeny på mobil. Kort, tabeller, skjemaer, detaljfaner og statusmerker deler samme visuelle system i lyst og mørkt tema. Dette er et rent grensesnittlag; katalogmodellene og domenereglene er uendret.

Musikkarkiv, forvaltning og utgivelser har paginerte søke-/filterlister. Innspillingsdetaljen samler **Oversikt · Radio · Utgivelser · Medvirkende · Filer · Kilder og historikk**. Søk og sidevalg kan returneres til etter detaljarbeid. Utgivelsesdetaljen prioriterer sporlisten og registrerer opptil fem utfylte spor atomisk gjennom fase-2-tjenesten. Innspillingssøk lastes dynamisk og begrenses til 20 treff; hele katalogen legges ikke i skjemaet.

Forvaltet musikk bruker fortsatt samme Recording og MusicLibraryEntry. Registreringen krever uttrykkelig administratorhandling og vises som **Registrert i Forvaltet musikk**. Den uttrykker ikke eierskap.

## Kilder og filer

«Bekreft», «Bruk som gjeldende verdi» og «Bekreft og bruk» er separate handlinger. Anvendelse støttes bare for `Recording.title` og `Recording.language`. Mappingen er eksplisitt, modellvalidering brukes, og forventet Recording-revisjon hindrer stille overskriving. `AppliedMetadataChange` lagrer kildepåstand, bruker, tidspunkt, felt, før-/etterverdi og revisjoner. Original kildeverdi og andre påstander bevares. Korrigering oppretter en erstattende påstand.

FileAsset vises som registrert referanse. FileLocation skiller **Ikke kontrollert** fra **Kontrollert plassering** gjennom feltet `verification_status`. En lagret sti merkes aldri automatisk som kontrollert. NAS-stier kan kopieres som tekst; validerte Google Drive-URL-er kan åpnes. Arbeidsflaten flytter, skanner eller endrer ingen filer.

## Migrasjoner og sikkerhetsgrenser

- `provenance.0002_appliedmetadatachange`: uforanderlig auditspor for anvendte kildeverdier.
- `media_assets.0002_filelocation_verification_status_and_more`: eksplisitt kontrollstatus og indeks for filplassering.

Arbeidsgrensesnittet tilbyr ingen fysisk sletting. Alle endrende POST-er krever CSRF og relevante modellrettigheter; forvaltningsregistrering krever superbruker. Returadresser godtas bare som trygge interne adresser.

## Backup og gjenoppretting

Stopp skrivende prosesser under SQLite-kopi. Test en separat kopi slik:

```powershell
Copy-Item db.sqlite3 .local\rights-backup.sqlite3
$env:DATABASE_URL = 'sqlite:///C:/full/path/to/project/.local/rights-restore-test.sqlite3'
Copy-Item .local\rights-backup.sqlite3 .local\rights-restore-test.sqlite3
.\.venv\Scripts\python.exe manage.py check
```

PostgreSQL testes med `pg_dump --format=custom`, ny tom testdatabase og `pg_restore --exit-on-error`. Databasebackupen inneholder katalogposter og filreferanser, men **ikke NAS-, Drive- eller lydfilene**. Disse må sikres separat.

Prøven 13. september 2026 gjenopprettet begge backendene i separate databaser. SQLite-kopien inneholdt 3 innspillinger og 45 migrasjoner; PostgreSQL-dumpen inneholdt 4 innspillinger, 1 forvaltningsregistrering og 45 migrasjoner. `manage.py check` passerte mot begge gjenopprettingene.

## Verifikasjon

- SQLite: ren migrering, oppgradering fra fase 2, 153/153 tester, systemkontroll og HTTP-smoke-test bestått.
- PostgreSQL: ren migrering, oppgradering fra fase 2, 153/153 tester, systemkontroll og HTTP-smoke-test bestått.
- DMP: 80/80 egne tester bestått; de inngår også i fullkjøringene.
- Nettleser: innlogging, filtrert utgivelsesliste, sporregistrering, gjenbruk av innspilling, radioredigering, kildekonflikt, «Bekreft og bruk», filoversikt og bevart retur til søk er utført mot isolerte data. Lyst og mørkt tema er visuelt kontrollert; en responsiv navigasjonsfeil ble rettet.

## Avgrensning og neste tilkoblingspunkt

Eierskap, andeler, territorier og avtalegrunnlag er ikke implementert. Neste fase kan koble disse til stabil Recording-UUID og eksisterende forvaltningsregistrering uten å gjøre radiometadata eller filtilstedeværelse til eierskapsbevis. OCR, OneTagger-synkronisering, Orchard, TuneTracker, programarkiv og publisering er fortsatt utsatt.


### Catalogue inspector (2026-09-13)
- Musikkarkivet now combines a paginated list with a selected-recording inspector, overview/radio/source tabs, duration and release information. Search, selection and return context remain in the URL.
- Genre/language filters and allowlisted title/recent sorting. Existing edit forms and permissions are reused. No model or migration changes.
- Registered recording cover images can be previewed from the configured NAS root. Requires file and location read permissions; resolved paths must remain inside the root. Only bounded JPEG/PNG/WebP rasters are rendered to a private JPEG response. Missing covers use an explicit placeholder. No remote downloads, scanning, audio edits, or verification-status changes. Release-cover fallback is not included.
- SQLite: full suite 155 passed, including DMP. Inspector filtering, permissions and confined image reads covered. Django system check passed. Actual browser login, record selection, radio tab, light and dark screenshots checked on isolated test data.
- PostgreSQL 17.11: full suite 155 passed, including DMP; system checks clean. No schema changes requiring an upgrade migration. Mobile layout was not reverified in this increment.

### Fast demo-datasett

Kommandoen ``load_demo_data`` oppretter et deterministisk, fiktivt datasett og
små testfiler. ``run-p7-demo.cmd`` bruker en separat SQLite-database og separat
filrot under ``.local``. Kommandoen er idempotent og sperret når ``DEBUG=false``.
Demoen omfatter katalog, radiometadata, forvaltning, kilder, konflikt, mulig
dublett og filplassering, men oppretter ingen eierskapsopplysninger.
Kommandoen og idempotensen er testet i full testmatrise: 157 tester bestått på
både SQLite og PostgreSQL, med ren Django-systemkontroll.
