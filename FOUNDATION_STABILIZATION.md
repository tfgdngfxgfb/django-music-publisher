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

Python 3.13.12 og Django 5.2.17 beholdes. Django 5.2 er siste patch i LTS-serien; Django 6.1 er nyere, men gir ingen nødvendig gevinst før fase 2. Python 3.14 var ikke installert lokalt, mens Django 5.2 støtter en senere separat CI-test på 3.14.

Oppgradert og testet: Django REST Framework 3.18.1, Pillow 12.3.0, psycopg2-binary 2.9.13, WhiteNoise 6.12.0 og den kompatible AWS-gruppen boto3/botocore 1.43.93 + s3transfer 0.19.0. Beholdt: django-storages 1.14.6, dj-database-url 3.1.2, python-dotenv 1.2.3 og Waitress 3.0.2 fordi de allerede er siste relevante stabile versjoner. `psycopg2-binary` er praktisk for utvikling/testing; produksjonsdriver vurderes ved deploy.

## Migrations og verifikasjon

- Nye fremovermigrasjoner lokaliserer modellmetadata og fryser DMPs tidligere uregistrerte choice-endringer. Ingen gammel migration er omskrevet.
- Oppgradering fra fase-1-migrasjonene og installasjon fra helt tom database er kontrollert på begge backends.
- SQLite: tom installasjon, fase-1-oppgradering med bevart Unicode-data, `check`, 105 tester og HTTP/admin-smoke bestod.
- PostgreSQL 17.11: tom installasjon, fase-1-oppgradering med bevart Unicode-data, `check`, 105 tester og HTTP/admin-smoke bestod. Isolert DMP-suite: 80 tester bestod.

## Begrensninger og anbefaling før fase 2

SQLite-adapteren bruker Djangos private `_remake_table` for én historisk DMP-indeks og må testes ved Django-oppgradering. DMP inneholder fortsatt eldre, spesialiserte engelske CWR-felt og eksporttekster; kodene og filformatene skal ikke oversettes. Produksjonsoppsett, roller/tilganger, sikkerhetskopi og overvåking gjenstår.

Fundamentet blokkerer ikke Release/Track, arkivmedlemskap, dublettkandidater, provenance, FileAsset/FileLocation, programarkiv eller teknologi-uavhengig JSON/JSONL/CSV-eksport. De stabile UUID-ene og eksplisitte relasjonene kan eksporteres uten database-ID-avhengighet. Fase 2 bør starte additivt med provenance/revisjon og en liten Release/Track-grense; ingen av de utsatte funksjonene er implementert her.
