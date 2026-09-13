# Fase 3 — eierskap og rettighetsgrunnlag

## Modell og betydning

Appen `rights` legger rettighetsforhold ved siden av katalogen. `RightsClaim`
peker på stabil `Recording` og en `Party`, og skiller kontrollerte typer for
**Mastereierskap**, **Administrasjon** og **Distribusjon**. Kravet kan ha ukjent
andel, rettighetsgiver, åpen gyldighetsperiode, normalisert territorieomfang,
felles `SourceRecord` og avtalegrunnlag. Det opprettes alltid som **Importert /
ikke verifisert**.

`Territory` bruker ISO 3166-1 alfa-2. Et krav gjelder enten hele verden, bare en
valgt landliste eller hele verden unntatt en valgt landliste. `Agreement` har
egne parter med roller og kan knyttes til eksisterende `FileAsset` med filrollen
Dokument. `RightsConfiguration` peker eksplisitt på installasjonens lokale
organisasjon; navn brukes aldri som identitet eller konfigurasjon.

Disse grensene gjelder i både modell og GUI:

- **Managed ≠ Owned**
- **File possession ≠ Owned**
- **Distribution ≠ Ownership**
- **Administration ≠ Ownership**
- **Imported claim ≠ Verified right**

## Historikk og integritet

Status endres transaksjonelt gjennom `RightsDecision`, med bruker, tidspunkt og
begrunnelse. Registrerte krav kan ikke overskrives direkte; korrigering oppretter
et nytt, uverifisert krav og markerer forgjengeren som erstattet. Avvisning og
erstatning beholder krav, kilder og beslutninger. Vanlig arbeidsgrensesnitt tilbyr
ingen hard sletting.

Andel er valgfri `Decimal` fra 0 til 100. Systemet krever ikke en kunstig sum på
100. Før et eierskapskrav bekreftes, avvises en kjent totalsum over 100 for samme
innspilling, identiske periodegrenser og identisk territorieomfang. Mer avansert
analyse av delvis overlappende perioder og territorier er uttrykkelig utsatt.

## Arbeidsflate og tilganger

Innspillingssiden har fanen **Rettigheter**, med separate tabeller for eierskap,
administrasjon og distribusjon, dokumentasjon, kilder, statuser og full
beslutningshistorikk. Avtaler har en egen arbeidsliste og detaljside. Autoriserte
brukere kan registrere krav, beslutte dem, erstatte dem og knytte avtale. Django-
tillatelser håndheves på serversiden: visning, oppretting/endring,
`decide_rightsclaim` og `manage_agreement` er separate fullmakter.

Demo-kommandoen lager fiktive, uverifiserte krav for to påståtte eiere samt
lokal administrasjon og distribusjon. Ingen demoopplysning presenteres som
bekreftet juridisk rett.

## Migrasjoner og verifikasjon

- `rights.0001_initial`: rettighetskrav, beslutninger, avtaler, part-/dokument- og
  territorierelasjoner samt lokal organisasjonsinnstilling.
- `rights.0002_seed_common_territories`: deterministiske startposter for NO, SE,
  DK, FI, IS, GB, US og DE. Flere gyldige ISO-land kan registreres senere.
- Ingen historisk migrasjon er endret.
- Ren installasjon og oppgradering fra fase 2.5 er kontrollert på SQLite og
  PostgreSQL 17.11.
- Full testpakke: 174 tester på hver database. DMP: 80 tester på hver database.
- Nettleser: innlogging/direktelenke, forvaltet uten automatisk eierskap,
  bekreftelse, bestridelse, erstatning, avtale/part/kobling, lesetilgang og lyst/
  mørkt tema er kontrollert mot isolerte testdata.

Begrensninger: Modellen er et dokumentert kravregister, ikke en juridisk
overlapp-, eksklusivitets- eller royaltymotor. Avtaler har ikke versjonerte
kontraktstekster eller økonomiske vilkår. Direkte databaseskriving utenom Django-
tjenestene kan omgå beslutningsflyten. FLAC, OneTagger, NAS-skanning, full import,
OCR, royalties og publishing-rettigheter er ikke del av fase 3.
