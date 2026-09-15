"""Combined development host. Environment overrides .env; SQLite by default."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")

# Keep DMP's publishing configuration and defaults in its upstream component.
from dmp_project.settings import *  # noqa: E402,F403


def env_bool(name, default=False):
    value = os.getenv(name, str(default)).lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ImproperlyConfigured(f"{name} must be true or false")
    return value in {"true", "1", "yes"}


DEBUG = env_bool("DEBUG", True)
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("SECRET_KEY is required when DEBUG=false")
    SECRET_KEY = "development-only-p7-rights-do-not-use-on-a-server"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost,[::1]").split(",")
INSTALLED_APPS = [  # noqa: F405
    *INSTALLED_APPS,  # noqa: F405
    "rights_core",
    "parties",
    "catalogue",
    "provenance",
    "music_library",
    "managed_music",
    "media_assets",
    "rights",
    "flac_ingest.apps.FlacIngestConfig",
    "workbench",
    "gui_v2.apps.GuiV2Config",
]
ROOT_URLCONF = "rights_project.urls"
WSGI_APPLICATION = "rights_project.wsgi.application"
LANGUAGE_CODE = "nb"
TEMPLATES[0]["DIRS"] = [PROJECT_DIR / "rights_project" / "templates"]  # noqa: F405
TEMPLATES[0].setdefault("OPTIONS", {}).setdefault("libraries", {})[  # noqa: F405
    "workbench_tags"
] = "workbench.templatetags.workbench_tags"
DATABASES = {
    "default": dj_database_url.config(  # noqa: F405
        default="sqlite:///" + (PROJECT_DIR / "db.sqlite3").as_posix(),
        conn_max_age=0,
    )
}
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    DATABASES["default"]["ENGINE"] = "rights_project.db.backends.sqlite3"
elif DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
    raise ImproperlyConfigured("Supported databases are SQLite and PostgreSQL")

SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", not DEBUG)
SECURE_HSTS_SECONDS = 0 if DEBUG else 300
LOGIN_URL = "/admin/login/"
OPTION_FILES = env_bool("OPTION_FILES", True)
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", PROJECT_DIR / "media"))
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
if S3_ENABLED:  # noqa: F405
    STORAGES["default"]["BACKEND"] = "storages.backends.s3.S3Storage"

# Physical roots belong to each installation. FileLocation stores only portable,
# logical relative paths.
P7_NAS_ROOT = os.getenv("P7_NAS_ROOT") or os.getenv("P7_MUSIC_ROOT", "")
P7_MUSIC_ROOT = os.getenv("P7_MUSIC_ROOT", P7_NAS_ROOT)
# Optional path presented to an internal Windows client. It may differ from the
# server mount used by Django and is never used for server-side file access.
P7_MUSIC_CLIENT_ROOT = os.getenv("P7_MUSIC_CLIENT_ROOT", "")

# Existing archive files are read-only by default. Explicit tag-writing tools must
# pass through this installation-level gate; ingest and maintenance never enable it.
P7_ALLOW_FILE_WRITES = env_bool("P7_ALLOW_FILE_WRITES", False)

# GUI v2 remains read-only unless an isolated test process explicitly enables
# catalogue writes. GUI v2 never exposes ingest apply or FLAC writeback routes.
GUI_V2_WRITES_ENABLED = env_bool("GUI_V2_WRITES_ENABLED", False)
