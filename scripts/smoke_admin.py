"""Exercise the real development server and CSRF-protected admin on a fresh DB.

Run: python scripts/smoke_admin.py
Creates a temporary SQLite database and administrator, never your dev records.
"""

import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    with tempfile.TemporaryDirectory(prefix="rights-smoke-") as directory:
        folder = Path(directory)
        password = secrets.token_urlsafe(24)
        env = dict(
            os.environ,
            DEBUG="true",
            SECURE_SSL_REDIRECT="false",
            DJANGO_SETTINGS_MODULE="rights_project.settings",
            SECRET_KEY=secrets.token_urlsafe(48),
            DATABASE_URL="sqlite:///" + (folder / "fresh.sqlite3").as_posix(),
            ALLOWED_HOSTS="127.0.0.1,localhost",
            DJANGO_SUPERUSER_USERNAME="smoke-admin",
            DJANGO_SUPERUSER_EMAIL="",
            DJANGO_SUPERUSER_PASSWORD=password,
        )
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        def manage(*args):
            result = subprocess.run(
                [sys.executable, "manage.py", *args],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                creationflags=flags,
            )
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            return result.stdout

        manage("migrate", "--noinput")
        manage("check")
        manage("createsuperuser", "--noinput")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        with (folder / "server.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen(
                [
                    sys.executable,
                    "manage.py",
                    "runserver",
                    f"127.0.0.1:{port}",
                    "--noreload",
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
                creationflags=flags,
            )
            try:
                jar = http.cookiejar.CookieJar()
                opener = urllib.request.build_opener(
                    urllib.request.HTTPCookieProcessor(jar)
                )

                def request(path, data=None):
                    payload = (
                        urllib.parse.urlencode(data).encode()
                        if data is not None
                        else None
                    )
                    req = urllib.request.Request(base + path, data=payload)
                    with opener.open(req, timeout=15) as response:
                        require(response.status == 200, f"HTTP {response.status}")
                        return response.read().decode("utf-8"), response.url

                for _ in range(60):
                    try:
                        html, _ = request("/")
                        break
                    except urllib.error.URLError as error:
                        if server.poll() is not None:
                            raise RuntimeError(
                                "Development server exited before becoming ready"
                            ) from error
                        time.sleep(0.25)
                else:
                    raise RuntimeError("Development server did not start")
                require("P7 Rights" in html, "Landing page is missing its title")
                html, _ = request("/admin/login/")

                def csrf(html):
                    return re.search(
                        r'name="csrfmiddlewaretoken" value="([^"]+)"', html
                    ).group(1)

                html, url = request(
                    "/admin/login/",
                    {
                        "csrfmiddlewaretoken": csrf(html),
                        "username": "smoke-admin",
                        "password": password,
                        "next": "/admin/",
                    },
                )
                require(
                    url.endswith("/admin/") and "Catalogue" in html,
                    "Admin login failed",
                )
                html, _ = request("/admin/catalogue/recording/add/")
                require(
                    'name="work"' not in html,
                    "Canonical recording form unexpectedly contains a work field",
                )
                data = {
                    "csrfmiddlewaretoken": csrf(html),
                    "title": "Example recording",
                    "metadata_status": "draft",
                    "_save": "Save",
                }
                for prefix in ("contributions", "identifiers"):
                    data.update(
                        {
                            f"{prefix}-TOTAL_FORMS": "0",
                            f"{prefix}-INITIAL_FORMS": "0",
                            f"{prefix}-MIN_NUM_FORMS": "0",
                            f"{prefix}-MAX_NUM_FORMS": "1000",
                        }
                    )
                html, url = request("/admin/catalogue/recording/add/", data)
                require(url.endswith("/admin/catalogue/recording/"), html)
                match = re.search(
                    r"/admin/catalogue/recording/([0-9a-f-]{36})/change/", html
                )
                require(match, "Saved recording missing from list")
                recording_id = match.group(1)
                change_url = f"/admin/catalogue/recording/{recording_id}/change/"
                html, _ = request(change_url)
                require(
                    "Example recording" in html and recording_id in html,
                    "Saved recording could not be reopened",
                )
                data.update(
                    {
                        "csrfmiddlewaretoken": csrf(html),
                        "title": "Blåbær / 東京 — edited",
                        "identifiers-TOTAL_FORMS": "1",
                        "identifiers-0-scheme": "ISRC",
                        "identifiers-0-value": "no-abc-26-00001",
                    }
                )
                html, url = request(change_url, data)
                require(url.endswith("/admin/catalogue/recording/"), html)
                html, _ = request(change_url)
                require(
                    "NOABC2600001" in html and "Blåbær / 東京 — edited" in html,
                    "Edited recording metadata was not persisted",
                )
                require(recording_id in html, "Recording UUID changed after edit")
                html, _ = request("/admin/music_publisher/work/")
                require(
                    "Musical work" in html or "Musical Work" in html,
                    "DMP work administration is unavailable",
                )
                manage(
                    "shell",
                    "-c",
                    "from music_publisher.models import Work, Writer, Recording, CWRExport; "
                    "models = (Work, Writer, Recording, CWRExport); "
                    "raise SystemExit(0 if all(m.objects.count() == 0 for m in models) else 1)",
                )
                print(
                    json.dumps(
                        {
                            "result": "PASS",
                            "server": base,
                            "database": "fresh temporary SQLite",
                            "checks": [
                                "migrate",
                                "system check",
                                "landing HTTP 200",
                                "CSRF login",
                                "admin",
                                "title-only save",
                                "reopen",
                                "edit Unicode title",
                                "add ISRC with same UUID",
                                "DMP admin HTTP 200",
                                "zero DMP records",
                            ],
                            "recording_uuid": recording_id,
                        },
                        indent=2,
                    )
                )
            finally:
                if os.name == "nt":
                    # The Windows venv launcher owns a child Python process.
                    subprocess.run(
                        ["taskkill", "/PID", str(server.pid), "/T", "/F"],
                        capture_output=True,
                        creationflags=flags,
                    )
                if server.poll() is None:
                    server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=10)


if __name__ == "__main__":
    main()
