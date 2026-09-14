# GUI v2 – isolert arbeidsområde

- Branch: `feature/gui-v2-prototype`
- URL: `http://127.0.0.1:8000/v2/`
- Standardmodus: lesing fra den valgte databasen. Skriving krever både
  `GUI_V2_WRITES_ENABLED=1` og relevante Django-rettigheter.
- Trygg prøvebruk: `run-p7-empty-test.cmd` og `run-p7-gui-v2-test.cmd`
  bruker begge `.local/p7-empty-test.sqlite3`.
- GUI-v2-startskriptet er nå et alias og laster ikke kunstige GUI-v2-data.
  Den tidligere `.local/gui-v2-test.sqlite3` brukes ikke.
- Musikkarkivet kan lese én allerede registrert radio-FLAC på nytt gjennom den
  eksisterende ingest-tjenesten. Handlingen starter ikke en full skann og følger
  etablerte autoritetsregler. Direkte start av OneTagger er ikke aktivert;
  full filsti kan kopieres og åpnes manuelt i OneTagger.
- Sporlagring undertrykker automatisk FLAC-synkroniseringskø i prototypen.
- Innlogging og visningstillatelser er de samme som i Workbench.

Templates, CSS og JavaScript ligger under `gui_v2`. Ingen domenemodeller eller
migrations er lagt til. Dagens `/`, `/arbeid/`, admin og Workbench er uendret.

Musikkarkiv-tabellen støtter pil opp/ned, Enter for full visning og Escape for å
lukke detaljpanelet. Søk, filtre, sortering, side og valgt rad følger eksplisitte
returlenker fra innspillings- og utgivelsesdetaljer.

## Starte prøveområdet

```powershell
run-p7-gui-v2-test.cmd
```

Bruk `run-p7-empty-test.cmd` eller `run-p7-gui-v2-test.cmd`. Begge åpner GUI v2
mot samme testkatalog. `-Reset` arkiverer testdatabasen og bygger en ny, helt
tom database. Administratorpassord kan angis med `-AdminPassword` og lagres
ikke i skriptet.
