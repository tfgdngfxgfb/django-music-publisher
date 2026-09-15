# Fase 4.5A – master til radio-FLAC

Fase 4.5A lar en bruker registrere en eksisterende master på en eksisterende
`Recording`, velge masteren uttrykkelig, forhåndsvise en ny radio-FLAC,
generere og verifisere en kandidat og deretter aktivere kandidaten separat.
Ingen eksisterende lydfil overskrives, flyttes eller slettes.

## Modell og tilstander

- `RecordingMediaSelection` lagrer én valgt master og én eksplisitt gjeldende
  radio-FLAC per `Recording`, med bruker og tidspunkt.
- `FileAsset.role` beskriver filtypen. `lifecycle_status` beskriver
  `CANDIDATE`, `CURRENT`, `HISTORICAL` eller uklassifisert tilstand.
- `FileDerivation` dokumenterer at en kandidat ble generert fra en bestemt
  master, med encoder, parametere og verifikasjon.
- `RadioFlacGeneration` bevarer preview, metadatakilde, mål, status og
  verifikasjonsresultat.
- `MediaAssetEvent` er strukturert, uforanderlig historikk for registrering,
  valg, verifisering, aktivering og avløsning.

Recordings uten eksplisitt current-valg bruker fortsatt den konservative
playback-resolveren. Kandidater og historiske filer inngår ikke i fallbacken.
Valgene, lineage og genereringshistorikken beskytter Recordingen mot
regenererbar cleanup/rebuild.

## Lyd og metadata

Automatisk generering støtter integer PCM WAV/WAVEX med 16 eller 24 bit.
Flyttall og andre representasjoner som krever kvantisering eller annen
lydendring blokkeres. `soundfile`/libsndfile koder FLAC uten resampling,
gain, normalisering, kanalendring eller annen DSP.

Master og kandidat dekodes blokkvis til heltalls-PCM. SHA-256 over alle
dekodede samples må være identisk. Sample rate, bitdybde, kanaler og antall
frames må også stemme. Kandidaten leses deretter gjennom den etablerte
`flac_ingest.adapter`-kontrakten, og alle forventede tags må round-trip-e.

For forvaltet musikk kommer katalogmetadata fra databasen og radiometadata
fra gjeldende radio-FLAC. For uforvaltet musikk kommer filbårne katalog- og
radiometadata fra gjeldende radio-FLAC. Den genererte filen får Recording-ens
`P7UUID`. Bare den etablerte katalog- og radiotag-allowlisten skrives;
ukjente tags og rights-/agreement-data kopieres ikke.

## Storage og skrivepolicy

Masteren registreres fra en konfigurert storage-root og åpnes read-only via
`media_assets.storage`. Kandidaten skrives til den uttrykkelig konfigurerte
`generated_media`-roten. `P7_ALLOW_FILE_WRITES=false` blokkerer selve
genereringen, mens inspeksjon og preview fortsatt virker. Målfilen får et
unikt navn og en eksisterende fil overskrives aldri.

I en operativ installasjon bør den genererte roten ligge innen området som
inngår i senere skann/re-scan, eller dette området må skannes uttrykkelig.
Faktisk server-mount, Windows client-root og filsystemrettigheter må
verifiseres i P7-miljøet før pilot.

## Avgrensninger

Embedded cover kopieres ikke fordi prosjektet ennå ikke har en entydig policy
for valg av Release-cover. Det blokkerer ikke lydgenerering. Fasen innfører
ingen opplasting, lydredigering, rådigitalisering, Delivery, rotasjon,
TuneTracker eller automatisk playout-synkronisering. PostgreSQL-race mellom
samtidige aktiveringer må verifiseres mot en faktisk PostgreSQL-instans før
pilot; modellen bruker radlåsing og en unik current-constraint.
