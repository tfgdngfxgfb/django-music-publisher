# GUI v2 – isolert arbeidsområde

- Branch: `feature/gui-v2-prototype`
- URL: `http://127.0.0.1:8000/v2/`
- Standardmodus: lesing fra den valgte databasen; alle POST-handlinger er blokkert.
- Trygg prøvebruk: `run-p7-empty-test.cmd` og `run-p7-gui-v2-test.cmd`
  bruker begge `.local/p7-empty-test.sqlite3`.
- GUI-v2-startskriptet er nå et alias og laster ikke kunstige GUI-v2-data.
  Den tidligere `.local/gui-v2-test.sqlite3` brukes ikke.
- Filskriving: GUI v2 har ingen scan-, ingest-, writeback- eller sync-endepunkter.
  Sporlagring undertrykker også automatisk FLAC-synkroniseringskø.
- Innlogging og visningstillatelser er de samme som i Workbench.

Templates, CSS og JavaScript ligger under `gui_v2`. Ingen domenemodeller eller
migrations er lagt til. Dagens `/`, `/arbeid/`, admin og Workbench er uendret.

## Starte prøveområdet

```powershell
run-p7-gui-v2-test.cmd
```

Bruk `run-p7-empty-test.cmd` eller `run-p7-gui-v2-test.cmd`. Begge åpner GUI v2
mot samme testkatalog. `-Reset` arkiverer testdatabasen og bygger en ny, helt
tom database. Administratorpassord kan angis med `-AdminPassword` og lagres
ikke i skriptet.
