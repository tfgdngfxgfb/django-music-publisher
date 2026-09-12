# P7 Rights — first runnable catalogue

Status: implemented and locally verified on Windows, 12 September 2026.

## Implemented

- Root `manage.py` selects `rights_project`: landing page, `/admin/`, DMP admin and `/publishing/` auxiliary routes.
- `rights_core`: UUID identity, timestamps, validation on normal model saves, revision counters and shared admin read-only fields.
- `parties`: Person/Organization/Group classification and separate ArtistIdentity with a Party relationship. No DMP reconciliation.
- `catalogue`: independent Recording, RecordingContribution and scheme-aware ExternalIdentifier (ISRC only in this phase).
- Title-only master creation, Unicode titles/names, optional version/kind/duration/language and draft metadata status.
- Inline contributions and ISRC entry, admin search/filtering, visible UUIDs, later editing without changing identity.
- Original entered ISRC plus normalized value, validation, unique global assignment and one current ISRC per recording.
- `.env.example`, pinned checkout dependencies, optional PostgreSQL Compose service and two-backend CI.
- Reproducible HTTP smoke script with an isolated database, temporary admin and real CSRF-protected login/form submissions.

## Decisions within this phase

The approved assessment is preserved as `ARCHITECTURE.md`. No rights domain was redesigned. The implementation follows the user's narrower runnable-foundation scope rather than implementing the assessment's larger first milestone.

UUIDs are concrete primary keys, supplied by an abstract canonical base. The future universal Entity registry is deferred until cross-domain provenance/identifiers need it; current identifiers use a real Recording FK. No GenericForeignKey, free-text target type or integer canonical ID is used. This keeps the first schema small; a later registry can be backfilled using the same UUIDs. Person/organization/group extension tables are likewise deferred until subtype-specific fields are needed.

Only ISRC is currently accepted by the explicit scheme vocabulary. Scheme/namespace/value columns and typed references leave room for later identifiers without overloading the Recording UUID. A later phase must deliberately migrate subject types, per-scheme cardinality rules and validation when supporting other schemes. No disputed identifier history, import matching or provenance workflow is claimed here.

Revision is a per-row save counter, not an immutable audit log or stale-form conflict detector. Saving an inline changes that relationship's revision; it does not by itself version the whole Recording aggregate. Normal instance saves validate UUID immutability, metadata and relationships. Bulk model writes are deliberately rejected until an import service can preserve validation/revision behavior. Authorized direct SQL remains an administrative operation, not a supported application write interface. Database checks/uniqueness cover vocabulary, ISRC format and assignment; cross-table artist/party consistency is validated in the application, not a database trigger.

Names and titles retain Unicode. Language validation checks a practical language-tag syntax, not the complete IANA registry. Display artist identity is separate from the Party object; neither implies legal title. No canonical ownership tables or placeholder works/owners/identifiers are created.

## Runtime and database support

Tested: Windows, Python 3.13, Django 5.2.17; exact dependencies in `requirements-dev.txt`. SQLite is zero-configuration development. PostgreSQL 17.11 is the authoritative deployment-oriented test backend. The same model migrations run on both. PostgreSQL validation used official portable binaries in an ignored local directory, bound to `127.0.0.1:55432`, without a system service; normal developers need only SQLite or the optional Compose file.

The checkout installation path intentionally bypasses old `setup.py` package requirements. `requirements.txt` now also includes python-dotenv so the inherited CI installation path can load the new host. No package release or production deployment was performed. Django 5.2's `STORAGES` setting is configured by the combined host; server secrets, host names, database and media paths come from environment variables. Local defaults listen on loopback through `runserver`.

## Migrations

- `parties/0001_initial`: Party and ArtistIdentity, UUIDs, references and checks.
- `catalogue/0001_initial`: Recording, RecordingContribution, ExternalIdentifier, UUIDs, checks and unique constraints.
- `rights_core` is abstract infrastructure and needs no tables/migrations yet.
- No file in `music_publisher/migrations/` changed. No migrations were faked, renamed, replaced or rewritten.
- Fresh SQLite and PostgreSQL databases migrated successfully with both DMP and canonical tables present. `makemigrations rights_core parties catalogue --check --dry-run` reports no changes in the new apps.
- An unscoped `makemigrations --check --dry-run` reports pre-existing DMP choices drift: CWR version choices and four society-choice fields differ from the last upstream migration. The relevant DMP model/choice files are unchanged from the baseline. No extra DMP migration was generated; do not automatically generate/commit one as part of catalogue development. The new CI checks only the new apps. Resolve this upstream metadata drift in a separate publishing maintenance task.

## Baseline and compatibility fixes

1. **Unmodified DMP baseline on Django 5.2 / SQLite:** 80 tests discovered; database creation failed before execution. DMP 0009 rebuilds WorkAcknowledgement on SQLite, losing the old `index_together` index. Migration 0010 then cannot rename it. The combined host selects a narrow SQLite schema editor adapter preserving that specific legacy index across table rebuilds. PostgreSQL uses Django's standard backend. This adds no migration dependency and also leaves already-migrated databases' history intact. The adapter uses Django's internal `_remake_table` hook and must be reviewed on future Django upgrades.
2. **Baseline with that adapter, before catalogue models:** 80 tests ran; 79 passed and one failed on Windows. RoyaltyCalculationView attempted to unlink an open download. The only production code change inside `music_publisher/` defers removal until FileResponse closes its stream; a dedicated regression test checks payload and cleanup. No CWR/model behavior changed.
3. **Corrected baseline before catalogue models:** all 80 DMP tests passed. The isolated DMP suite also passed all 80 tests against PostgreSQL after implementation.
4. `dmp_project/tests.py` explicitly selects its original URL configuration for its assertion that old-host admin lives at `/`. The combined-host tests verify `/` and `/admin/` separately. No upstream music_publisher test was altered or skipped.
5. New integration fixtures use TransactionTestCase and sequence resets so their DMP isolation tests do not consume integer IDs assumed by upstream fixtures on PostgreSQL. An initial combined run exposed that fixture interaction; the corrected combined run passes without changing DMP's tests.

## Verification results

Commands below use `.venv\Scripts\python.exe` on Windows; no activation is required.

| Verification | Result |
| --- | --- |
| `python manage.py test music_publisher` before domain additions, with isolated compatibility fixes | 80 passed on SQLite |
| `python manage.py test music_publisher` after implementation, PostgreSQL | 80 passed |
| `python manage.py test` combined, SQLite | 99 passed |
| `python manage.py test` combined, PostgreSQL 17.11 | 99 passed |
| `python manage.py migrate --noinput` against empty SQLite and PostgreSQL | All migrations applied |
| `python manage.py check` | No issues |
| `python manage.py makemigrations rights_core parties catalogue --check --dry-run` | No changes detected in the new apps |
| Unscoped migration drift check | Existing DMP choice-field drift, documented above; not changed in this phase |
| `python scripts/smoke_admin.py` | PASS; real server, login, title-only save, reopen, Unicode edit, later ISRC, same UUID and zero DMP records |
| Interactive browser at `http://127.0.0.1:8000/` | Landing → admin login → Catalogue → Add recording → title-only Save → reopen → edit/save all succeeded |
| Clean checkout of commit `7824eeb`, brand-new virtual environment, pinned dependency install and copied `.env.example` | Install and `pip check` passed; empty database migrated; system check passed; real-server smoke passed; all 99 tests passed |
| Final committed identifier partial-update regression on PostgreSQL | All 18 new/compatibility tests passed |
| GitHub Actions: P7 Rights foundation on `7824eeb` | Both SQLite and PostgreSQL jobs passed on Linux |

Browser verification used UUID `c87581ba-6f02-4b33-8948-dd5af329b111`; the title edit retained that UUID and incremented revision from 1 to 2. Disposable HTTP verification independently exercised ISRC normalization and checked no Work, Writer, DMP Recording or CWRExport was created. The smoke script starts a server on a free loopback port, so its printed port changes per run. Windows child-process cleanup requires normal permission to stop the process tree; it was verified outside the agent's restrictive sandbox after sandboxed cleanup was denied.

The full suite comprises 80 original DMP app tests, one original host test and 18 new domain/admin/compatibility tests. Tests cover independent masters, all Party kinds, optional contributions, Unicode, stable UUIDs, ISRC format and uniqueness at database level, mismatched artist identity, canonical/DMP deletion isolation, admin form behavior, authentication and the final migrated legacy index.

The clean checkout was a detached worktree of the same repository, with no reused virtual environment or database. Commands executed there were `python -m venv .venv`, `.venv\Scripts\python -m pip install -r requirements-dev.txt`, `Copy-Item .env.example .env`, then `pip check`, `manage.py migrate --noinput`, `manage.py check`, `scripts/smoke_admin.py` and `manage.py test`. Browser verification data and the temporary superuser were removed after testing; create your own administrator with `manage.py createsuperuser`.

## Current limits and next phase

This is local development software, not a completed rights-management or production deployment. It contains no master ownership, fractional splits, agreements, territories, releases, DMP Work links, import system, neighbouring rights, royalties, distribution, AI or external integrations. Admin's native history and row revisions are not the future audit/provenance system. Canonical deletion is currently ordinary admin deletion; introduce retirement/history before storing authoritative rights facts.

P7 branding is host-only; canonical models contain no P7-specific business rules. Production settings still need a separate deployment review, authentication/access design, storage validation, backups and operational configuration. DMP publisher settings must be populated before using real registration workflows. There is no shipped default administrator.

Recommended Phase 2: provenance and append-only audit, reviewed identifier/import staging, and the first small release/catalogue model. Add optional DMP Work links only as an explicit later bridge milestone. Keep ownership/agreements as a separate evidence-based phase, without inferring ownership from performer or label metadata.

## Repository

Development branch: `feature/master-catalogue-foundation` in the existing fork. Expected remotes remain `origin=https://github.com/tfgdngfxgfb/django-music-publisher.git` and `upstream=https://github.com/matijakolaric-com/django-music-publisher.git`. No merge to master or push to upstream is part of this task.
