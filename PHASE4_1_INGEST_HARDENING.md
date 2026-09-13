# Fase 4.1 — FLAC-ingest hardening

## Reelt testkorpus og observerte former

Den tilgjengelige lokale testmappen inneholdt 1 842 FLAC-filer. En komplett
skrivebeskyttet skann mot en kopi av katalogdatabasen tok 39 sekunder. Ingen
lydfiler ble endret.

- Alle filer hadde `TITLE`, `ARTIST`, `ALBUM`, `TRACKNUMBER` og `ISRC`.
- `ARTIST`, `GENRE` og `COMMENT` forekommer som flerverdier. Opptil fire
  sjangerverdier og fire kanalverdier ble observert.
- Kanal, målgruppe, språk og kjønn ligger hovedsakelig som
  `TXXX:<felt> - <verdi>` i `COMMENT`. Direkte `KANAL`, `TARGET` og `LANGUAGE`
  forekommer også.
- `RATING` bruker OneTagger-skalaen 20/40/60/80/100. `RATING WMP` bevares som
  råtag, men er ikke katalogautoritet.
- `DATE` er hovedsakelig full dato. Observerte `TRACKNUMBER` og `DISCNUMBER`
  er heltall; parseren støtter også `03` og `3/12`.
- `P7UUID` finnes i 1 643 filer. Ukjente tags og alle råverdier bevares.

## Bugregister

| Problem | Årsak | Rettet | Regresjonstest / begrensning |
|---|---|---|---|
| Kanal og målgruppe manglet | StationPlaylist lagret TXXX-data i `COMMENT` | Adapteren leser embedded TXXX og beholder rådata | Direkte adaptertest og reell filprøve |
| Energy ga unødvendig konflikt | OneTagger bruker 20-trinn | 20/40/60/80/100 mappes til 1–5 | Test av alle fem nivåer |
| Flere sjangre ble redusert til én | `GENRE` ble lest som enkeltverdi | Alle verdier bevares og lagres semikolonseparert i dagens felt | Test med to sjangre; feltet er fortsatt tekst |
| Én ødelagt/utilgjengelig fil kunne stoppe skann | Lesing, stat og hash var ikke isolert | Forventede filfeil registreres per fil | Test med ødelagt FLAC og lesbar nabofil |
| Fil kunne endres under skann | Ingen kontroll før/etter lesing | Størrelse og nanosekund-mtime kontrolleres | Ustabil fil får «Prøv igjen» |
| Fil kunne forsvinne før import | Preview ble brukt uten ny kontroll | Hash, størrelse og mtime verifiseres før canonical writes | Nabofil importeres; forsvunnet fil isoleres |
| Én datakonflikt kunne stoppe resten av apply | Batch manglet forventet feilgrense | Hver fil bruker egen transaksjon; forventede konflikter logges på raden | Batchtesten bevarer andre filer |
| Store resultater var tunge å kontrollere | Få filtre og ny skann krevde ny start | Nye/endrede/uendrede/advarsler/konflikter/feil kan filtreres; hele, feilede eller valgte filer kan skannes på nytt | Workbench- og permissionstester |
| To databaseoppslag per filplassering | Plassering ble slått opp inne i løkken | Aktuelle plasseringer lastes én gang, med chunking for valgte filer | Reell skann: 1 842 filer på 39 sekunder |
| Anvendte FLAC-verdier fylte listen «Uverifiserte opplysninger» | Kildepåstanden beholdt standardstatus etter at autoritetsregelen allerede hadde anvendt verdien | Nye anvendte FLAC-påstander bekreftes med beslutningshistorikk. Eldre FLAC-påstander kan bekreftes eksplisitt fra Kontroll | Test skiller FLAC-metadata fra andre kilder og rettighetskrav |

Automatisk bekreftelse gjelder bare metadata som ingest faktisk anvender etter
autoritetsreglene: katalogmetadata for ikke-forvaltet musikk og radiometadata
for alle innspillinger. FLAC-ingest kan aldri bekrefte eierskap eller andre
rettighetskrav.

## Resultat og kjente begrensninger

Siste realistiske skann ga 94 nye, 19 eksisterende treff, 944 endrede, 686
uendrede, én advarsel, 99 konflikter og ingen lesefeil. Konfliktene var
handlingsbare: 86 usikre metadata-treff, 11 P7UUID/ISRC-konflikter og to
ikke-standardiserte språkverdier (`Afrikanske språk`). De skal ikke slås
sammen eller godkjennes automatisk.

Skanningen kjøres fortsatt synkront. Knappen låses og viser arbeidsstatus, men
det finnes ikke live prosentvis fremdrift før svaret er ferdig. Permanent
watcher, køsystem og automatisk bakgrunnsskann er fortsatt utsatt. En tom
database vil med hensikt avvise P7UUID-er som peker på en annen katalogbase.

## Verifikasjon

De målrettede FLAC-ingesttestene dekker 30 tilfeller, blant annet ødelagte og
tagløse filer, lange felt, endring under lesing, forsvunnet fil, idempotens,
autoritetsregler, tillatelser og bekreftelse av anvendte FLAC-opplysninger.
`manage.py check` og migrasjonskontrollen er uten feil. Den avsluttende samlede
regresjonskjøringen bestod: 219 tester på SQLite. DMP-koden ble ikke endret og
inngår i denne kjøringen. PostgreSQL fullsuite ble ikke gjentatt fordi endringen
bare har en databaseuavhengig choice-migrasjon og ingen PostgreSQL-spesifikk kode.
