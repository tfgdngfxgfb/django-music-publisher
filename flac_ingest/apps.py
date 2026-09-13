from django.apps import AppConfig


class FlacIngestConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "flac_ingest"
    verbose_name = "FLAC-innlesing"

    def ready(self):
        from . import signals  # noqa: F401
