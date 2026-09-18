# GUI v2 – primær arbeidsflate

- Primær URL: `http://127.0.0.1:8000/`. Den tidligere `/v2/`-adressen virker
  fortsatt.
- GUI v1/Workbench er sekundær på `/arbeid/`. Eksisterende dyp-lenker under
  `/arbeid/` virker fortsatt.
- GUI v2 leser og skriver mot databasen som er valgt for prosessen. Skriving er
  aktivert som standard, men krever relevante Django-rettigheter og kan slås
  av med `GUI_V2_WRITES_ENABLED=false`. Filskriving styres separat.
- Lokal start: `run-p7-gui-v2-test.cmd` bruker den vedvarende databasen
  `.local/p7-empty-test.sqlite3`.
- GUI-v2-startskriptet er nå et alias og laster ikke kunstige GUI-v2-data.
  Den tidligere `.local/gui-v2-test.sqlite3` brukes ikke.
- Musikkarkivet kan lese én allerede registrert radio-FLAC på nytt gjennom den
  eksisterende ingest-tjenesten. Handlingen starter ikke en full skann og følger
  etablerte autoritetsregler. Direkte start av OneTagger er ikke aktivert;
  full filsti kan kopieres og åpnes manuelt i OneTagger.
- Sporlagring undertrykker automatisk FLAC-synkroniseringskø; DB→FLAC styres
  fortsatt av egne autoritets- og filskrivingsregler.
- Innlogging og visningstillatelser er de samme som i Workbench.

Templates, CSS og JavaScript ligger under `gui_v2`. Ingen domenemodeller eller
migrations ble lagt til ved omleggingen. Admin og Workbench er fortsatt
tilgjengelige.

Musikkarkiv-tabellen støtter pil opp/ned, Enter for full visning og Escape for å
lukke detaljpanelet. Søk, filtre, sortering, side og valgt rad følger eksplisitte
returlenker fra innspillings- og utgivelsesdetaljer.

En åpen utgivelse er samlet i ett GUI-v2-arbeidsområde med fanene **Sporliste**,
**Utgivelsesdetaljer**, **Filer og kilder** og **Rettigheter**. Et kompakt cover
og utgivelsens identitet ligger fast i overskriften. Utgivelsesdetaljer kan
redigeres i sin egen fane. Hovedartist vises avledet fra sporenes
artistkrediteringer; dagens kanoniske `Release` har ingen egen hovedartistrelasjon.

Nye utgivelser kan opprettes direkte fra GUI v2 og åpnes straks i sportabellen.
Filer og kilder viser eksisterende cover, dokumentreferanser, filplasseringer og
kildepåstander når brukeren har tilgang. Autoriserte rettighetsbrukere kan i
Rettigheter-fanen velge hvilke av utgivelsens underliggende innspillinger som
skal få samme rettighetsgrunnlag. Rettighetene lagres fortsatt på `Recording`,
ikke på `Release`.

Høyrepanelet i Sporliste gjelder bare valgt spor og innspilling: sporplassering,
artist, ISRC, kontrollbehov, andre utgivelsesforekomster og filkoblinger. Det
brukes ikke som navigasjon eller handlingsmeny for utgivelsen.

Sportabellen støtter piltaster, `Enter`, `Tab`/`Shift+Tab`, flercelleinnliming,
`Ctrl+Z`, `Ctrl+D` (fyll ned) og `Ctrl+Enter` (lagre). Den kan sette inn,
duplisere, flytte og renummerere rader. Ulagrede endringer lagres lokalt i
nettleseren per utgivelse og kan gjenopprettes; databasen endres først når
«Lagre sporlisten» brukes. Kontrollkolonnen viser konkrete mangler og kan
filtrere listen til spor som trenger arbeid.

## Starte prøveområdet

```powershell
run-p7-gui-v2-test.cmd
```

`run-p7-gui-v2-test.cmd` åpner GUI v2 mot den vedvarende testkatalogen. `-Reset`
arkiverer testdatabasen og bygger en ny, helt tom database.
Administratorpassord kan angis med `-AdminPassword` og lagres ikke i skriptet.
