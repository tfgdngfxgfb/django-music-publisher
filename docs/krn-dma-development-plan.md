# KRN DMA – utfasing og videre utvikling

Vurdert 24. september 2026 mot kodebasen på
`fix/gui-workflow-security-review`, med `f2ca662` som utgangspunkt og
feilrettingene i samme arbeidsrunde. Dette er et prioritert forslag,
ikke en beslutning om å slette funksjoner eller starte nye domeneprosjekter.

## Anbefaling

Fullfør stabilisering av den daglige arbeidsflyten før nye integrasjoner.
Fas ut overlappende brukerflater etappevis. Bevar katalog, rettigheter,
provenance og mediehistorikk. GUI v2 skal bli selvstendig som normal
arbeidsflate; det krever først at de gjenværende Workbench-avhengighetene
er erstattet eller bevisst beholdt som administrasjonsverktøy.

## Hva kan fases ut?

| Område | Vurdering | Anbefalt handling og vilkår |
| --- | --- | --- |
| GUI v1: parallelle lister for Musikkarkiv, Utgivelser og Forvaltet musikk | Gode kandidater for utfasing når tilsvarende oppgaver er dekket i v2 | Kartlegg handlinger og innkommende lenker. Flytt navigasjon til v2. La gamle GET-adresser videresende med bevart filter/objektkontekst før maler og views fjernes. |
| GUI v1: Recording- og rights-visninger | Betydelig overlapp, men ikke fullstendig overflødige | V2 har egne Recording-workspaces og rights-workflows. Den gamle Recording-siden brukes fortsatt for blant annet kildehistorikk. Erstatt gjenværende behov først; fjern deretter dobbelt presentasjonsarbeid. Nye sikkerhetsfunn i v1 må rettes mens siden er tilgjengelig. |
| Parallelle skjemaer for samme katalog-/rights-handling | Dobbelt vedlikehold og risiko for ulike regler | Samle autorisasjon og domenekall. Pensjoner gamle POST-ruter først når ingen brukerflyter avhenger av dem. En POST skal ikke ukritisk videresendes til en annen mutation. |
| «Ny digitaliseringsbatch» og manuell FileAsset-registrering som vanlig brukerinngang | Den tekniske inngangen er erstattet av «Start ny digitalisering» og filvelgeren | Bruk den nye inngangen i normal navigasjon. Behold relevante admin-/systemmuligheter for reparasjon og import; ikke slett DigitizationBatch eller FileAsset. |
| Separate, gjentatte mastervalg når én master allerede er valgt | Unødvendig i den enkle flyten | Vis valgt master og la brukeren gå videre. Behold koblingssteget for RAW, tvetydige forslag, flere mastere og eksplisitt bytte. |
| Uvirksomme integrasjonsinnganger | Planlagte funksjoner er ikke operative funksjoner | Samle «ikke konfigurert» under en kompakt forklaring/innstillinger. Ikke bygg en tilsynelatende fungerende NCB-/TONO-/Gramo-import uten reell adapter og testdata. |
| Tekniske navn som `prototype.js` og interne `P7_*`-nøkler | Navnene er gamle; funksjonene er fortsatt aktive | Lav prioritet. En senere navneopprydding må bevare konfigurasjonskompatibilitet. KRN DMA-identiteten krever ikke nye databaseidentiteter eller omdøping av P7 som rettighetshaver. |

### Dette bør ikke fjernes nå

- **Hele `workbench`-appen.** Hovedmenyen i GUI v2 peker fortsatt til
  Artister/personer, Filer og Kontroll der. FLAC-innlesing og enkelte
  kataloghandlinger bruker også Workbench. GUI v2 bruker dens cover-endepunkt
  og importerer `RadioMetadataForm` fra `workbench.forms`.
- **Django admin.** Bruker-/gruppeadministrasjon og avanserte driftsoppgaver
  har ikke fullstendige native erstatninger.
- **Eksisterende `music_publisher`/DMP, Work og CWR.** Dette er et separat
  publishing-subsystem, ikke dokumentert ubrukt kode. Fase 8 må avklare
  integrasjon eller migrasjon før eventuell utfasing.
- **Legacy correction, eksplisitt tilbakeføring, superseding og historikk.**
  Dette er nødvendige domenefunksjoner, også når navnene inneholder «legacy».
- **6J-/6K-readiness, source-profiler og versjonerte standardadaptere.**
  De er grunnlag for senere integrasjoner, ikke feilaktig forlatt funksjonalitet.
- **Ukjent lineage og historiske radiofiler.** Ukjent opphav skal fortsatt
  være en ærlig tilstand. Gamle filer og provenance skal ikke slettes for å
  få oversikten til å se enklere ut.

Det finnes ikke bruksstatistikk i denne vurderingen som beviser at gamle
endepunkter er ubrukte. Kallsteder og faktisk pilotbruk må kontrolleres før
sletting. Lav bruk alene er heller ikke grunn til å fjerne en reparasjonsfunksjon.

## Prioritert utviklingsløp

### 1. Stabil daglig bruk og sikker redigering

Første leveranse bør konsolidere feilrettingene fra pilotbruken:
digitalisering, filvelger, masterkoblinger, avspilling og innstillinger.

- Nettleserregresjon av hele løpet, inkludert tilbake/frem, gjentatt
  sidenavigering, trege søk, tastaturbruk og ulagrede endringer.
- Revisjonskontroll ved lagring av metadata og spor. Oppdag at en annen
  bruker har endret data siden skjemaet ble åpnet; vis forskjellen før lagring.
- Knytt lokale utkast til bruker og revisjon. Et gammelt utkast skal kunne
  sammenlignes med dagens data, ikke overskrive dem skjult.
- Gå systematisk gjennom leserettigheter for relaterte kilder, avtaler,
  filplasseringer og audit-data i begge GUI-er.

**Ferdig når:** pilotløpet fungerer uten admin eller muntlig forklaring,
feilkoblinger kan korrigeres sporbart, samtidige endringer blir oppdaget,
og tester med begrensede brukerroller dekker både sider og direkte kall.

### 2. Fullfør GUI v2 og reduser dobbelt vedlikehold

- Lag en konkret matrise over Workbench-funksjon → v2-erstatning →
  gjenværende brukssteder. Prioriter Filer, kildehistorikk og Kontroll.
- Flytt felles skjemaer og cover-serving til et passende felles lag før
  eventuell fjerning av Workbench-moduler. Behold gamle URL-er som
  kompatibilitetsinnganger der dette er nyttig.
- Gjør filvelgeren til en dokumentert felles komponent for RAW, master og
  andre faktiske filvalg, med separate tillatte filtyper/operasjoner.
- Skill «må løses nå» fra «kan følges opp senere» konsekvent i hele GUI-et.
- Hold person-/identitetsarbeid innenfor fase 7; ikke bygg en konkurrerende
  identitetsmodell bare for å erstatte en v1-side.

**Ferdig når:** normal arbeidsflyt er native v2, gamle innholdssider har
verifiserte erstatninger, og utfasing ikke fjerner tjenester/importverktøy
som fremdeles er nødvendige. Slett deretter den dokumentert redundante koden.

### 3. Robust mediebehandling og drift

- Flytt langvarig Radio-FLAC-generering ut av HTTP-forespørsler til
  kontrollerte bakgrunnsjobber med fremdrift, retry og tydelige feilårsaker.
  Gjenbruk `RadioFlacGeneration`; arbeidsstatus skal fortsatt avledes.
- Skilj manglende lagringskonfigurasjon fra utilgjengelig NAS og manglende
  fil. Vis rot, startmappe og anbefalt neste handling.
- Mål filvelger, Radio-FLAC og Oppfølging mot en realistisk stor katalog
  før eventuell ekstra cache/indeksering. Bevar filtrering før paginering.
- Dokumenter og prøv gjenoppretting av database, registrerte filplasseringer
  og konfigurasjon. Gjennomgå faktisk produksjonsoppsett og avhengigheter.

**Ferdig når:** feil i NAS eller generator kan håndteres uten å miste
current/candidate/historikk, dobbeltkjøring er kontrollert, og en backup
er gjenopprettet i et isolert miljø.

### 4. Fase 7 – Party og ArtistIdentity

- Skill person/organisasjon, artistidentitet og fysisk oppgitt kreditering.
- Tilby menneskelig avklaring av kandidater, navnevarianter og identifikatorer.
- Bevar `credited_as`, stabile UUID-er, provenance og referanser ved en
  eventuell eksplisitt sammenslåing. Ingen automatisk sammenslåing på navn.
- Fullfør operativ GUI og migrasjons-/kompatibilitetsregler før eksterne
  identiteter tas inn i større skala.

**Ferdig når:** samme identitet kan gjenbrukes på tvers av utgivelser og kilder
uten å miste historisk kreditering eller blande juridiske parter og artistnavn.

### 5. Kontrollerte importpiloter oppå 6J

Innhenting av representative eksportfiler kan begynne før fase 7 er ferdig.
Velg første produksjonspilot ut fra faktisk tilgjengelige data og brukerbehov.

- Kartlegg én kilde om gangen: Mudi, Orchard, Klango eller LU-MI.
- Bevar rå kilde, namespace for eksterne ID-er og endrede kilderevisjoner.
- Implementer parsing → matching/forslag → preview → autorisert workflow.
- Verifiser gjentatt import, tvetydighet, source-endringer og katalogvern.

**Ferdig når:** en pilot kan gjentas uten utilsiktede dubletter,
metadataoverskriving eller automatisk bekreftelse av rettigheter.

### 6. Fase 8 – Work/publishing og deretter standardutveksling

- Ta eksplisitt stilling til eksisterende DMP og forbindelsen Recording–Work.
  Unngå to konkurrerende Work-/writer-registre.
- Modellér kanoniske verk, opphav, andeler og publishing-scope i riktig domene.
- Prioriter reell RDR-N-/CWR-utveksling først når mottakerkrav, eksempeldata
  og de nødvendige identitets-/publishing-kontraktene er klare.
- Behold versjonerte adaptere rundt domenet og eksplisitte gaps.

**Ferdig når:** konkrete mottakerkontrakter er verifisert mot testfiler og
tap/begrensninger er dokumentert. Et readiness-kart alene er ikke en ferdig
eksport eller integrasjon.

## Gjennomføring og verifikasjon

Arbeid i små featurebrancher fra avtalt `develop`-baseline. Konsolider først
når den konkrete leveransen er godkjent. `master` endres bare etter egen
beslutning. Fase 6 forblir låst; videre arbeid skal bygge på domenekontraktene.

Bruk målrettede tester, SQLite der det er tilstrekkelig, Django-check og
migrasjonskontroll. PostgreSQL-/concurrency-verifikasjon planlegges særskilt
og krever tillatelse etter repositoryets `AGENTS.md`. Ikke erklær neste fase
DONE/locked på grunnlag av en deltest eller en GUI-gjennomgang.

Detaljer om tidligere funn står i [pilotgjennomgangen](review-2026-09-20.md).
Den foreslåtte native innstillingssiden der er nå implementert; neste arbeid
er driftsrobusthet og bedre tilbakemeldinger, ikke å bygge en ny innstillingsside.
Fasegrensene følger [fase 6-statementet](phase-6-done.md).

## Rettet i denne gjennomgangen

- Filvelgeren installerer ikke flere globale hendelsesbehandlere ved gjentatt
  navigering. Søk og mappevalg eies av filvelgeren, og forsinkede/avbrutte
  svar kan ikke overstyre nyere valg. Valg blandes ikke mellom batcher.
- En ventende søkeforsinkelse avbrytes ved eksplisitt søk/mappevalg, og
  avmerkingen for «velg alle» følger de tilgjengelige, synlige filene.
- Nettlesernavigering forkaster gamle svar før sideinnholdet erstattes.
  Digitaliseringssidens globale lyttere ryddes, og radnavigering stjeler
  ikke tastetrykk fra knapper/felt i RAW- og masterlistene.
- Blokkert/full nettleserlagring stopper ikke avspilleren eller sportabellen.
  Ulagrede sporendringer gir et eksplisitt spørsmål før siden forlates
  dersom et lokalt utkast ikke kan lagres. Ugyldig lagret volum får en
  gyldig standardverdi.
- Mappemaler med Windows-skilletegn bruker nærmeste eksisterende overmappe
  korrekt. Også mappemaler fra serverkonfigurasjon valideres før formatering.
- GUI v1 krever separate leserettigheter for kildehistorikk og filplasseringer
  i Recording-visningen. Rights-oversikten skjuler kilde-/avtalenavn uten
  relevante leserettigheter.

Verifisert med 64 målrettede SQLite-tester for innstillinger/digitalisering/
Recording-filer, 39 Workbench-tester og 13 JavaScript-regresjoner.
Django-check, migrasjonskontroll, JavaScript-syntakskontroll og diff-check
var grønne. JavaScript-regresjonene kjører ekte applikasjonsskript med
kontrollerte DOM-/nettverksgrenser, ikke en full nettleser mot pilotdatabasen.
Kjør dem med `node --test gui_v2/js_tests/*.test.cjs`.

Ingen modell-/migrasjonsendring, PostgreSQL-test, full ny sikkerhetssertifisering
eller sletting av eldre funksjoner inngår i denne leveransen.
