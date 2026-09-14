# Fase 4.2 — regenererbart Musikkarkiv

## Filpolicy og autoritet

- Eksisterende radio-FLAC er read-only ved skann, preview, import, re-scan,
  cleanup og rebuild. Disse handlingene skriver heller ikke P7UUID eller cover.
- Den laveste tag-skriveren og synkroniseringstjenesten krever eksplisitt
  `P7_ALLOW_FILE_WRITES=true`. Standard og begge lokale launchere bruker
  `false`. Vanlige katalogendringer markerer bare relevante filer som ventende.
- En P7UUID som allerede finnes leses, bevares som råmetadata og brukes som et
  konservativt matchingssignal. Import og rebuild fungerer også uten P7UUID.
- Uforvaltet FLAC er autoritet for filbårne katalog- og radiometadata.
  Databasen beholder intern identitet, relasjoner, matchingavgjørelser,
  provenance og administrativ kunnskap. Forvaltet/lokalt eid musikk beholder
  databaseautoritet for katalogmetadata; radiometadata leses fortsatt fra FLAC.

## Regenererbart og beskyttet

Rebuild regenererer `MusicLibraryEntry`, radiometadata og FLAC-krediteringer,
og oppdaterer tekniske data på eksisterende filreferanser. Eksisterende
`Recording`-UUID, `FileAsset`, plassering-/checksumhistorikk og trygg
Release-struktur gjenbrukes. Det gjør prosessen uavhengig av tilfeldige
database-primary keys uten å kaste stabile identiteter.

En innspilling blokkeres konservativt når den har Forvaltet musikk,
RightsClaim, andre filroller, manuelle/eksterne kilder eller krediteringer,
kontrollert kataloginnhold, manuelle merknader, en vurdert ingestkonflikt,
kjent ISRC-avvik, dublettavgjørelse eller endringer etter siste FLAC-innlesing.
Usikker provenance behandles som beskyttet. Agreement og RightsDecision ligger
utenfor cleanup/rebuild og slettes aldri.

## Cleanup, rebuild og flytting

**Kontroll → Vedlikehold av Musikkarkivet** gir separate previews for
**Rydd Musikkarkiv** og **Bygg uforvaltet Musikkarkiv på nytt**. Planen viser
hva som kan fjernes, hva som bevares og hvorfor. Utføring har egne permissions,
konkret bekreftelse og backupbekreftelse ved rebuild. Jobben logger plan,
resultat, bruker, tidspunkt og feil.

Manglende filer får status *Mangler på registrert plassering*; Recording,
FileAsset, tidligere FileLocation og provenance beholdes. En uttrykkelig
fjerning av én uforvaltet post fjerner MusicLibraryEntry og gjør aktiv
plassering historisk, men beholder filen og Recording. Automatisk hard delete
av Recording, FileAsset, Release eller kildehistorikk utføres ikke.

STREAMINFOs PCM-MD5 lagres som teknisk signal. En flyttet fil kan gjenkjennes
når nøyaktig én kandidat har samme PCM-MD5 og P7UUID, ISRC eller tittel og
varighet er kompatible. PCM-MD5 brukes aldri alene til Recording-merge.

## Migrasjon og verifikasjon

`flac_ingest.0005` legger til den auditerte vedlikeholdsjobben og fire separate
preview-/utfør-permissions. `music_library.0007` registrerer *Ikke vurdert*
som en eksplisitt rotasjonstilstand. Ren SQLite-migrering og oppgradering fra
de foregående migrasjonene er kontrollert.

54 målrettede FLAC-tester dekker blant annet filskriveport, uendrede bytes,
P7UUID med og uten gjenoppretting, rebuild uten P7UUID, flyttet/manglende fil,
beskyttet manuell kunnskap, Managed Music, RightsClaim/Agreement, cleanup og
serverside-permissions. Workbench/GUI v2-regresjonen bestod 68 tester, og
hele SQLite-suiten bestod 279 tester. `manage.py check` var uten feil.
Nettleserprøven bekreftet innlogging, tilgang til vedlikeholdsflaten og at en
cleanup-preview viser konsekvensene uten å endre data eller filer.

Et isolert SQLite-forsøk brukte 1 475 lokale FLAC-filer. Førstegangsskann tok
45,3 sekunder og ga 1 475 nye filer uten konflikter. Alle 1 475 ble importert
på 163,8 sekunder. En ny inkrementell skann tok 8,6 sekunder og klassifiserte
alle 1 475 som uendrede. OneTagger-verdiene fordelte seg på 132 *Ikke
rotasjonsverdig* og 22 uttrykkelig *Ikke vurdert*, uten ugyldige
rotasjonsverdier. SHA-256 av alle 1 475 originalfiler var identisk før og
etter. Testdatabasen var separat og er ikke versjonskontrollert.

PostgreSQL var ikke tilgjengelig lokalt: ingen tjeneste, Docker eller Podman
var installert, og tilkobling til prosjektets lokale port 5433 tidsavbrøt.
Målrettet PostgreSQL-verifikasjon er derfor et konkret krav før P7-pilot.

Kjente begrensninger: webutføring er fortsatt synkron og viser ikke løpende
prosent. Rebuild sletter ikke konservativt beholdte, tomme Releases eller
gamle SourceRecords automatisk. Eksplisitt tag-synkronisering finnes fortsatt
som administrativ kommando bak skriveporten; Quick Tag er ikke implementert.
