# Fase 2 — operativ musikkatalog

Dato: 13. september 2026.

## Modeller og grenser

- `catalogue`: `Label`, `Release`, `ReleaseTrack` og `DuplicateCandidate`; `ExternalIdentifier` støtter ISRC på innspilling samt UPC/EAN/GTIN på utgivelse.
- `music_library`: `MusicLibraryEntry` med sjanger, språk, målgruppe, kanal, kjønn, rating, energi og verifikasjonsstatus.
- `managed_music`: `ManagedRecording` peker på en `MusicLibraryEntry`. Databaserelasjonen gjør det umulig å ha forvaltet musikk uten arkivmedlemskap. Musikkarkivposter blir aldri automatisk forvaltet.
- `provenance`: `SourceSystem`, `ImportBatch`, `SourceRecord`, `MetadataAssertion` og `AssertionDecision`. Rå kildeverdier og beslutningshistorikk bevares separat fra kanoniske felt.
- `media_assets`: `FileAsset` og `FileLocation`. Filens UUID og eventuelle SHA-256 er adskilt fra aktive, flyttede, manglende og historiske plasseringer. NAS-root kommer fra `P7_NAS_ROOT`.

Alle nye domeneobjekter bruker stabile UUID-er. Recording er fortsatt uavhengig av Work, ISRC, utgivelse, artist, arkivmedlemskap og forvaltning. ReleaseTrack er forekomsten av en Recording på en Release. Label, Party, eier og distributør er separate konsepter.

## Arbeidsflyter

På en utgivelse åpner **Registrer spor** en transaksjonsbeskyttet flyt som enten gjenbruker en innspilling eller oppretter Recording + eventuell ISRC/artistkreditering + ReleaseTrack samlet. Tittel, ISRC, artist og varighet brukes konservativt til kandidatsøk. En mulig match stopper opprettelsen til brukeren velger eksisterende eller uttrykkelig oppretter ny. Overstyring lager en `DuplicateCandidate`; ingen automatisk merge finnes.

Forvaltet musikk har et eget administratorskjema. Det gjenbruker eller oppretter Recording, sørger for MusicLibraryEntry og oppretter ManagedRecording i én transaksjon. Kildepåstander kan bekreftes, bestrides, avvises eller erstattes via loggførte avgjørelser.

Startadressen krever innlogging og åpner en integrert startside i samme Django-adminlayout. Innganger til Musikkarkiv, Forvaltet musikk, Utgivelser og Kontroll vises bare når brukeren har visningstilgang til de underliggende modellene. Direkte lenker returnerer til den forespurte siden etter innlogging.

## FLAC/Vorbis-mapping for senere synkronisering

| Tag | Kanonisk kilde |
| --- | --- |
| `TITLE` | `Recording.title` |
| `ARTIST` | hovedartistens krediterte navn/artistidentitet |
| `ISRC` | Recording-identifikator med type ISRC |
| `GENRE` | `MusicLibraryEntry.genre` |
| `RATING` | `MusicLibraryEntry.rating` |
| `ENERGY` | `MusicLibraryEntry.energy` |
| `TARGET` | `MusicLibraryEntry.target` |
| `KANAL` | `MusicLibraryEntry.channel` |
| `GENDER` | `MusicLibraryEntry.gender` |
| `LANGUAGE` | arkivpostens språk, eventuelt innspillingens språk |
| `P7UUID` | stabil `Recording.id` |

Mappingen er dokumentasjon; fase 2 leser eller skriver ikke lydfiler eller OneTagger-data.

## Migrasjoner, test og begrensninger

Fase 2 legger bare til fremovermigrasjoner: `catalogue 0004`, samt initiale migrasjoner for `provenance`, `music_library`, `managed_music` og `media_assets`. Ingen historisk migration er endret. Ren installasjon og oppgradering fra fase 1.5 testes på SQLite og PostgreSQL.

Selve dublettsammenslåingen, kontrollerte sjanger-/kanalregistre, full import, OneTagger-synkronisering, NAS-skanning, Google Drive-integrasjon, rettigheter, avtaler og enterprise-GUI er utsatt. Feltproveniens bruker portabel objekttype + UUID; eksistens valideres ved registrering, mens historiske påstander kan bestå som dokumentasjon dersom et kanonisk objekt senere fjernes.

SQLite og PostgreSQL 17.11 bestod ren migrering, oppgradering fra fase 1.5 med bevart Unicode/ISRC, `manage.py check`, alle 132 tester og operativ HTTP/admin-smoke. DMP bestod 80/80 isolert på begge databaser.
