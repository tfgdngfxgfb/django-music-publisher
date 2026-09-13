# Fase 3.1 — rettighetsoversikt og konteksthjelp

## Eierskapsoversikt

Forvaltet musikk klassifiseres ved visning fra aktive `RightsClaim`-poster og
den lokale organisasjonen i `RightsConfiguration`. Kategoriene er **Heleid**,
**Deleid**, **Ikke eid**, **Uavklart** og **Bestridt**. Manglende eller bare
uverifiserte eieropplysninger gir alltid Uavklart. Ikke eid krever bekreftede,
verdensomspennende krav med kjente andeler som viser 100 prosent hos andre.
Territoriespesifikke kombinasjoner konkluderes konservativt; fase 3.1 innfører
ingen juridisk overlappsmotor.

Administrasjon og distribusjon beregnes og filtreres separat. Ingen av dem,
medlemskap i Forvaltet musikk eller filbesittelse innebærer eierskap.

## Dokumentasjonsstyrke og hjelp

`RightsClaim.evidence_strength` skiller menneskets vurdering av grunnlaget fra
kravets beslutningsstatus. Kontrollerte nivåer er Ikke vurdert, Svak indikasjon,
Sannsynlig, Sterkt underbygget og Dokumentert. Feltet er ikke en score og gir
ingen automatisk juridisk konklusjon.

`rights.help_content` er én liten autoritativ kilde for korte tooltips og lengre
forklaringer. Workbench-hjelpen er den eneste brukerrettede hjelpesiden. Små
`ⓘ`-knapper virker med peker, tastaturfokus og trykk, kan lukkes med `Esc` eller
trykk utenfor og lenker til riktig hjelpeavsnitt. Kritiske beskjeder om uavklart
eierskap og «Forvaltet ≠ eid» står fortsatt synlig.

## Migrasjon, demo og verifikasjon

- `rights.0003_rightsclaim_evidence_strength` er en additiv migrasjon med trygg
  standardverdi for eksisterende krav.
- Det deterministiske demo-datasettet viser alle fem eierskapskategorier,
  forskjellige dokumentasjonsnivåer og lokal administrasjon/distribusjon uten
  lokalt eierskap.
- SQLite: ren migrering og oppgradering med bevart eksisterende krav bestod;
  `manage.py check` var ren og full suite bestod 184/184 tester.
- PostgreSQL 17.11: ren migrering og oppgradering med bevart eksisterende krav
  bestod; systemkontrollen var ren og rights/workbench bestod 45/45 tester.
- En faktisk utviklingsserver og installert Chrome bestod innlogging med bevart
  filtrert returadresse, alle fem eierskapsfiltre, lokale administrasjons- og
  distribusjonsfiltre, rettighetsoppsummering og sentral hjelp. Tooltip ble
  prøvd med mus, tastaturfokus, Enter og `Esc`. Mørk rettighetsvisning og lys
  hjelpeside ble visuelt kontrollert i 1600 × 1000.
- Templatebiblioteket `workbench_tags` registreres eksplisitt i prosjektets
  innstillinger. Dette hindrer `TemplateSyntaxError` etter ren oppstart; en
  allerede kjørende server må startes på nytt når ny Python-kode legges til.

Kjent begrensning: klassifiseringen er en praktisk katalogoversikt for dagens
omfang. Kompleks overlapp i tid og territorier og full rettskjedeanalyse krever
fortsatt menneskelig vurdering og er utsatt.
