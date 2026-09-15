# PostgreSQL verification

P7 Core through phase 4.5A is tested with PostgreSQL 17.11, matching
`compose.yaml` and the GitHub Actions service container.

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

The ordinary test command remains valid with SQLite when `DATABASE_URL` is
unset. Actual P7 NAS mounts, Windows client paths, and production file
permissions are separate pilot checks.
