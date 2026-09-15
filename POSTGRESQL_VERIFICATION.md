# PostgreSQL verification

P7 Core through phase 4.5A is tested with PostgreSQL 17.11, matching
`compose.yaml` and the GitHub Actions service container.

The authoritative CI matrix uses Python 3.14 as P7's only stable runtime for
the full SQLite and PostgreSQL suites. Python 3.15.0rc2 is an experimental compatibility job that
runs migrations and the targeted media, mastering, PostgreSQL-concurrency, and
FLAC-ingest suites. It is not the sole production runtime. The declared audio
runtime dependencies are NumPy 2.5.3 and SoundFile 0.13.1; psycopg2-binary
2.9.13 provides the PostgreSQL driver. All three install and run on the 3.15 RC
Linux runner without additional system packages.

Run locally:

```powershell
docker compose up -d db
$env:DATABASE_URL = "postgresql://rights:local-development-only@127.0.0.1:5433/rights"
python manage.py migrate media_assets 0003 --noinput
python manage.py migrate --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

`media_assets.test_postgresql` contains PostgreSQL-only transaction tests for
the partial unique current-radio index, concurrent activation, concurrent
master selection, concurrent deterministic generation, and activation
rollback. These tests are explicitly skipped on SQLite because SQLite cannot
prove PostgreSQL row-locking semantics.

Verified in GitHub Actions on 15 September 2026:

- Python 3.14 / PostgreSQL 17.11: 354 tests passed.
- Python 3.14 / SQLite: 348 tests passed, one PostgreSQL-only test class skipped.
- Python 3.15.0rc2 / PostgreSQL 17.11: 95 targeted compatibility tests passed.
- Fresh migrations, the pre-4.5A `media_assets.0003` upgrade path,
  `manage.py check`, migration-drift checks, and the operational smoke test
  passed in every matrix job.

The PostgreSQL run verifies the partial current-radio unique constraint with a
direct violating update, real separate-connection activation and generation
races, master-selection serialization, activation rollback, lineage
constraints, rebuild behavior, and playback regressions. PostgreSQL exposed
nullable outer-join `FOR UPDATE` queries; these now lock only their base rows.
Concurrency-test connections are explicitly closed before test-database
teardown.

The ordinary test command remains valid with SQLite when `DATABASE_URL` is
unset. Actual P7 NAS mounts, Windows client paths, and production file
permissions are separate pilot checks.
