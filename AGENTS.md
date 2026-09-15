# Codex-regler for P7 Arkiv og rettigheter

## Testpolicy – PostgreSQL

PostgreSQL-testene er kostbare og skal ikke kjøres rutinemessig etter hver
endring eller commit.

Under vanlig implementasjon skal Codex bruke:

- målrettede tester for området som endres;
- SQLite når testen ikke er avhengig av PostgreSQL-semantikk;
- `manage.py check`;
- `manage.py makemigrations --check --dry-run`;
- Black/lint etter behov.

PostgreSQL-tester kan kjøres uten ny tillatelse bare når brukeren uttrykkelig
har bedt om dem i den aktuelle oppgaven. Dersom Codex mener at
PostgreSQL-verifikasjon er nødvendig før arbeidet kan fortsette, skal Codex
stoppe og be brukeren om tillatelse før testene startes. Forespørselen skal kort
angi hvilke PostgreSQL-tester som ønskes kjørt og hvorfor.

Full PostgreSQL-suite skal normalt reserveres til:

- avslutning av en større fase;
- endringer i constraints, transaksjoner, locking eller concurrency;
- migrasjonsendringer med PostgreSQL-spesifikk betydning;
- en eksplisitt brukerbestilling;
- endelig pre-merge-verifikasjon etter godkjenning.

Ikke kjør full PostgreSQL-suite bare for sikkerhets skyld. Begrens tokenbruken
og arbeid målrettet.

## Fase 4.5B

Under utviklingen av 4.5B skal Codex kjøre målrettede tester fortløpende. Hvis
nye modeller, constraints, `transaction.atomic()`, row locking eller
concurrency-regler trenger faktisk PostgreSQL-verifikasjon, skal Codex først be
om tillatelse. Full PostgreSQL-regresjon tas når fasen nærmer seg ferdigstilling
og brukeren har godkjent kjøringen.

Formuler testkrav for vanlig utvikling slik:

> Kjør målrettede lokale tester og SQLite-regresjon. Foreslå
> PostgreSQL-verifikasjonen som bør kjøres ved ferdigstilling, og be om
> tillatelse før den startes.
