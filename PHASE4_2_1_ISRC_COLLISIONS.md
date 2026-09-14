# Fase 4.2.1 — ISRC-kollisjoner

## Import og representasjon

ISRC er fortsatt et sterkt matchingssignal, men filer kobles ikke automatisk til
samme `Recording` når tittel, artist, varighet eller utgivelseskontekst samlet
viser en tydelig identitetskonflikt. Dette kontrolleres både mot katalogen og mot
tidligere filer i samme skann. Samme innspilling kan fortsatt forekomme på flere
filer og utgivelser med samme ISRC.

Kanonisk ISRC beholder eksisterende unikhetsregel. Ved dokumentert feilbruk av
samme ISRC lagres kildefilens verdi som en bestridt `reported_isrc`-påstand. En
avvist dublettkandidat med signalene `reported_isrc_collision` og
`manual_separate_recordings` registrerer den menneskelige avgjørelsen om at
innspillingene skal forbli adskilt. Denne avgjørelsen og filens eksplisitte
Recording-kobling behandles som beskyttet kunnskap ved re-scan, cleanup og
rebuild.

## Reparasjon i GUI v2

Når en uforvaltet innspilling har flere radio-FLAC-er, viser **Musikkarkiv →
Filer** handlingen **Skill ut som egen innspilling**. En preview viser filen,
nåværende Recording, leste FLAC-metadata og eventuell entydig sporforekomst.
Ved bekreftelse opprettes ny Recording og MusicLibraryEntry, valgt FileAsset og
bare en entydig ReleaseTrack flyttes, og metadata leses gjennom den eksisterende
ingesttjenesten. Den opprinnelige Recording leses på nytt når nøyaktig én
autoritativ radiofil gjenstår. Forvaltede innspillinger og innspillinger med
rettighetsdata blokkeres for manuell avklaring.

Arbeidsflyten skriver, flytter eller kopierer aldri FLAC-filen.

## Verifikasjon

90 målrettede tester for FLAC-ingest og GUI v2 bestod. De dekker samme ISRC for
samme innspilling, tydelig forskjellige innspillinger med samme ISRC, manuell
utskilling, bevart keep-separate-beslutning gjennom rebuild, permissions og
uendrede filbytes. `manage.py check` og migrasjonskontrollen var uten feil; ingen
ny migrasjon er nødvendig.

En read-only skann av de 1 475 lokale testfilene identifiserte 34 filer med
tydelig inkompatible metadata under samme ISRC. De ble isolert som konflikter i
stedet for å bli koblet automatisk. Ingen import/apply eller filskriving ble
utført i denne korpuskontrollen.

