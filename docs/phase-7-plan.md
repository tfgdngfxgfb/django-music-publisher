# Forslag til fase 7 – identiteter og krediteringer i KRN DMA

Dato: 24. september 2026. Status: **planforslag, ikke implementert eller låst**.

## Mål

Fase 7 skal gjøre det mulig å vite hvem en kreditering viser til, gjenbruke
riktig person/organisasjon/artist og korrigere feil identitetskoblinger uten
å miste opprinnelig kreditering eller provenance. Katalogarbeid skal kunne
fortsette selv om identiteten ennå ikke er avklart.

Eksempel:

```text
Trykt på utgivelse A: «K. Hansen» ─┐
Trykt på utgivelse B: «Kari Hansen» ├─ kontrollert kobling → ArtistIdentity
Kildenes opprinnelige tekst beholdes┘                         ↓
                                                           Party
```

Et navnetreff er et forslag. En kontrollert identitetskobling er en
beslutning. Ingen av delene dokumenterer mastereierskap eller forvaltning.

## Faktisk utgangspunkt

Gjennomgangen er gjort mot `c2fdab028e874789051136de4b4abdb4662b4aca` på
`fix/gui-workflow-security-review`. Lokal `develop` peker fortsatt på
`44a36707142d2680df52a5adab2a2ce528815460`. Remote-status er ikke undersøkt
på nytt som del av denne planleggingen.

| Finnes i dag | Betydning for fase 7 |
| --- | --- |
| `Party`: permanent UUID, navn, PERSON/ORGANIZATION/GROUP | Utvid eksisterende identitet; ikke opprett et nytt konkurrerende personregister. |
| `ArtistIdentity`: UUID, display_name, obligatorisk Party | En Party kan ha flere artistidentiteter. Dagens modell forbyr flytting av eksisterende ArtistIdentity til en annen Party ved vanlig lagring. |
| `RecordingContribution`: rolle, Party, ArtistIdentity, credited_as, display_order, source_record | God basis. Kobling til bare tekst er tillatt og må fortsatt fungere. |
| `ReleaseTrack`: kobler Release og Recording, har egen sportittel | Har ingen egen krediteringsforekomst. Trykt tekst for forskjellige utgivelser kan ikke uten videre ligge i ett globalt Recording-felt. |
| `MetadataAssertion`: støtter PARTY og ARTIST_IDENTITY som objekttyper | Provenance kan gjenbrukes. Dagens generelle apply-service støtter bare Recording-tittel og -språk; Party-apply finnes ikke allerede. |
| `catalogue.ExternalIdentifier`: Recording eller Release | Har nyttige namespace-prinsipper, men kan ikke i dag brukes som Party-/ArtistIdentity-identifikator. |
| Workbench tilbyr lister og enkle opprettelsesskjemaer | Native v2-workspace, identitetsavklaring og reell historikk mangler. |
| `gui_v2.services._replace_credits()` | Kan slette og opprette krediteringsrader på nytt; ny rad får ikke med gammel source_record. Dette må erstattes av bevarende endringslogikk. |
| Navnematching i `gui_v2.services`, `gui_v2.forms` og `flac_ingest.services` | Et enkelt eksakt navnetreff kan bli en identitetskobling. Det er svakere enn den foreslåtte fase-7-kontrakten. |

Det brede arkitekturdokumentet beskriver også fremtidige objekter som ennå
ikke er implementert. Det skal ikke brukes som bevis for at slike modeller
allerede finnes eller må bygges i sin helhet i fase 7.

## Kontrakt som bør vedtas i 7A

1. **Party, artistidentitet og kreditering er forskjellige objekter.** Et
   artistnavn er ikke automatisk en juridisk part. Et band og et selskap med
   samme navn likestilles ikke. Ett scenenavn er heller ikke bare en alternativ
   skrivemåte når det representerer en egen offentlig artistidentitet.
2. **Navn er ikke unike identifikatorer.** To personer eller artistidentiteter
   kan hete det samme. Navnelikhet, én kandidat i databasen eller en delt
   utgivelse skal aldri alene opprette/bekrefte identitetslikhet.
3. **Normalisering brukes til søk.** Original tekst bevares. Forkortelser,
   translitterering og tegnsettingsvarianter gir forslag med forklaring.
   Sammensatte navn skal ikke automatisk splittes på «og», «&» eller komma.
4. **Fysisk kreditering hører til sin kilde/katalogkontekst.** En endring på
   Release A skal ikke overskrive den trykte krediteringen på Release B.
   RecordingContribution beskriver bidrag til selve innspillingen.
5. **Uavklart er tillatt.** Kreditering kan registreres uten Party eller
   ArtistIdentity. Brukeren kan velge «avklar senere». Ingen fiktiv «ukjent
   person» opprettes bare for å fylle en obligatorisk FK.
6. **Identitetsvalg skjer med UUID.** Autocomplete kan vise navn, type og
   kontekst, men innsendt canonical target er UUID, ikke navnetekst.
7. **Provenance og beslutninger bevares.** Endringer registrerer før/etter,
   bruker, begrunnelse og relevant kilde. Flere kilder kan dokumentere samme
   kobling uten å skape flere additive bidrag.
8. **Rettigheter følger ikke navnevalg.** Ingen automatisk onboarding,
   claim-bekreftelse, holder-/grantor-bytte eller lifecycle-reconciliation
   følger av identitetsarbeid. Scope- og ownership-reglene fra fase 6 består.
9. **Ingen stille massekorreksjon.** Endring av en brukt Party/ArtistIdentity
   viser berørte krediteringer og relevante avhengigheter, med tilgangsfilter.
   Historiske UUID-er og beslutninger kan fortsatt spores.
10. **Eksisterende kobling er ikke bevis på tidligere kontroll.** Migrasjonen
    skal ikke fabrikere bekreftelser eller audit-hendelser for gamle koblinger.

## Avgrenset modellarbeid

Det anbefales additive, kildeuavhengige utvidelser. Eksakte navn og constraints
avklares i 7A før migrasjonene skrives.

| Behov | Anbefalt løsning |
| --- | --- |
| Alternative navn | Typede navnevarianter knyttet til Party eller ArtistIdentity, med kilde. Canonical navn og søkealias skilles. Alias innebærer ikke same-person-beslutning på tvers av objekter. |
| Eksterne identifikatorer | Typet Party-/ArtistIdentity-assignment med scheme, namespace/utsteder/source-instance der relevant, original og normalisert verdi samt provenance. Verifisert canonical assignment skilles fra uavklarte eksterne påstander. Kollisjon skal til kontroll, ikke auto-merge. |
| Ulik trykt kreditering på samme Recording | En liten ReleaseTrack-krediteringsforekomst, eksempelvis `ReleaseTrackCredit`, med original tekst, oppgitt rolle/rekkefølge, kilde og valgfri kobling til relevant RecordingContribution. Ikke en parallell person- eller Recording-katalog. |
| Sporbare identitetsavgjørelser | Gjenbruk SourceRecord/MetadataAssertion der semantikken passer. Et append-only beslutningsspor for kobling/frakobling/avvist kandidat/konsolidering må angi eksakte targets og før/etter; Django LogEntry alene er ikke hele domenekontrakten. |

Identifikatorskjemaer må ha eksplisitt måltype: en ID for en artistprofil er
ikke automatisk en ID for personen eller selskapet bak. Unikhet og antall
tillatte ID-er bestemmes per skjema; katalogens ISRC-regler kopieres ikke.
Ukjente kilde-ID-er beholdes som observasjoner fremfor å gis oppdiktet validering.

Ikke bygg personfødselsdata, adresser, komplett bandmedlemskap, CRM,
organisasjonshierarki eller Person/Organization-undertabeller uten et konkret
behov. Release-level hovedartistkreditering og fysisk tekst på spornivå er
forskjellige ting; en albumartist skal ikke utledes ved å summere sporartister.
Full Release-level credit-modell kan behandles separat dersom pilotcasene
ikke krever den i fase 7.

## Gjennomføring

| Del | Konkret leveranse | Avhengighet | Akseptanse før neste steg |
| --- | --- | --- | --- |
| Startport | Gjennomgå og godkjenn nyere GUI-/feilrettingsarbeid; integrer på avtalt develop-baseline og noter eksakt SHA | Egen integrasjonsbeslutning | Fase 7 starter fra riktig, verifisert pilotkode. Master forblir urørt. |
| **7A – kontrakt og kartlegging** | Kartlegg alle Party-/ArtistIdentity-referanser, navnebaserte write paths og krediteringskontekster. Lag read-only datarapport og representative cases. Lås modell-/workflow-avgrensningen over. | Startport | Eksplisitt migrasjonsplan, rettighetsmatrise, konsolideringsregler og tester for identitetsinvariants. Ingen automatisk datarydding. |
| **7B – modell og bevarende services** | Implementer nødvendige navn/identifier-/krediteringsutvidelser og beslutningsspor. Erstatt slett-og-gjenopprett av credits. Innfør expected_revision ved mutasjoner. | 7A | Gamle UUID-er/kilder består; redigering av ett bidrag mister ikke andre bidrag eller source_record; stale endring avvises. |
| **7C – søk, kandidater og kontrollert kobling** | Én felles kandidatservice og autoriserte link/unlink/correct-workflows. Resultater forklarer hva som matchet. Fjern implisitt identitetsbekreftelse via navn fra GUI og FLAC-ingest. | 7B | Like navn forblir forskjellige uten beslutning; gjentatt apply er trygg; feil kobling kan korrigeres med historikk; uavklart kreditering kan beholdes. |
| **7D – native GUI v2** | «Artister og parter» med kompakt liste, filtre, inspector og objektworkspace. Felles UUID-velger i Recording, Release/spor, Digitalisering og relevante rights-/avtaleskjemaer. | 7B/7C | K. Hansen kan knyttes til riktig Kari Hansen uten å endre covertekst; bruker ser samme navn hos flere kandidater, roller/kontekst og tydelig avklar-senere-valg. |
| **7E – dubletter og korreksjon** | Sammenlign kandidater, dokumenter «samme»/«forskjellige», forhåndsvis berørte referanser og tilby snevert avgrenset konsolidering. | 7C/7D | Ingen blind FK-omskriving, sletting av historikk eller stilltiende effekt på rights. Blokkeringer forklarer hvilke avhengigheter som krever separat behandling. |
| **7F – integrasjon og overgang** | Koble identitetsfakta til 6I/6J/6K, gjennomgå eldre write paths og migrer de erstattede Party-sidene fra Workbench. | 7B–7E | Ingen parallelle identitetsregler; filtre/telling lekker ikke skjulte data; eksisterende FLAC-/katalogflyt består. Standardgaps lukkes bare der implementasjon og test beviser mappingen. |
| **7G – konsolidering og låsing** | Oppgraderingsprøve, brukerakseptanse, regresjon, avtalt PostgreSQL/CI og normativ sluttdokumentasjon på samlet baseline. | Alle deler | Hele kombinasjonen er verifisert, ikke bare enkeltbrancher. Eksakt kode- og dokumentasjonscommit registreres. |

Rekkefølge: `startport → 7A → 7B → 7C → 7D → 7E → 7F → 7G`.
UX-skisser og innsamling av kildeeksempler kan gjøres mens grunnlaget
avklares. Endringer i samme modeller og workflows bør bygges sekvensielt;
unngå parallelle implementasjoner av ulike identitetsregler.

### Første implementasjonsleveranse

Etter at 7A er vedtatt: lever bevarende krediteringsendring og eksplisitt
UUID-valg først. Det løser konkrete svakheter i dagens digitaliseringsflyt
før et større nytt workspace bygges. Utvid deretter til flere kildeformer,
navnevarianter og identifikatorer i henhold til den avtalte 7B-kontrakten.

## Særlig om 7E: identitetsavklaring er ikke fri sammenslåing

Anbefalt første versjon:

- Feil kobling på én kreditering kan korrigeres eller fjernes med audit.
- To dublerte ArtistIdentity-poster som tilhører **samme Party** kan omfattes
  av kontrollert konsolidering når alle avhengigheter er kartlagt. Gammel
  UUID beholdes som sporbar referanse, og separate reelle artistidentiteter
  skal kunne bestå selv om de tilhører samme Party.
- To Party-poster kan undersøkes og få en dokumentert identitetsavgjørelse.
  En slik avgjørelse skal ikke generelt omdefinere likhet mellom Party-ID-er
  i rights-resolveren.
- Full Party-konsolidering som berører RightsClaim, AgreementParty,
  RightsConfiguration eller andre juridiske referanser utsettes til et eget
  avgrenset arbeid. GUI viser berørte områder og hva som må behandles.
  Avgjørelser om holder/grantor følger eksisterende rights-workflows.
- Den eksisterende sperren mot å flytte ArtistIdentity mellom Party-er
  omgås ikke med QuerySet-update. En korrekt annen identitet opprettes/velges,
  og de konkrete feilaktige koblingene behandles eksplisitt.

Dette er en bevisst grense for fase 7: identitetsavklaring og trygg
katalogkonsolidering inngår; en generell motor som omskriver juridisk historikk
gjør ikke det. En feil sammenslåing skal kunne korrigeres gjennom nye
beslutninger; ikke lov «ett-klikk undo» etter senere avhengige endringer.

## GUI og arbeidsflyt

Objektworkspacet viser oversikt, navn/identifikatorer, artistidentiteter,
krediteringer, kilder og faktiske beslutninger. Juridiske relasjoner er
permission-filtered lenker til eksisterende rights-/avtalearbeid.

I en sporrad vises eksempelvis:

```text
Kreditert på denne utgivelsen: K. Hansen
Koblet artist:                 Kari Hansen
Person/organisasjon:           Kari Hansen · Person · kort UUID
Kobling:                      Kontrollert [kilde/beslutning]
Handling:                     Endre kobling / Fjern kobling
```

Den vanlige flyten blir:

`registrer original tekst → se kandidater → velg eksisterende / opprett
eksplisitt / avklar senere → forhåndsvis konsekvens → lagre med audit`.

Massekobling tillates bare for eksplisitt valgte forekomster med preview,
ny validering og samlet anvendelse. «Alle som heter Hansen» er ikke et gyldig
automatisk konsolideringsgrunnlag. Metadataarbeid skal ikke starte filskriving
eller Radio-FLAC-generering som skjult sideeffekt.

## Tilgang, audit og samtidighet

- Skill visning, redigering, identitetsavklaring og konsolidering med
  konkrete tillatelser. Nye koder som `resolve_identity` og
  `consolidate_identity` er forslag som fastsettes i 7A.
- Sjekk relevant Recording-/Release-/credit-tilgang ved kobling, også ved
  direkte POST. Rett til Party-redigering gir ikke rett til å endre claims.
- Kilde-/avtaledetaljer krever sine egne tillatelser. Filtrer før kandidater,
  telling og paginering. Konsolidering kan ikke skjult endre referanser
  brukeren mangler nødvendig tilgang til; vis en generell blokkering.
- Gjenbruk revisjoner, men kontroller forventet revisjon: automatisk økning
  av revisjon alene oppdager ikke at en bruker lagrer et gammelt skjema.
- Preview må revalideres mot alle berørte referanser og revisjoner ved apply.
  Planen er brukerbundet og utløper. GET skal ikke endre identiteter.
- Beslutninger bevares. Avvist kandidat gjelder den dokumenterte koblingen/
  konteksten; den skal ikke forby alle fremtidige navnelike kandidater.

## Dataovergang

7A-rapporten teller navnelike objekter, uavklarte krediteringer, inkonsistente
Party-/ArtistIdentity-koblinger og referanser fra rights, katalog, filer og
provenance. Dette er kandidater til kontroll, ikke en automatisk fasit.

Migrasjonene er additive. Ikke tildel bekreftede aliaser eller identifikatorer
ved navnelikhet. Behold eksisterende credited_as/source_record. Når gammel
Recording-kreditering ikke kan tilordnes en bestemt utgivelse med grunnlag i
eksisterende data, forblir den på Recording-nivå med uavklart kildekontekst.
Ikke kopier den ut til alle ReleaseTracks og kall dette trykt coverinformasjon.

Ny kode må håndtere gamle data før eventuell manuell avklaring er gjennomført.
Oppgraderings- og gjenopprettingsprøve skjer mot en isolert kopi, ikke ved
opprydding direkte i pilotdatabasen.

## Integrasjon uten scope-glidning

- **6I:** identitetssignaler er avledet fra canonical facts og beslutninger.
  «Uten Party» er ikke alltid et avvik; ukjent instrumental kreditering eller
  bevisst uavklart data kan være legitimt. Ingen ny persistent oppgavekø.
- **6J:** kilde-ID og credit går til observasjon/kandidat/preview. Samme navn
  eller endret kildeinnhold gir ikke automatisk canonical oppdatering.
- **6K:** test nøyaktig hvilke identitets- og contributor-gaps som er løst.
  Behold PHASE_7/MISSING/LOSSY for gjenstående betydning; ikke endre alle
  PHASE_7-flagg samlet. Party-ID løser ikke InitialProducer eller mandatsemantikk.
- **Fase 8:** en Party kan være koblet til en komponistkreditering uten at
  dette etablerer Work, authorship, writer shares eller publishing authority.
- **GUI v1:** fas ut bare de erstattede Party-/ArtistIdentity-inngangene med
  kompatible leselenker. Workbench som helhet og DMP består.

Utenfor fase 7: produksjonsimport fra de eksterne katalogene, RDR-N-/CWR-filer,
transport, royalty, Work-modell, komplett juridisk Party-merge, komplett fysisk
arkivmodell og nye regler for ownership, management eller media lifecycle.

## Test- og akseptansekontrakt

| Scenario | Påkrevd resultat |
| --- | --- |
| To Party-er eller artistidentiteter har samme navn | Begge består; brukeren får forklarte kandidater. Ingen automatisk kobling. |
| «K. Hansen» velges mot Kari Hansens UUID | Original tekst består, koblingen spores og kan korrigeres. |
| Artistidentitet tilhører en annen Party enn innsendt Party | Hele endringen avvises; ingen delvis lagring. |
| Samme Recording på to utgivelser med ulik trykt kreditering | Begge kildetekstene består. Redigering på A endrer ikke B. |
| En credit endres eller rekkefølge endres | Uendrede rader/UUID-er/provenance består; faktisk endring får audit. |
| Flere kilder dokumenterer samme bidrag eller identifikator | Flere bevis, ikke automatisk flere bidrag eller identiteter. |
| Ny kilde motsier en tidligere identitetsavgjørelse | Ny observasjon/avklaring, ingen overskriving av råkilde/historikk. |
| Identifikator har ugyldig måltype, format eller kollisjon | Avvis canonical assignment eller send til konkret kontroll; ingen auto-merge. |
| Gammel preview eller to samtidige endringer | Ny validering/revisjonskontroll blokkerer stale apply; ingen halv konsolidering. |
| Party brukes i claims/avtaler/lokal organisasjon | Generell konsolidering blokkeres; holder, grantor, shares og lifecycle er uendret. |
| Begrenset bruker søker eller åpner direkte URL/POST | Ingen skjulte detaljer, tellinger eller uautoriserte mutasjoner. |
| Eksisterende database oppgraderes | UUID-er, source payloads, eldre koblinger, filvalg og rights-history består. |
| Fase 7 kombinert med låst fase 6 og pilot-GUI | Regresjoner på samlet commit er grønne før DONE/locked. |

Under utvikling: målrettede tester, SQLite der semantikken tillater det,
`manage.py check`, migrasjonskontroll, formattering og nettlesertester for
brukerflytene. Ren kandidatvurdering testes separat fra ORM/batchlasting.

Constraints, transaksjoner og samtidighet krever planlagte PostgreSQL-tester
for blant annet identifikatorunikhet og stale/concurrent apply.
Repositoryets [AGENTS.md](../AGENTS.md) sier at slike kjøringer skal
forklares og autoriseres før de startes. Endelig full lokal regresjon og
autoritativ CI kjøres på den felles consolidation-commiten etter avtale.

## Foreslått gjennomføringsstyring

Bruk én oppdatert `develop`-baseline. Opprett korte brancher som
`feature/phase-7a-identity-contract`, `feature/phase-7b-identity-foundation`
osv. fra den sist godkjente integrasjonen. Hver del leverer kode der aktuelt,
tester, migrasjonsvurdering, kort demo og oppdatert kontrakt.
Merge og sletting av ferdige brancher følger egen godkjenning og ancestry-
kontroll. Ingen endring av master følger automatisk av fase 7.

Planlegg én delleveranse om gangen. Kalendertid bør estimeres etter 7A når
mengden legacykoblinger og migrasjonsbehov er kjent; særlig 7B og 7E kan ikke
estimeres forsvarlig bare fra antall skjermbilder.

## Foreslått DONE/locked-statement

> Fase 7 er godkjent når KRN DMA har ett konsistent, kildebelagt og operativt
> identitets- og krediteringsgrunnlag over Party, ArtistIdentity og
> RecordingContribution. Original kreditering og riktig utgivelseskontekst
> bevares, navnelikhet gir forslag fremfor automatisk sannhet, identitetsvalg
> og korreksjoner skjer gjennom autoriserte og auditerte workflows, og GUI v2
> gir en sammenhengende arbeidsflate. Avgrenset konsolidering bevarer UUID-er
> og historikk uten å omskrive juridiske posisjoner. Eksisterende fase-6-
> semantikk består, og den samlede implementasjonen har grønn avtalt lokal
> verifikasjon og autoritativ CI på den eksakte consolidation-baselinen.

## Underlag i repositoryet

- [Eksisterende identitetsmodeller](../parties/models.py)
- [Katalog og krediteringer](../catalogue/models.py)
- [Dagens krediteringslagring](../gui_v2/services.py)
- [Dagens FLAC-kobling](../flac_ingest/services.py)
- [Provenance-service](../provenance/services.py)
- [Låst fase 6](phase-6-done.md)
- [6K-gaps](phase-6k-interoperability-foundation.md)
- [Overordnet utviklingsplan](krn-dma-development-plan.md)
