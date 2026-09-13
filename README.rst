P7 Archive & Rights / P7 Arkiv og rettigheter
+++++++++++++++++++++++++++++++++++++++++++++++

Running P7 Arkiv og rettigheter locally
========================================

Quick start on Windows
----------------------

Double-click ``run-p7.cmd`` in the project folder, or run this in PowerShell::

   .\run-p7.cmd

The script creates the virtual environment and ``.env`` file when needed,
installs or updates dependencies, migrates the database, prepares a local
administrator and starts the application. On the first run it prints a strong,
random password once. Existing administrator passwords are never reset unless
you explicitly pass ``-AdminPassword``.

The account is only intended for local development. Stop the application with
``Ctrl+C``. The longer manual setup remains available below.

This fork now contains a runnable master catalogue alongside Django Music
Publisher. The master catalogue accepts a recording with only a title; no
musical work, artist, ISRC or rights owner is required. DMP remains available
for publishing Works, Writers and CWR.

Use **Python 3.13** (tested on Windows). No Node.js or database installation is
needed for the default SQLite development setup. Until this feature is merged,
check out its branch explicitly:

.. code-block:: powershell

   git clone --branch feature/catalogue-workbench https://github.com/tfgdngfxgfb/django-music-publisher.git
   cd django-music-publisher
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   Copy-Item .env.example .env
   .\.venv\Scripts\python.exe manage.py migrate
   .\.venv\Scripts\python.exe manage.py createsuperuser
   .\.venv\Scripts\python.exe manage.py runserver

Using the virtual environment's Python directly avoids PowerShell activation
policy problems. If you prefer activation, run ``.\.venv\Scripts\Activate.ps1``
and then use ordinary ``python`` commands. The optional ``.env`` loads
automatically; existing environment variables take precedence. Do not overwrite
an existing local ``.env`` when updating your checkout.

Open http://127.0.0.1:8000/ . If necessary, the application asks you to log in
and then returns you to the Norwegian catalogue workbench. Daily work uses the
shared navigation for **Musikkarkiv**, **Forvaltet musikk**, **Utgivelser**,
**Artister/personer**, **Kontroll**, **Filer** and **Hjelp**. The interface has
light and dark themes and uses the same permissions and domain services as
Django administration. Choose
**Katalog → Innspillinger → Legg til innspilling**, enter a title, leave the
other fields as they are, and click **Lagre**. Click the saved title to reopen
and edit it. Administration is also available directly at
http://127.0.0.1:8000/admin/ .

To add an ISRC later, reopen the recording, choose **Legg til ny ekstern
identifikator**, keep type **ISRC**, enter the code and save. The recording UUID
does not change. Add people or organizations through **Personer og
organisasjoner**, then add credits through **Medvirkende** on the recording.
Credits never establish rights.

Publishing is available through the administration navigation or directly at
http://127.0.0.1:8000/admin/music_publisher/ . Its separate recording models are
still publishing records; use **Katalog → Innspillinger** for canonical masters.
DMP's existing auxiliary endpoints now live under ``/publishing/``.

Stop the server with Ctrl+C. Restart with the final command above. Development
data is stored in the ignored ``db.sqlite3`` file. No default login is shipped.

Optional PostgreSQL development
-------------------------------

PostgreSQL is the intended deployment database. If Docker Desktop is available,
the supplied Compose file starts a local PostgreSQL 17 instance automatically:

.. code-block:: powershell

   docker compose up -d --wait db
   $env:DATABASE_URL = 'postgresql://rights:local-development-only@127.0.0.1:5433/rights'
   .\.venv\Scripts\python.exe manage.py migrate
   .\.venv\Scripts\python.exe manage.py createsuperuser
   .\.venv\Scripts\python.exe manage.py runserver

Alternatively uncomment ``DATABASE_URL`` in ``.env``. This is a separate
database: switching backends does not transfer records. Remove the environment
variable with ``Remove-Item Env:DATABASE_URL`` and comment it out in ``.env``
to return to SQLite. Compose credentials are local development values only.
``docker compose stop`` preserves the database volume.

Verification
------------

.. code-block:: powershell

   .\.venv\Scripts\python.exe -m pip check
   .\.venv\Scripts\python.exe manage.py check
   .\.venv\Scripts\python.exe manage.py makemigrations rights_core parties catalogue --check --dry-run
   .\.venv\Scripts\python.exe manage.py test
   .\.venv\Scripts\python.exe scripts/smoke_admin.py

The test suite uses the selected database backend. The HTTP smoke test always
creates its own temporary SQLite database and administrator, starts a real
server on a free loopback port, exercises login/save/reopen/edit, then removes
its test data. It does not touch your development catalogue.

``requirements-dev.txt`` locks the tested checkout runtime. Use it rather than
``pip install .``: upstream ``setup.py`` still describes the older standalone
DMP package and is not the combined application installation path. The root
``manage.py`` now selects ``rights_project.settings``. Existing deployments
must review this host change before switching; explicitly selecting
``--settings=dmp_project.settings`` retains the old host configuration but does
not include the SQLite compatibility adapter.

See `FOUNDATION_STABILIZATION.md <FOUNDATION_STABILIZATION.md>`_ for the phase
1.5 handover. `IMPLEMENTATION_STATUS.md <IMPLEMENTATION_STATUS.md>`_ records
phase 1. `PHASE2_IMPLEMENTATION.md <PHASE2_IMPLEMENTATION.md>`_ describes the
operative catalogue. The approved long-term baseline is
`ARCHITECTURE.md <ARCHITECTURE.md>`_; the complete rights and agreement model
remains a later phase.

`PHASE2_5_IMPLEMENTATION.md <PHASE2_5_IMPLEMENTATION.md>`_ documents the
catalogue workbench, source-value audit and database backup/restore procedure.

Operational catalogue workflow
------------------------------

After login, create or reuse people and artist identities, then create a label
and release. Open the release and choose **Registrer spor**. Each row can reuse
an existing recording or create a new recording and track atomically. Potential
matches must be reviewed and are never merged automatically.

Use **Musikkarkiv** for radio metadata. Use **Forvaltet musikk** only for an
explicit administrator decision; this workflow guarantees the recording is
also present in Musikkarkiv. **Kilder og verifikasjon** retains conflicting
source values and review decisions. **Filer** stores portable file identities
separately from current and historical locations.

Original Django Music Publisher documentation
=============================================

.. image:: https://img.shields.io/github/actions/workflow/status/matijakolaric-com/django-music-publisher/build.yml
    :target: https://github.com/matijakolaric-com/django-music-publisher/actions/workflows/build.yml
    :alt: Build Status
.. image:: https://img.shields.io/github/issues/matijakolaric-com/django-music-publisher/bug?logo=github&logoColor=white
    :target: https://github.com/matijakolaric-com/django-music-publisher/issues
    :alt: GitHub issues
.. image:: https://img.shields.io/readthedocs/django-music-publisher?logo=read-the-docs&logoColor=white   
    :target: https://django-music-publisher.readthedocs.io/en/latest/
    :alt: Documentation Status
.. image:: https://img.shields.io/coveralls/github/matijakolaric-com/django-music-publisher/master?logo=coveralls&branch=master&logoColor=white
    :target: https://coveralls.io/github/matijakolaric-com/django-music-publisher?branch=master
    :alt: Coverage Status
.. image:: https://img.shields.io/badge/code%20style-black-000000.svg
    :target: https://github.com/psf/black
    :alt: Code Style
.. image:: https://img.shields.io/pypi/v/django-music-publisher.svg?logo=pypi&logoColor=white
    :target: https://pypi.org/project/django-music-publisher/
    :alt: PYPI
.. image:: https://img.shields.io/pypi/wheel/django-music-publisher?logo=pypi&logoColor=white
    :target: https://pypi.org/project/django-music-publisher/
    :alt: PyPI - Wheel
.. image:: https://img.shields.io/pypi/status/django-music-publisher?logo=pypi&logoColor=white
    :target: https://pypi.org/project/django-music-publisher/
    :alt: PyPI - Status
.. image:: https://img.shields.io/pypi/djversions/django-music-publisher?logo=pypi&logoColor=white
    :target: https://pypi.org/project/django-music-publisher/
    :alt: PyPI - Django Version
.. image:: https://img.shields.io/pypi/pyversions/django-music-publisher?logo=pypi&logoColor=white
    :target: https://pypi.org/project/django-music-publisher/
    :alt: PyPI - Python Version
.. image:: https://img.shields.io/github/license/matijakolaric-com/django-music-publisher.svg?logo=github&logoColor=white
    :target: https://github.com/matijakolaric-com/django-music-publisher/blob/master/LICENSE
    :alt: License

Django-Music-Publisher (DMP) is open source software for **managing music metadata**, **registration/licencing of musical works**, **royalty management** and **music data distribution**.

.. image:: docs/images/work.png

* Docs: https://django-music-publisher.readthedocs.io/
* Code: https://github.com/matijakolaric-com/django-music-publisher/
* PYPI: https://pypi.org/project/django-music-publisher/
