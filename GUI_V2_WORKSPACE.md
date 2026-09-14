# GUI v2 – isolert arbeidsområde

- Branch: `feature/gui-v2-prototype`
- URL: `http://127.0.0.1:8000/v2/`
- Standardmodus: lesing fra den valgte databasen; alle POST-handlinger er blokkert.
- Trygg prøvebruk: `run-p7-gui-v2-test.cmd` bruker bare
  `.local/gui-v2-test.sqlite3` og `.local/gui-v2-files`.
- Testdata: `load_gui_v2_test_data` oppretter oppdiktede innspillinger,
  utgivelser, radioklassifisering og filreferanser. Ingen lydfiler opprettes.
- Filskriving: GUI v2 har ingen scan-, ingest-, writeback- eller sync-endepunkter.
  Sporlagring undertrykker også automatisk FLAC-synkroniseringskø.
- Innlogging og visningstillatelser er de samme som i Workbench.

Templates, CSS og JavaScript ligger under `gui_v2`. Ingen domenemodeller eller
migrations er lagt til. Dagens `/`, `/arbeid/`, admin og Workbench er uendret.

## Starte prøveområdet

```powershell
run-p7-gui-v2-test.cmd
```

Bruk `run-p7-gui-v2-test.cmd -Reset` for å arkivere den forrige GUI-v2-databasen
og bygge en ny. Administratorpassord kan angis med `-AdminPassword` og lagres
ikke i skriptet.
