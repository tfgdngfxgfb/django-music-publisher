# Fase 6B – objektvis metadataautoritet og katalogvern

Normativ kontrakt, 17. september 2026. Bygger på
[6A – forvaltningssemantikk](phase-6a-management-semantics.md).
Ingen nye modeller eller migrasjoner. Ingen 6C-implementasjon.

**Databaseautoritet er objekt- og domeneavgrenset. Et katalogvern er ikke
automatisk et rettighets- eller forvaltningsforhold.**

## Vurdering mot faktisk kode før implementering

Følgende var allerede riktig: ManagedRecording-medlemskap beskytter Recording
uansett status; ordinær FLAC-import etablerer ikke forvaltning; radiodata har
snapshot-semantikk; tekniske filobservasjoner og rå SourceRecords beholdes;
filskriving krever eksisterende eksplisitt sync og `P7_ALLOW_FILE_WRITES`.
6As eksplisitte tilbakeføring og historiske forvaltningsidentitet videreføres.

Konkrete hull i baselinen:

- Recording-vernet hoppet også over behandling av ordinære utgivelser.
- ManagedRelease hadde cleanup-vern, men ikke vern i track-/cover-import.
- `_catalogue_tags` blandet Recording- og Release-tags og avledet ALBUMARTIST
  fra sporartist. Modellen har ikke en autoritativ Release-hovedartist.
- Release-signals markerte alle filer på berørte Recordings, også filer med
  annen utgivelseskontekst. ManagedRelease utløste ikke relevant sync-markering.
- Rebuild hoppet over beskyttede innspillinger i sin helhet. Radiodata trenger
  en observasjonsbasert oppdatering som ikke først sletter katalogkrediteringer
  eller den MusicLibraryEntry som ManagedRecording avhenger av.
- FLAC-basert «skill ut fil» kan bytte ReleaseTrack.recording. Også denne
  eksisterende inngangen må avvise automatisk omkobling av beskyttet katalog.

En eksisterende sikkerhetsregel beholdes eksplisitt: et aktuelt, bekreftet
lokalt ownership-claim kan beskytte Recording og gi kontrollert writeback også
uten ManagedRecording. Dette er **ikke** avledet fra ManagedRelease. 6B innfører
ingen ny generell rights-resolver eller automatisk medlemskap. Dagens enkle
datoavgrensning videreføres, uten å late som den er en full territory/scope-motor.

## Authority-matrise

| Recording | Release | Recording-katalog | Release/track/cover | Radiodata og filobservasjoner |
|---|---|---|---|---|
| Ordinær | Ordinær | Eksisterende FLAC-regler | Eksisterende ingest-regler | FLAC/storage |
| ManagedRecording | Ordinær | DB-beskyttet | Eksisterende ingest-regler | FLAC/storage |
| Ikke ManagedRecording, spor på ManagedRelease | ManagedRelease | Avledet identitetsvern | DB-beskyttet | FLAC/storage |
| ManagedRecording | ManagedRelease | DB-beskyttet | DB-beskyttet | FLAC/storage |

ManagedRecording og ManagedRelease beskytter sine respektive katalogobjekter
i PENDING, ACTIVE og INACTIVE så lenge medlemskapet består. En aldri-aktiv
Recording som eksplisitt tilbakeføres i 6A, følger igjen ordinære regler hvis
den ikke har annet selvstendig eller avledet vern.

### Recording

`catalogue.authority` skiller `managed`, `via_release`, eksisterende
`local_ownership`, `protected` og `writeback`. `managed_release_only` betyr
`via_release AND NOT managed`, uavhengig av livssyklusstatus på utgivelsen.

Vernet omfatter faktiske ingest-mutasjoner: tittel, Recording-varighet,
kanonisk ISRC og etablering av RecordingContribution. Eksisterende felter som
versjonsbetegnelse og Recording-type endres allerede ikke ved reread.
P7UUID-/ISRC-matching og konfliktkontroll beholdes. Matching er ikke mutation.
Et beskyttende ReleaseTrack kan ikke brukes til å endre global Recording-identitet
gjennom en ny FLAC-kilde. Ingen ManagedRecording eller RightsClaim opprettes
som følge av dette vernet.

### Release og ReleaseTrack

Release behandles uavhengig av Recording. Dagens import kan opprette ordinære
utgivelser og spor, men overskriver allerede ikke eksisterende Release-tittel,
label, år eller katalognummer. 6B sperrer faktiske mutation-paths for
ManagedRelease: nye tracks, nye signaturkoblinger og automatisk covertilknytning.
Eksisterende tekniske FLAC-release-signaturer brukes fortsatt til matching;
de er ikke produktidentifikatorer eller juridisk dokumentasjon.

En allerede registrert FileAsset-sporkobling på ManagedRelease beholdes ved
tagavvik. Uten slik kobling kan en entydig eksisterende track matches på
Recording og oppgitt spor-/discnummer. Sequence behøver ikke være utledet av
discnummeret. Side, sequence, Recording-kobling, title_override og sporvarighet
endres ikke. Manglende eller tvetydig match gir et synlig avvik, ingen ny track;
sikre Recording-/radio-/filobservasjoner kan likevel importeres.

Uten sikker Release-identifikasjon eller spormatch gjetter systemet ikke en
forvaltet kobling. Det introduseres ingen fuzzy matching i 6B.

### Cover og tekniske filer

Ordinær cover-discovery videreføres. ManagedRelease får ikke nytt cover fra et
tilfeldig folder-/cover-/front-bilde. Allerede registrerte coverlokasjoner kan
kontrolleres for størrelse, SHA-256, tidspunkt og tilgjengelighet uten at selve
covervalget endres. Råfiler, mastere og lydinnhold endres ikke.

FileAsset størrelse, checksumhistorikk, tekniske lyddata, lesetidspunkt,
source-modified og FileLocation-status følger fortsatt faktiske observasjoner.
`on_managed_release_track` gjelder **den konkrete filens** ReleaseTrack,
ikke alle filer på en Recording som forekommer på en ManagedRelease.

### MusicLibraryEntry og provenance

Genre, language, energy, gender, rotation_suitability, channels og target_audiences
er fortsatt FLAC-/OneTagger-drevne. Eksplisitt reread av registrert radiofil
rydder verdier når tags er fjernet. Dette gjelder også begge typer katalogvern.
Notes er ikke et FLAC-katalogfelt og slettes ikke i observasjonsbasert rebuild.

SourceRecord/raw payload og ingest-meldinger beholder kildens avvik. Det er
ikke laget en ny assertion-/conflict-motor. Eksisterende radiodata-assertions
og revisjonsspor videreføres; beskyttede katalogverdier skrives ikke som nye
kanoniske FLAC-assertions. Avvik gir pending når et autorisert tag-subsett kan
synkroniseres, ellers relevant konfliktmelding på fil/ingest-resultat.

## Kontrollert DB → FLAC

- Recording-subsett: TITLE, ARTIST, ISRC, COMPOSER, LYRICIST, ARRANGER. Krever
  egen Recording-writeback-autoritet, aldri bare medlemskap på en utgivelse.
- Release/track-subsett: ALBUM, TRACKNUMBER, DISCNUMBER, DATE, BARCODE,
  CATALOGNUMBER. Krever at **filens egen ReleaseTrack** er på ManagedRelease.
- P7UUID beholder eksisterende teknisk identitetssemantikk, inkludert separat
  eksplisitt UUID-only-skriving.
- ALBUMARTIST bevares. Sporartist er ikke automatisk albumartist.
- Alle tags utenfor det faktisk autoriserte subsettet verifiseres urørt,
  også tags som inngår i et annet katalogobjekts mulige skrive-subsett.

Recording-signals markerer bare Recording-writeback. Release-, track-,
releaseidentifier- og ManagedRelease-signals markerer relevante radiofiler med
faktisk sporkobling. Markering skriver ingen fil; eksisterende suppression og
write gate beholdes. Autoritet beregnes på nytt ved sync. Ingen dirty-reason-modell,
Quick Tag, bakgrunnssync eller generell two-way-sync er innført.

## Cleanup og rebuild

Cleanup vurderer sletting av regenererbar MusicLibraryEntry, ikke sletting av
Recording, Release, ReleaseTrack eller historikk. Eksisterende manuelle data,
rettighetskunnskap og ManagedRecording-avhengighet beskytter fortsatt posten.
En release-only Recording uten slike selvstendige sperrer kan få en bortfalt
radioarkivpost fjernet; den beskyttede Recording-identiteten og utgivelsen består.

Rebuild har to eksplisitte grupper i planen:

1. Vanlig regenerering etter eksisterende sikkerhetskontroll.
2. Kun oppdatering av radio-/filobservasjoner for beskyttet katalog. Ingen
   sletting av MusicLibraryEntry eller kanoniske krediteringer først.

Autoritet vurderes igjen ved utføring. Hvis vernet bak en refresh-plan er borte,
hoppes posten over fremfor å utvide planen til katalogoverskriving. Nytt vern
etter en vanlig preview hindrer destruktiv reset. Filene endres ikke av rebuild.

## Operativ identifisering og ytelse

`annotate_recording_authority` bruker Exists, også på MusicLibraryEntry- og
FileAsset-querysets med eksplisitt Recording-referanse. `protecting_releases`
lister de konkrete årsakene. `annotate_file_authority` identifiserer filens
sporkontekst i én query. Ingen ny persistent authority-status.

Musikkarkivet har filteret **Kun via forvaltet utgivelse**, en nøktern badge og
permission-styrte utgivelseslenker i inspector. Eksisterende «Ikke forvaltet»
betyr fortsatt «ikke ManagedRecording» og inkluderer derfor særkategorien.
Når ManagedRecording opprettes, faller innspillingen automatisk ut av særfilteret.
Recording → Filer viser sporkontekst uten å bygge en ny fil-workbench.
Sesjon, pagination og avspiller er uendret.

GUI-listene bruker annotations; bare valgt inspector henter beskyttende utgivelser.
Regresjonstestene kontrollerer query-antall for helpers og sentrale GUI-lister.
Ingests eksisterende transaksjon og stale filkontroll videreføres; ingen ny
distribuert concurrency-/locking-motor er introdusert.

## Spørsmål til 6C og senere

- Hvordan skal aktuelle rettigheter løses for territorium og tid, utover dagens
  enkle lokale ownership-sikkerhetsregel?
- Må en distribusjons-/administrasjonsrett kunne begrenses til én Recording
  **på én bestemt Release**, uten å gi generell Recording-level rett?
- Senere Party/Release-arbeid må definere kanonisk albumartist før ALBUMARTIST
  eventuelt kan bli databaseautoritativ.

Katalogvern er ikke en løsning på disse rettighetsspørsmålene. 6B gir ingen
automatisk onboarding, distribusjonstillatelse eller rettighetsanskaffelse.

## Lokal verifikasjon

Python 3.14.3, SQLite: 245 tester for flac_ingest, managed_music, catalogue,
music_library, gui_v2 og rights bestått. De 23 egne 6B-testene ble deretter kjørt
på nytt etter siste cover-/cleanup-kontroll, også bestått. Testene omfatter
mixed authority, PENDING/ACTIVE/INACTIVE, beskyttet track-match og avvik,
coverobservasjon, tag-subsett, write gate, dirty-markering, rebuild/cleanup,
filter/permissions og konstant query-antall ved flere listerader.

Black 26.5.1 (79 tegn) på endrede Python-filer, `manage.py check`,
`makemigrations --check --dry-run` og `git diff --check` bestått.
Ingen lokal PostgreSQL-kjøring. Full suite og PostgreSQL 17.11 overlates til
GitHub Actions; lokal verifikasjon alene erklærer ikke branchen mergeklar.
