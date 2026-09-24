# PostgreSQL verification

The optional local Compose service and GitHub Actions matrices target
PostgreSQL 18.6. Django 5.2 supports PostgreSQL 14 and newer, which includes
18.6. The first successful 18.6 CI run will establish verification for this
version.

The most recent fully green full-suite CI run before this version change used
PostgreSQL 17.11 on 24 September 2026: 772 tests passed on Python 3.14, and the
fresh-database migrations, system checks, migration-drift check and HTTP/admin
smoke test passed. PostgreSQL 18.6 has not yet been verified by CI.

Changing a database server from PostgreSQL 17 to 18 is a major-version
upgrade. The local Compose volume is persistent: do not start a PostgreSQL 17
volume with the 18.6 image or delete it to make Compose start. Back it up and
use a supported major-version upgrade or a dump/restore first. GitHub Actions
starts with a fresh database and does not upgrade a user volume.

The authoritative CI matrix uses Python 3.14 as P7's only stable runtime for
the full SQLite and PostgreSQL suites. Python 3.15.0rc2 is an experimental
compatibility job that runs the same full PostgreSQL suite. It is not the sole
production runtime. The declared audio runtime dependencies are NumPy 2.5.3
and SoundFile 0.14.0; psycopg2-binary 2.9.13 provides the PostgreSQL driver.
All three install and run on the 3.14 and 3.15 RC Linux runners without
additional system packages. SoundFile uses the wheel's bundled libsndfile.

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

Historical PostgreSQL 17.11 verification from 15 September 2026:

- Python 3.14 / PostgreSQL 17.11: 354 tests passed.
- Python 3.14 / SQLite: 348 tests passed, one PostgreSQL-only test class skipped.
- Python 3.15.0rc2 / PostgreSQL 17.11: 354 tests passed.
- Fresh migrations, the pre-4.5A `media_assets.0003` upgrade path,
  dependency integrity, `manage.py check`, migration-drift checks, and the
  operational smoke test passed in every matrix job.

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
