# GUI v2 – implementasjon

## Omfang

Referansene `docs/gui-v2/references/01_musikkarkiv.png` og
`03_utgivelse_spor.png` er brukt som visuell retning. Nettleserens ramme og
oppdiktede statusbetydninger fra illustrasjonene er ikke kopiert.

Musikkarkivet har nå en tett, paginert arbeidsliste med reelle søk og filtre,
flerverdi-filtrering for kanal/målgruppe (`minst én` eller `alle`), bevart
spørringskontekst og et lukkbart, skalerbart detaljpanel. Panelet viser radio,
filer, utgivelser, identifikatorer og forvaltning. `Energy` er eneste
energiverdi. Innlesing fra filer peker til den eksisterende kontrollerte
Workbench-flyten; GUI v2 starter ikke ingest.

Utgivelsesflaten har kontekstnavigasjon, kompakt utgivelseshode og en reell
sporgrid. Den støtter nye rader, fem rader om gangen, tabulatorseparert
innliming, tastaturnavigasjon, eksisterende Recording-søk, dublettkandidater,
cellefeil, ulagret-status og dobbeltsendingsvern. Hele sporlisten lagres i én
transaksjon. ReleaseTrack-felt og felles Recording-metadata holdes atskilt;
felles metadata krever `Oppdater felles`.

Sportabellen er keyboard-first: `Tab` går mellom felt, `Enter` og
`Shift+Enter` går til samme kolonne på neste eller forrige rad, `Ctrl` +
piltaster beveger seg i rutenettet, `Ctrl+Shift+N` oppretter en rad og
`Ctrl+Enter` lagrer. Snarveiene vises i arbeidsflaten. Aktiv rad oppdaterer
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
- målrettet GUI/catalogue-suite: 19 tester bestått.
- full regresjon: 241 tester bestått.
- Ruff for endrede Python-filer: bestått.
- faktisk nettleserprøve: innlogging, søk + kanalfilter, valg/detaljpanel,
  overgang til utgivelse, TSV-innliming, ugyldig varighet, bevart rad,
  korrigering og atomisk lagring. Mørkt og lyst tema, smalere bredde,
  paneltabber/lukking, breddejustering og tastaturnavigasjon i sporgrid ble
  kontrollert.
- original FLAC-mappe: manifest før/etter var identisk.
- isolert GUI-v2-filrot: 0 filer etter lagringsprøven.

Skjermbilder fra den faktiske prøven ligger i `docs/gui-v2/screenshots/`:

- `musikkarkiv-dark.png`
- `utgivelse-spor-dark.png`
- `musikkarkiv-light-narrow.png`

## Begrensninger

Avspilling og FLAC-innlesing ligger fortsatt i eksisterende system. GUI v2 har
foreløpig ikke egne flater for forvaltet musikk, rettigheter, filer eller
kontroll. Disse toppmenypunktene går til dagens Workbench. GUI v2 erstatter
ikke standardgrensesnittet før brukerprøve og godkjenning.
