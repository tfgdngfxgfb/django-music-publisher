# GUI v2 – implementasjon

## Omfang

Referansene `docs/gui-v2/references/01_musikkarkiv.png` og
`03_utgivelse_spor.png` er brukt som visuell retning. Nettleserens ramme og
oppdiktede statusbetydninger fra illustrasjonene er ikke kopiert.

Musikkarkivet har nå en tett, paginert arbeidsliste med reelle søk og filtre,
flerverdi-filtrering for kanal/målgruppe (`minst én` eller `alle`), bevart
spørringskontekst, aktive filterchips og et lukkbart, skalerbart detaljpanel.
Detaljpanelet åpnes først når brukeren velger en innspilling. Hele tabellraden
kan velges, mens tittellenken fortsatt gir vanlig tastaturnavigasjon.
Ingen tabellkolonner er fryst ved horisontal rulling. Musikkarkiv-radene er
komprimert for å vise flest mulig innspillinger uten å blande sammen verdiene.
Panelets åpne/lukkede tilstand og bredde huskes lokalt. Tabellen viser tittel,
artist, ISRC, varighet, sjanger, språk, `Energy`, kanaler, målgrupper, filstatus,
forvaltning og siste innlesing som separate sammenlignbare kolonner. `Energy`
er eneste energiverdi. Innlesing fra filer peker til den eksisterende kontrollerte
Workbench-flyten; GUI v2 starter ikke ingest.

Utgivelsesflaten har kontekstnavigasjon, kompakt utgivelseshode og en reell
sporgrid. Den støtter nye rader, fem rader om gangen, tabulatorseparert
innliming direkte fra aktiv celle, lokal angre, Recording-autocomplete,
dublettkandidater,
cellefeil, ulagret-status og dobbeltsendingsvern. Hele sporlisten lagres i én
transaksjon. ReleaseTrack-felt og felles Recording-metadata holdes atskilt;
felles metadata krever `Oppdater felles`.

Sportabellen er keyboard-first: piltaster flytter aktiv celle, `Enter` åpner
eller bekrefter celleredigering, `Tab`/`Shift+Tab` går til neste/forrige celle,
`Escape` avbryter cellen, `Ctrl+C`/`Ctrl+V` kopierer og fordeler tabelltekst,
`Ctrl+Z` angrer siste celleendring, innliming eller nye rad, `Ctrl+Shift+N`
oppretter en rad og `Ctrl+Enter` lagrer. Venstre/høyre pil redigerer tekst når
cellen er i redigeringsmodus. Recording-forslag åpnes mens brukeren skriver og
kan velges med pil/Enter før Tab fortsetter arbeidsflyten. Snarveiene vises i
arbeidsflaten. Aktiv rad oppdaterer
sporinspektøren, og detaljpanelene kan lukkes, åpnes og breddejusteres med mus
eller tastatur.

Avspillingsfeltet er synlig, men deaktivert fordi prosjektet ikke har en trygg
avspillingsbackend. Det står derfor eksplisitt `Avspilling ikke tilkoblet`.

## Sikker prøvebruk

Standardinnstillingen `GUI_V2_WRITES_ENABLED=false` blokkerer alle v2-skrivekall
på serversiden. Teststarteren aktiverer lagring bare med:

- database: `.local/gui-v2-test.sqlite3`
- filrot: `.local/gui-v2-files`

Ingen v2-rute kan skanne, importere, flytte eller skrive lydfiler. Automatisk
FLAC-sync undertrykkes rundt den isolerte sportransaksjonen. Referanser til
filer i testdataene er kun databaseposter.

## Verifikasjon

- `manage.py check`: bestått.
- `makemigrations --check --dry-run`: ingen endringer.
- målrettet GUI/catalogue-suite: 28 tester bestått.
- full regresjon: 244 tester bestått.
- Ruff for endrede Python-filer: bestått.
- faktisk nettleserprøve: søk + flere filtre, filterchips, radvalg, lukking og
  åpning av detaljpanel, bevart returtilstand, Tab/piler/Enter/Escape,
  3 × 4 TSV-innliming, `Ctrl+Z`, autocomplete med pil/Enter, isolert
  atomisk lagring og ugyldig varighet uten tap av øvrige endringer.
- original FLAC-mappe: manifest før/etter var identisk.
- isolert GUI-v2-filrot: 0 filer etter lagringsprøven.

Skjermbilder fra den faktiske prøven ligger i `docs/gui-v2/screenshots/`:

- `musikkarkiv-panel-apent.png`
- `musikkarkiv-panel-lukket.png`
- `sportabell-normal.png`
- `sportabell-aktiv-celle.png`
- `sportabell-autocomplete.png`
- `sportabell-valideringsfeil.png`

## Begrensninger

Avspilling og FLAC-innlesing ligger fortsatt i eksisterende system. GUI v2 har
foreløpig ikke egne flater for forvaltet musikk, rettigheter, filer eller
kontroll. Disse toppmenypunktene går til dagens Workbench. GUI v2 erstatter
ikke standardgrensesnittet før brukerprøve og godkjenning.
