# Fase 4.1 — FLAC-ingest hardening

## Reelt testkorpus og observerte former

Den tilgjengelige lokale testmappen inneholdt 1 842 FLAC-filer. En komplett
skrivebeskyttet skann mot en kopi av katalogdatabasen tok 39 sekunder. Ingen
lydfiler ble endret.

- Alle filer hadde `TITLE`, `ARTIST`, `ALBUM`, `TRACKNUMBER` og `ISRC`.
- `ARTIST`, `GENRE` og `COMMENT` forekommer som flerverdier. Opptil fire
  sjangerverdier og fire kanalverdier ble observert.
- Kanal, målgruppe, språk og kjønn ligger hovedsakelig som
  `TXXX:<felt> - <verdi>` i `COMMENT`. Direkte `KANAL`, `TARGET` og `LANGUAGE`
  forekommer også.
- `RATING` bruker OneTagger-skalaen 20/40/60/80/100. `RATING WMP` bevares som
  råtag, men er ikke katalogautoritet.
- `DATE` er hovedsakelig full dato. Observerte `TRACKNUMBER` og `DISCNUMBER`
  er heltall; parseren støtter også `03` og `3/12`.
- `P7UUID` finnes i 1 643 filer. Ukjente tags og alle råverdier bevares.

## Bugregister

| Problem | Årsak | Rettet | Regresjonstest / begrensning |
|---|---|---|---|
| Kanal og målgruppe manglet | StationPlaylist lagret TXXX-data i `COMMENT` | Adapteren leser embedded TXXX og beholder rådata | Direkte adaptertest og reell filprøve |
| Energy ga unødvendig konflikt | OneTagger bruker 20-trinn | 20/40/60/80/100 mappes til 1–5 | Test av alle fem nivåer |
| `Afrikanske språk` ble avvist | OneTagger bruker en kontrollert samlebetegnelse, ikke en språkkode | Verdien godtas og bevares som radiometadata og rå kildeverdi | Adapter- og modelltest |
| Flere sjangre ble redusert til én | `GENRE` ble lest som enkeltverdi | Alle verdier bevares og lagres semikolonseparert i dagens felt | Test med to sjangre; feltet er fortsatt tekst |
| Én ødelagt/utilgjengelig fil kunne stoppe skann | Lesing, stat og hash var ikke isolert | Forventede filfeil registreres per fil | Test med ødelagt FLAC og lesbar nabofil |
| Fil kunne endres under skann | Ingen kontroll før/etter lesing | Størrelse og nanosekund-mtime kontrolleres | Ustabil fil får «Prøv igjen» |
| Fil kunne forsvinne før import | Preview ble brukt uten ny kontroll | Hash, størrelse og mtime verifiseres før canonical writes | Nabofil importeres; forsvunnet fil isoleres |
| Én datakonflikt kunne stoppe resten av apply | Batch manglet forventet feilgrense | Hver fil bruker egen transaksjon; forventede konflikter logges på raden | Batchtesten bevarer andre filer |
| Store resultater var tunge å kontrollere | Få filtre og ny skann krevde ny start | Nye/endrede/uendrede/advarsler/konflikter/feil kan filtreres; hele, feilede eller valgte filer kan skannes på nytt | Workbench- og permissionstester |
| To databaseoppslag per filplassering | Plassering ble slått opp inne i løkken | Aktuelle plasseringer lastes én gang, med chunking for valgte filer | Reell skann: 1 842 filer på 39 sekunder |
| Anvendte FLAC-verdier fylte listen «Uverifiserte opplysninger» | Kildepåstanden beholdt standardstatus etter at autoritetsregelen allerede hadde anvendt verdien | Nye anvendte FLAC-påstander bekreftes med beslutningshistorikk. Eldre FLAC-påstander kan bekreftes eksplisitt fra Kontroll | Test skiller FLAC-metadata fra andre kilder og rettighetskrav |
| Radioposten sto som uverifisert etter vellykket FLAC-import | Påstandene ble bekreftet, men `MusicLibraryEntry` beholdt gammel status | Anvendt FLAC-radiometadata setter radioposten til bekreftet | Regresjonstest ved ny import og etterbekreftelse |
| Fjernede radio-tagger ble liggende i databasen | Manglende tag ble tolket som «ingen oppdatering», og eksplisitt re-scan kunne hoppe over en uendret fil | Ny lesing av en registrert radio-FLAC speiler også fravær: sjanger/språk/Energy/kjønn tømmes og kanal-/målgrupperelasjoner fjernes. En eksplisitt enkeltfil-re-scan leser alltid filen på nytt | Syntetisk test legger til og fjerner `GENRE`/`KANAL`, og reparerer også en gammel restverdi uten ny filendring |
| Tom testdatabase avviste kjente `P7UUID` | UUID-en tilhørte en tidligere testdatabase | Superbruker kan uttrykkelig aktivere gjenoppretting og opprette Recording med samme UUID; ISRC-motstrid forblir konflikt | Fire tester dekker normal avvisning, gjenoppretting, idempotens, ISRC-konflikt og serversidetilgang |
| Utgivelser ble sjelden opprettet | Release-matching krevde for mye komplett tagmetadata, og allerede leste filer ble hoppet over | Albumfolder brukes som stabil importkontekst. Disc/CD-undermapper grupperes under parent, cover registreres som filreferanse, og en versjonsmarkør gjør at eldre filer behandles én gang for etterregistrering | Tester dekker manglende `ALBUM`, flere discer, etterregistrering, samme albumtittel i ulike mapper og cover |
| Uforvaltede innspillinger viste irrelevant rettighetskontroll | Recording-siden viste krav uavhengig av operativt repertoar | Rettighetsfanen og rettighetshandlinger skjules og avvises for uforvaltet musikk. Innmelding i Forvaltet musikk krever valg av lokalt eierskap, administrasjon eller distribusjon | Permission-, service- og Workbench-tester |

Automatisk bekreftelse gjelder bare metadata som ingest faktisk anvender etter
autoritetsreglene: katalogmetadata for ikke-forvaltet musikk og radiometadata
for alle innspillinger. FLAC-ingest kan aldri bekrefte eierskap eller andre
rettighetskrav.

## Resultat og kjente begrensninger

Den nyeste skrivebeskyttede forhåndsvisningen mot en kopi av den aktive
demodatabasen brukte 1 842 filer og eksplisitt UUID-gjenoppretting. Den ga
1 296 nye, 513 oppdateringer/etterregistreringer, åtte eksisterende treff og
25 konflikter. 1 214 forslag kunne gjenbruke UUID fra fil, og 299
albumfoldere ble identifisert. To av radene skyldtes OneTagger-verdien
`Afrikanske språk`, som nå støttes. De øvrige konfliktene var reelle
P7UUID/ISRC-motstrid og usikre treff i kildekatalogen; de er ikke
programfeil og skal ikke føre til automatisk sammenslåing. Ingen originalfiler
ble skrevet.

Den tidligere realistiske skannen mot den eldre katalogkopien ga 94 nye, 19
eksisterende treff, 944 endrede, 686 uendrede, én advarsel, 99 konflikter og
ingen lesefeil. Konfliktene var
handlingsbare: 86 usikre metadata-treff og 11 P7UUID/ISRC-konflikter. De skal
ikke slås sammen eller godkjennes automatisk. To tidligere språkavvik for
`Afrikanske språk` er ikke konflikter etter denne rettelsen.

Skanningen kjøres fortsatt synkront. Knappen låses og viser arbeidsstatus, men
det finnes ikke live prosentvis fremdrift før svaret er ferdig. Permanent
watcher, køsystem og automatisk bakgrunnsskann er fortsatt utsatt. Normal
innlesing avviser fremdeles P7UUID-er som peker på en annen katalogbase;
gjenoppretting må aktiveres uttrykkelig av superbruker og er merket for ny
vurdering før produksjonsbruk.

Rettighetskrav lagres fortsatt mot Recording, siden masterretten gjelder
innspillingen og samme Recording kan finnes på originalutgivelse, gjenutgivelse
og samlealbum. Arbeidsflyten kan nå startes fra en Release: brukeren velger
berørte spor og oppretter like krav med felles kilde/avtale i én transaksjon.
Samme Recording på flere spor behandles én gang. Bare administrator kan samtidig
føre en uforvaltet Recording inn i Forvaltet musikk, og kravet må da gjelde
konfigurert lokal organisasjon. Selve Release arver eller beviser ikke eierskap.

## Verifikasjon

De målrettede FLAC-ingesttestene dekker blant annet ødelagte og
tagløse filer, lange felt, endring under lesing, forsvunnet fil, idempotens,
autoritetsregler, tillatelser, etterregistrering av Release-struktur og
bekreftelse av anvendte FLAC-opplysninger. Rettighetsflyten fra Release er
testet for atomisk opprettelse og serversidetilgang. `manage.py check`,
migrasjonskontrollen og Ruff er uten feil. Den avsluttende samlede
regresjonskjøringen bestod: 230 tester på SQLite. Etter avklaringen av
OneTagger-samlebetegnelsen bestod 43 målrettede ingest- og
musikkarkivtester. DMP-koden ble ikke endret og inngår i regresjonskjøringen.
PostgreSQL fullsuite ble ikke gjentatt fordi migrasjonene bare endrer et
databaseuavhengig boolsk felt og en validator, og ingen PostgreSQL-spesifikk
kode er endret.

En innlogget nettleserprøve kontrollerte Musikkarkiv, Utgivelsesliste,
utgivelsesdetalj med spor og cover, samt skjemaet «Registrer rettigheter» med
forhåndsvalgte unike innspillinger. Ingen rettighetsdata ble lagret i prøven.
