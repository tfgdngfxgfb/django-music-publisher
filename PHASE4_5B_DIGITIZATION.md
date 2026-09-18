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
