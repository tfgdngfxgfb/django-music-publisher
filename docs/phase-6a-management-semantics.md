# Fase 6A – forvaltningssemantikk og invariants

Normativ kontrakt fra 17. september 2026. Denne presiserer forvaltning i den
opprinnelige [arkitekturvurderingen](master-rights-architecture.md).
Implementasjonen og kontrakttestene finnes i `managed_music/lifecycle.py`,
`managed_music/services.py` og `managed_music/test_lifecycle.py`.

## Avgrensning og medlemskap

`ManagedRecording` betyr at lokal organisasjon (P7) har, har hatt eller konkret
vurderer et relevant forvaltningsforhold til en Recording. Det er ingen generell
katalogmerking og intet bevis på mastereierskap.

Den eksisterende relasjonen beholdes:

```text
ManagedRecording → MusicLibraryEntry → Recording
MusicLibraryEntry alene oppretter aldri ManagedRecording.
```

I dagens masterdomene brukes eksplisitte krav for konfigurert lokal organisasjon
av type `master_ownership`, `master_administration` eller `distribution`.
Distribusjon betyr her et registrert forvaltningsgrunnlag; en Delivery, filimport,
filbesittelse, label eller publiseringskreditering er ikke et slikt grunnlag.
Ingen Work-/publishing-rettighet kvalifiserer gjennom denne lifecycle-tjenesten.

Rights-workflowen åpnes ikke generelt for ordinære Musikkarkivposter. Opplysninger
om tredjepart brukes når de beskriver P7s eget forhold, for eksempel deleierskap,
administrasjon eller konflikt. Tredjepartskrav alene aktiverer eller oppretter
aldri P7-forvaltning. Eksisterende generelle lagringsfunksjoner er ikke en ny
autorisasjon til å registrere eierskapet til hele Musikkarkivet.

## Livssyklus

| Tilstand | Betydning |
| --- | --- |
| `PENDING` | Konkret P7-grunnlag vurderes. Et fremtidig bekreftet grunnlag er heller ikke aktivt før startdato. |
| `ACTIVE` | Minst ett kvalifiserende P7-krav er bekreftet og innenfor sin gyldighetsperiode. |
| `INACTIVE` | Faktisk tidligere bekreftet P7-forvaltning, uten aktuelt bekreftet grunnlag. |
| `PENDING` + `requires_follow_up` | Onboarding har mistet alle plausible grunnlag. Medlemskapet beholdes til en uttrykkelig beslutning. Dette er ikke historisk forvaltning. |

`management_state()` returnerer lesbar, testbar tilstand uten å endre data.
`refresh_management_status()` kan oppdatere statusen på eksisterende medlemskap.
Ingen av funksjonene oppretter eller sletter medlemskap.
`decide_rights_claim()` og `supersede_rights_claim()` bruker samme vurdering.

Gjeldende bekreftet grunnlag har forrang. Uten dette har faktisk bekreftet
historikk forrang over nye ubekreftede krav: en historisk forvaltet innspilling
kan være `INACTIVE` samtidig som et nytt grunnlag vurderes.

Manglende aktuelt bekreftet grunnlag er aldri en beslutning om tilbakeføring.
Avvisning/bestridelse av tidligere bekreftet grunnlag sletter ikke at det faktisk
har vært behandlet som forvaltet. Korrigering av feilaktig historisk forvaltning
er en egen fremtidig administrativ prosess.

## Historikk og datoer uten nytt felt

Historikken utledes fra eksisterende `RightsClaim` og immutable `RightsDecision`.
Et krav bekreftet i ettertid for en allerede avsluttet periode kan dokumentere
historisk forvaltning. Et fremtidig krav som avvises før startdato, og aldri blir
virksomt, gjør det ikke. `DOCUMENTED`-hendelser er ikke statusendringer.
Et eksisterende bekreftet krav uten beslutningspost beholdes som bekreftet
grunnlag innenfor datoperioden, også for historisk avsluttede perioder.

Gamle/manuelt satte `ACTIVE`/`INACTIVE` uten underbyggende bekreftelseshistorikk
skal ikke gjettes om til «aldri forvaltet». Vurderingen returnerer
`history_uncertain=True`, `requires_follow_up=True` og `status=None`.
Lagret status beholdes; tilbakeføring er blokkert inntil historikken er avklart.
Dette er et oppfølgingssignal, ikke en ny database-status.

6A beholder dagens enkle datogrenser (inklusive sluttdato) og legger til
historikkvern. Vurderingen kan oppdage utløp uten en ny rettighetsbeslutning.
6A legger ikke inn en periodisk jobb eller bygger om alle GUI-lister som leser
lagret status. Samlet integrasjon av avledet tilstand kommer i lifecycle-fasen.
Parameteren `on_date` støtter kontroll av datogrenser; funksjonen er ikke en
generell historisk/territoriell rettighetsresolver eller en bruksautorisasjon.
Den generelle resolveren og overlappskontroll hører til 6C.

## Uttrykkelig tilbakeføring

`return_to_music_library(managed, user=..., reason=...)` er eneste operative
handling for å avslutte et aldri aktivt onboarding-medlemskap. GUI bygges senere.
Tjenesten krever eksisterende permission
`managed_music.delete_managedrecording` og en ikke-tom begrunnelse.

Tjenesten laster medlemskapet på nytt under lås og krever:

- lagret og avledet `PENDING`;
- ingen aktuelt bekreftet eller historisk bekreftet P7-forvaltning;
- ingen gjenstående plausible P7-krav (ubekreftet, bestridt eller fremtidig
  bekreftet); slike krav må først korrigeres/avvises;
- ingen usikker legacy-historikk og en konfigurert lokal organisasjon.

Handlingen lagrer først en `SourceRecord` i «P7 forvaltningshistorikk» med
handlingstype, bruker, tidspunkt, begrunnelse og snapshot av medlemskapet,
inkludert UUID, kilde, notater og revisjon. Deretter slettes kun
`ManagedRecording`. Begge skjer i samme transaksjon. Recording, MusicLibraryEntry,
RightsClaims, RightsDecisions, tidligere SourceRecords og MetadataAssertions
beholdes. En assertion mot det avsluttede medlemskapets UUID forblir historikk;
snapshotet dokumenterer objektet selv om medlemskapet ikke lenger er aktivt.

Generisk sletting og fri statusredigering av ManagedRecording i Django-admin
er deaktivert, slik at de ikke omgår lifecycle-reglene. Direkte ORM-/database-
operasjoner er vedlikeholdsverktøy og skal ikke brukes som brukerarbeidsflyt.
Claim-beslutninger og tilbakeføring låser samme Recording først for å unngå
at en beslutning og tilbakeføring vurderer forskjellig grunnlag samtidig.

## ManagedRelease

`ManagedRelease` er et selvstendig katalogforhold på Release. PENDING gjelder
konkret vurdering, ACTIVE gjelder aktiv katalogforvaltning og INACTIVE gjelder
faktisk tidligere katalogforvaltning. `owned_catalogue` er katalogklassifikasjon,
ikke et RightsClaim på underliggende mastere.

ManagedRelease oppretter aldri ManagedRecording eller masterrettigheter.
ManagedRecording gjør aldri tilknyttede Releases forvaltet. Release-status
registreres fortsatt eksplisitt gjennom dagens tjeneste; Recording-claims er
ikke grunnlaget for å automatisk avlede Release-status. 6A innfører ingen ny
Release-lifecycle, felter eller migrasjoner.

## Metadataautoritet – kontrakt for 6B

- Ordinær, aldri forvaltet musikk beholder dagens FLAC-/OneTagger-/ingest-regler.
- Pågående onboarding beskytter den kanoniske katalogetableringen.
- Forvaltet og faktisk historisk forvaltet katalog er databaseautoritativ for
  kanoniske katalogopplysninger, også etter opphør av aktuelle rettigheter.
- Recording- og Release-autoritet skal følge hvert sitt objekt.
- Rights-beslutninger skal aldri automatisk gjøre historisk forvaltet materiale
  ordinært/filautoritativt igjen.

6A endrer ingen ingest-/synkroniseringskode. Bevaring av ManagedRecording beholder
det eksisterende Recording-vernet. Release-beskyttelse og den fullstendige
objektvise autoritetsregelen implementeres i 6B. Quick Tag, generell DB→FLAC-
synkronisering og endret radio-/OneTagger-autoritet er utenfor 6A.

## Før videre faser

6B må konkretisere hvilke kanoniske felter og koblinger som tilhører hvert objekt.
6C må avgjøre rettighetsomfang per territorium/dato og hvordan blant annet
null/ukjent eierandel og kvalifiserende distribusjonsgrunnlag skal vurderes.
Lokal organisasjonsidentitet må ikke byttes ukritisk: eksisterende claims peker
til Party, mens konfigurasjonen ikke har historikk for tidligere operatørvalg.
Ved manglende/uklar historikk skal automatisk tilbakeføring fortsatt være sperret.
