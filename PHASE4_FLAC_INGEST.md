# Fase 4 — automatisk FLAC-innlesing

## Autoritet og tagger

- Ikke-forvaltet musikk uten bekreftet lokalt mastereierskap bruker normalt
  FLAC som autoritet for katalog- og radiometadata.
- Forvaltet eller bekreftet lokalt eid musikk bruker databasen som autoritet
  for katalogmetadata. Avvik i filen overskriver ikke katalogen.
- FLAC/OneTagger-arbeidsflyten er alltid autoritet for radiosjanger,
  radiospråk, Energy, kanal, målgruppe og kjønn. `RATING` betyr P7 Energy.
  Både direkteverdier `1–5` og OneTaggers lagring `20/40/60/80/100` tolkes
  som Energy `1–5`.
- Writeback har en eksplisitt allowlist for katalogtags. Radiotags og ukjente
  tags bevares og kontrolleres etter skriving. `P7UUID` gir stabil filkobling.

`flac_ingest.adapter` samler dagens tag-aliaser og holder StationPlaylist- og
OneTagger-representasjon ute av domenemodellen. Alle rå Vorbis Comments og
tekniske lyddata lagres i `SourceRecord.raw_payload`.

## Ingest, matching og filer

Workbench har arbeidsflyten **Les inn fra musikkarkiv → Skann → Forhåndsvis →
Bruk/importer**. Bare konfigurerte mapper under `P7_MUSIC_ROOT` kan velges.
Recording treffes i rekkefølgen P7UUID, ISRC, konservativ metadata og ellers ny
Recording. Konflikter beholdes som `FlacIngestItem` og merges aldri automatisk.
En autorisert bruker kan åpne **Kontroller og rett**, korrigere den tolkede
verdien, velge ny eller eksisterende Recording og godkjenne raden før import.
Råtags endres ikke. Bruker, tidspunkt og kontrollmerknad logges.

Release og ReleaseTrack opprettes bare når album og en sikker strekkode eller
katalognummer finnes. Manglende releasegrunnlag hindrer ikke Recording og
MusicLibraryEntry. Uavklarte krediterte navn bevares uten å opprette en falsk
juridisk Party.

Radio-FLAC kobles til `FileAsset`, en valgfri entydig `ReleaseTrack` og en
logisk `FileLocation`. Full SHA-256 har historikk i `FileChecksum`, men er ikke
filens eller Recordingens identitet. Uendrede filer hoppes over ved ny skann.

## Synkronisering og migrasjoner

Endringer i autoritative katalogobjekter markerer radio-FLAC som ventende.
Vanlige Workbench-endringer prøver writeback straks; manglende filer og feil
beholder databaseendringen og kan prøves igjen med `sync_flac_tags`. Lyddata
transkodes aldri.

- `catalogue.0005`: krediteringsroller for komponist, tekstforfatter og
  arrangør; kildekobling og uavklart kreditert navn.
- `music_library.0002`: normaliserte `Channel` og `TargetAudience`, flerverdi,
  trygg flytting av fase-2-data og `rating` til Energy.
- `media_assets.0003`: ReleaseTrack-kontekst, syncstatus, checksumhistorikk,
  sti-integritet og syncindeks.
- `flac_ingest.0001`: persistente skanninger, kontrollrader og synklogg.
- `flac_ingest.0002`: revisjonsspor for manuell kontroll av innlesingsrader.

## Verifikasjon og begrensninger

Målrettede tester dekker parsing, råtags, multiverdi, tekniske data,
autoritetsregler, P7UUID/ISRC-konflikt, idempotens, releasebygging,
filgjenbruk, writeback, beskyttede radiotags, utilgjengelig fil og permissions.
Det deterministiske demosettet inneholder en kort egenprodusert FLAC med
stillhet og fiktive tags.

SQLite-oppgradering fra fase 3.1 og datamigrering av tidligere kanal,
målgruppe og rating er kontrollert. De målrettede fase-4-testene passerer,
hele regresjonssuiten passerer med 198 av 198 tester, `manage.py check` og
migrasjonskontrollen er rene. Nettleserprøven bekreftet innlogging med retur
til forespurt side, skanning, forhåndsvisning, bruk/import, beskyttet
katalogverdi, writeback og idempotent ny skanning. En malfeil som bare oppstod
ved forhåndsvisning av en uendret fil ble funnet i prøven, rettet og dekket av
regresjonstest.

PostgreSQL-verifikasjon er ikke kjørt i dette arbeidsmiljøet fordi verken en
PostgreSQL-tjeneste, Docker, WSL eller en tilgjengelig pakkehåndterer finnes.
Fase-4-migrasjonene og de målrettede filtestene må derfor kjøres mot prosjektets
PostgreSQL-testmiljø før produksjonsbruk. Forrige fase verifiserte PostgreSQL
17.11, men det erstatter ikke fase-4-kontrollen.

Ingen OneTagger-API, watcher, lydfingerprinting, WAV-behandling eller generisk
CSV/XLSX-import er implementert. Album uten strekkode/katalognummer opprettes
konservativt ikke automatisk. Konflikter kan ses i forhåndsvisningen, men har
ingen automatisk merge- eller massebehandlingsfunksjon.
