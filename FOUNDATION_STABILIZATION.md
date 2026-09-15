# Foundation stabilization — fase 1.5

Dato: 13. september 2026. Prosjektet heter **P7 Archive & Rights / P7 Arkiv og rettigheter**. DMP er det historiske forlagsfundamentet; P7s dataintegritet og arkitektur har prioritet over enkel synkronisering med upstream.

## Kontroll og rettelser

- Kontrollerte UUID-er, relasjoner, slettestrategier, constraints, unikhet, indekser, migrasjonsgraf og SQLite/PostgreSQL-forskjeller. Innspilling er fortsatt uavhengig av Work, ISRC, utgivelse, artist og eier. ISRC er en ekstern identifikator, aldri primærnøkkel.
- Gjennomgikk DMPs Work/Writer-andeler, CWR/ACK, import/eksport, filopprydding, datoer, transaksjoner, sletting og royaltyberegning. Eksisterende admintransaksjoner, PROTECT/CASCADE-valg og CWR-regresjonstester ble beholdt.
- Rettet to konkrete krasj i royaltybehandling: ugyldig beløp og kontrollert andel lik null gir nå en feilrad i resultatfilen i stedet for serverfeil eller divisjon med null.
- Fjernet fast `admin / 123` fra Windows-launcheren. Første kjøring lager et sterkt tilfeldig passord og viser det én gang; senere kjøringer endrer ikke passordet. Eksplisitt `-AdminPassword` er fortsatt mulig lokalt.
- Norsk bokmål er standardspråk. Fase-1-katalogen, partene, valideringsfeil, navigasjonen og sentrale DMP-modellnavn er norske. En synlig hjelpeside forklarer domenebegrepene og grensene mot eierskap.
- La til `select_related` på adminlister som ellers henter relaterte innspillinger/parter rad for rad. Ingen caching eller spekulativ ytelsesinfrastruktur ble innført.

## Dependencies

Python 3.14 og Django 5.2.17 er prosjektets stabile runtime-grunnlag. Python 3.15 testes separat som en pre-release kompatibilitetsjobb og er ikke produksjonskrav.

Oppgradert og testet: Django REST Framework 3.18.1, Pillow 12.3.0, psycopg2-binary 2.9.13, WhiteNoise 6.12.0 og den kompatible AWS-gruppen boto3/botocore 1.43.93 + s3transfer 0.19.0. Beholdt: django-storages 1.14.6, dj-database-url 3.1.2, python-dotenv 1.2.3 og Waitress 3.0.2 fordi de allerede er siste relevante stabile versjoner. `psycopg2-binary` er praktisk for utvikling/testing; produksjonsdriver vurderes ved deploy.

Black 26.5.1 er prosjektets pinnede formatter. Dette er samme versjon som den siste dokumenterte grønne upstream-kjøringen 28.07.2026, og CI og lokal utvikling bruker samme versjon. Bare de historiske upstream-migrasjonene under `music_publisher/migrations` er unntatt; nye P7-migrasjoner kontrolleres som annen ny kode. En eventuell full reformatering av eldre eller senere tilkommet kode skal gjøres separat fra funksjons- og runtime-endringer.

Mot `backup/master-pre-p7-core-4.5a-20260915` (`45e41c4`) består den nåværende formatteringsgjelden av 88 filer opprettet i P7-utviklingen og én eksisterende fil endret av P7-utviklingen (`music_publisher/royalty_calculation.py`). Ingen av de 89 filene er innholdsmessig uendret fra siste grønne upstream-baseline `4fdec3d`. Gjelden normaliseres i én separat, mekanisk formatteringscommit etter runtime- og PostgreSQL-verifikasjonen; Black forblir en ordinær CI-kvalitetsport.

## Migrations og verifikasjon

- Nye fremovermigrasjoner lokaliserer modellmetadata og fryser DMPs tidligere uregistrerte choice-endringer. Ingen gammel migration er omskrevet.
- Oppgradering fra fase-1-migrasjonene og installasjon fra helt tom database er kontrollert på begge backends.
- SQLite: tom installasjon, fase-1-oppgradering med bevart Unicode-data, `check`, 105 tester og HTTP/admin-smoke bestod.
- PostgreSQL 17.11: tom installasjon, fase-1-oppgradering med bevart Unicode-data, `check`, 105 tester og HTTP/admin-smoke bestod. Isolert DMP-suite: 80 tester bestod.

## Begrensninger og anbefaling før fase 2

SQLite-adapteren bruker Djangos private `_remake_table` for én historisk DMP-indeks og må testes ved Django-oppgradering. DMP inneholder fortsatt eldre, spesialiserte engelske CWR-felt og eksporttekster; kodene og filformatene skal ikke oversettes. Produksjonsoppsett, roller/tilganger, sikkerhetskopi og overvåking gjenstår.

Fundamentet blokkerer ikke Release/Track, arkivmedlemskap, dublettkandidater, provenance, FileAsset/FileLocation, programarkiv eller teknologi-uavhengig JSON/JSONL/CSV-eksport. De stabile UUID-ene og eksplisitte relasjonene kan eksporteres uten database-ID-avhengighet. Fase 2 bør starte additivt med provenance/revisjon og en liten Release/Track-grense; ingen av de utsatte funksjonene er implementert her.
