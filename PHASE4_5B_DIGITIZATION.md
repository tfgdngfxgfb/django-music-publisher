# Fase 4.5B – digitalisering

`Release → DigitizationBatch → RAW → redigert master → Recording → selected_master → 4.5A → radio-FLAC`.

## GUI-revisjon etter pilotbruk

Normal inngang er nå **Start ny digitalisering**: velg/opprett Release,
registrer metadata og spor i Release-arbeidsflaten, velg RAW-filer, velg
redigerte WAV-mastere, koble RAW → master og master → spor/Recording, og velg
master. RAW og master har hver sin filvelger med en avgrenset, read-only
mappevisning. Søk og mappenavigasjon oppdaterer bare den aktuelle filvelgeren,
uten full sidelasting; vanlig GET er reserve uten JavaScript.
Sporregistreringen kan knytte en trykt hovedartistkreditering til en
eksisterende kanonisk ArtistIdentity med et annet navn. `credited_as` beholdes
uendret, og ukjent/ikke-entydig identitet blir ikke opprettet automatisk.
Registrering av valgte filer bruker eksisterende
`DigitizationPlan`-preview/apply og endrer ikke kildefilene. En fysisk RAW-fil
gjenbrukes bare innen samme batch i denne revisjonen.

En batch kan avsluttes når katalog-/filkoblingene og mastervalgene er på
plass; radio-FLAC er ikke et ferdigkrav. Den tverrgående Radio-FLAC-flaten
viser én rad per Recording, også når den finnes på flere Releases. Status
avledes fra valgt master, gjeldende radiofil, kandidat og kjent lineage.
Ukjent lineage er et nøytralt undersøkelsessignal, ikke en automatisk
erstatningsbeslutning. Bulkforhåndsvisning revalideres før eksisterende
4.5A-generator lager kandidater; aktivering skjer separat. Ingen ny
work-item-modell eller statuskolonne er opprettet.

Installasjonen kan konfigurere separate, read-only kildeområder gjennom
`P7_RAW_SOURCE_ROOT` og `P7_MASTER_SOURCE_ROOT`; ellers kan den eksisterende
Musikkarkiv-roten brukes. **Standardmappe per filrolle i systemadministrasjon**
er en senere innstillingsoppgave og er ikke modellert her. Mer detaljert
fysisk kildebeskrivelse, som spolebånd-master for kassett og 2-/4-spors
programstruktur, hører også til en senere formatrevisjon. Den eksisterende
`Release.release_type` brukes nå uten ny fysisk arkivmodell.

Oppdeling og lydredigering skjer manuelt utenfor P7-systemet. P7 dokumenterer
resultatet og provenance; det er ikke et lydredigeringsprogram.

## Arbeidsflate og domene

GUI v2 har **Digitalisering** med søk, batchoversikt, mappeforhåndsvisning,
mastertabell med flervalg, valgt-fil-panel, råmateriale og pipeline per spor.
Eksisterende utgivelseseditor brukes til sporstruktur. Innspilling → Filer
viser digitaliseringsopprinnelsen også for genererte radiofiler.

- `DigitizationBatch`: ett forsøk på en Release; flere forsøk kan beholdes.
- `DigitizationFile`: bevart medlemskap for FileAsset, også før Recording finnes.
- `DigitizationDerivation`: brukerbekreftet RAW → redigert master. Én aktiv
  råkilde per master; én råkilde kan gi mange mastere. Korrigering krever grunn
  og beholder den gamle relasjonen inaktiv.
- `DigitizationPlan`: lagret preview med bruker, konsekvenser og fingerprint.
- `MediaAssetEvent`: eksisterende audit utvidet med batchkontekst for filer
  uten Recording. Senere mastervalg og generering bruker eksisterende audit.

Migrasjonene `media_assets.0006` og `0007` er additive. Ingen gamle
migrasjoner, lydfiler eller katalogobjekter transformeres automatisk.

Bulkhandlingene er råkobling, eksplisitt Recording-/ReleaseTrack-kobling og
mastervalg. Filnavn, spor/side/disc og tittel gir forslag, aldri automatisk
identitet. Nye Recordings opprettes bare ved eksplisitt valg. Eksisterende
mastervalg overskrives ikke i bulk; individuell 4.5A-bekreftelse brukes.

Apply låser Release, batch, involverte Recordings og filer, validerer preview
på nytt og lagrer hele bulkoperasjonen atomisk. Gamle previews avvises;
gjentatt apply av samme plan er uten nye endringer. Filregistrering leser
metadata/checksum på nytt før apply. Ingen eksisterende WAV/FLAC skrives.

Tilgang krever vanlige view-permissions og `media_assets.operate_digitization`;
GUI-skriving krever også `GUI_V2_WRITES_ENABLED`. Generering beholder egne
4.5A-permissions, skrivegate og separat kandidat/aktivering.

## Første radiofil

Eksisterende entydig radio-FLAC brukes etter gjeldende 4.5A-kildeautoritet.
Tvetydig eller utilgjengelig registrert radiofil gir ikke fallback til DB.

Når ingen radiofil finnes, bruker samme generator bare registrerte
katalogopplysninger, entydig/spesifikt koblet ReleaseTrack og radiometadata
fra Musikkarkiv. Manglende verdier utelates. P7UUID skrives bare til den nye,
P7-genererte filen som i 4.5A. Ingen verdier oppfinnes fra filnavn.

Før plan opprettes må brukeren bekrefte at DB-opplysningene er kontrollert og
så komplette som det fysiske mediet tillater. Preview-fingerprint avviser
endrede metadata. Bekreftelsen bevares på genereringsplanen med bruker/tid.
Dette er brukerens katalogansvar, ikke automatisk kvalitetssikring.
Senere følger filene samme eksisterende regler som øvrige radio-FLAC-er;
Managed Music og rettigheter opprettes eller utledes ikke automatisk.

## Verifikasjon og grenser

Målrettede tester dekker atomisk rollback, foreldet preview, rettigheter,
råkoblinger, re-digitalisering, delvis mastererstatning, uendret filhash/mtime,
første radio fra DB, PCM/metadata-verifikasjon, aktivering, Range og rebuild.
Ekte PostgreSQL-tester bruker separate connections for konkurrerende
råkoblinger og mastervalg. Query-regresjon sammenligner 3 og 50 mastere.

Kjør `python manage.py test` med PostgreSQL 17.11 gjennom `DATABASE_URL`, og
deretter SQLite. CI kjører Python 3.14 og separat 3.15-preview. Lokalt er
Python 3.13.12 brukt som supplerende testmiljø, ikke som støttet runtime.
Fresh PostgreSQL og oppgradering fra `media_assets.0005` til `0006/0007` med
eksisterende master/current/audit er kontrollert i isolerte databaser.

Lokalt sluttresultat: PostgreSQL 374 tester (1 eksisterende Windows-symlink-
skip); SQLite 366 kjørte tester (3 skips: symlink og to PostgreSQL-spesifikke
testklasser). Black 26.5.1 med linjelengde 79 kontrollerte alle 182 Python-filer
i CI-omfanget uten avvik. `pip check`, `manage.py check`,
`makemigrations --check --dry-run` og JavaScript-syntakskontroll var rene.

Nettleserkontroll bruker syntetiske WAV-filer og en separat testdatabase.
Faktisk P7-NAS, Windows/UNC og reelle filrettigheter er fortsatt miljøtester.
Mappeimport leser bare WAV-filer direkte i valgt mappe. En fil tilhører én
batch; flere råkilder til én redigert fil støttes ikke i denne versjonen.
Generering og aktivering skjer per Recording gjennom eksisterende 4.5A.

Programarkiv, Delivery, Rotasjon, DSP, automatisk oppdeling og flytting/
sletting av kildelyd er ikke implementert. Batchmedlemskap og råprovenance er
adskilt fra musikkens Recording-kobling; ingen polymorf modell eller
GenericForeignKey er innført for hypotetisk framtidig gjenbruk.

## Veiledet digitalisering og valgt master ved avspilling (pilot, 2026-09-18)

Normal inngang er «Start ny digitalisering». Søk i utgivelser omfatter label,
katalognummer, tittel og registrert artist/kreditering. Valgt eller ny utgivelse
fortsetter i digitaliseringsguiden: metadata/spor, RAW, redigerte mastere,
kobling/mastervalg, Radio-FLAC-status/ferdig. Utgivelsesredigering og sporgrid
gjenbruker eksisterende Release-workspace med retur til riktig steg. Ingen ny
lagret wizard-status eller nye domenemodeller er innført.

`catalogue.metadata_providers` definerer en read-only grense for søk og
kildebelagte metadataforslag. NCB er første planlagte leverandør; NCB, TONO og
Gramo vises som ikke konfigurert. Faktisk integrasjon krever godkjent API,
tilgang, kildeformat og kontroll av forslag mot fysisk kilde. Ingen scraping
eller automatisk canonical metadata-import er implementert.

RAW- og mastervelgere navigerer med klikkbar mappesti. Brukeren skriver ikke
filstier. Søking/mapper oppdaterer bare panelet med fetch; avkrysninger bevares
innen samme mappe også når et søk skjuler noen av de valgte filene. Antallet
skjulte valg vises før forhåndsvisning. Listing leser én mappe, høyst 500 treff,
og gjør ikke rekursive NAS-skanninger eller endrer source storage.

Systemoppsettet bruker `P7_RAW_SOURCE_ROOT` og `P7_MASTER_SOURCE_ROOT` med
tilsvarende `*_CLIENT_ROOT` for Windows-stier. Uten separate røtter kan brukeren
bla fra det eksisterende Musikkarkiv-området. Følgende miljøinnstillinger
styrer mappeforslag, uten hardkodet label:

- `P7_RAW_FOLDER_TEMPLATE`: standard `{label}/{series}`.
- `P7_MASTER_FOLDER_TEMPLATE`: standard
  `{label}/{catalogue_number} - {title}`.

`series` er bokstavprefikset i katalognummeret (FMC 102 → FMC). Malene kan bruke
label, series, catalogue_number og title. Forslag prøver bare den forventede
mappen og dens foreldre innen konfigurert rot. Manglende katalogopplysninger
eller mappe gir manuell navigasjon, aldri opprettelse/flytting. Native redigering
av standardmapper i systemadministrasjon og full fysisk formatmodell er utsatt.

`media_assets.digitization_matching.suggest_master_links()` er den felles, rene
forslagstjenesten for koblingstabellen. Den mottar ferdiglastede fakta og gjør
ingen ORM-/filoppslag. Prioritet: lagret kobling, eksplisitt side/spornummer
(A1/A01/B04 osv.), fullstendig nummerert filrekkefølge, deretter entydig lagret
filendringstid. Endringstid er ikke fremstilt som sikker opprettelsesdato.
Ulikt antall mastere/spor, like tidsstempler, gjentatte posisjoner og konflikt
med lagret Recording gir manuell kontroll fremfor stille forskyvning.
RAW foreslås etter side eller én forenlig råkilde; A+B kan dekke begge sider.
Ingen filnavn/tidspunkter blir autoritative katalogdata.

«Godta alle» velger forslag for brukerens kontroll. `link_masters` legger
RAW→master og master→Recording/ReleaseTrack i én eksisterende DigitizationPlan.
Samme preview-fingerprint, låserekkefølge, permissions og atomiske apply brukes;
begge relasjoner og hendelser rulles tilbake dersom en rad feiler. De gamle
rå-/Recording-operasjonene deler nå apply-funksjoner med samlehandlingen.
Valgt master er fortsatt et eget eksplisitt valg og erstattes aldri automatisk.

Vanlig intern avspilling bruker `resolve_recording_playback`: valgt master
først, ellers eksisterende konservativ current-radio-resolver. En utilgjengelig
valgt master rapporteres og gir ingen skjult overgang til en annen lydfil.
WAV strømmes med riktig MIME-type, Range/HEAD og samme tilgangs-/storagekontroll.
Normal lydadresse er `/v2/avspilling/innspillinger/<uuid>/lyd`; eksisterende
`radio.flac`-adresse og «Spill Radio-FLAC» er fortsatt eksplisitt radioavspilling.
4.5A-generering, metadataautoritet, Delivery og radio-lifecycle er uendret.

Radio-FLAC-workbenchen og batchens oppsummering gjenbruker pipeline-statusen;
ferdigstilling krever ikke Radio-FLAC. Søk etter eksisterende radio åpner
Musikkarkivets registrerte materiale; dette er ikke en ny fysisk FLAC-import.
Ingen produksjons-NCB-integrasjon, ny generator, DSP eller work-item-tabell.

Revisjonen kontrolleres med målrettede SQLite-tester, Django check,
migrasjonskontroll, Black 26.5.1/79 og JS-syntakskontroll. Nettleserkontroll
bruker syntetiske data i SQLite-minne. PostgreSQL er ikke kjørt i denne
revisjonen; de tidligere fasekontrollene ovenfor er historiske resultater.
