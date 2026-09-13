from django.db import models

from music_library.models import MusicLibraryEntry
from provenance.models import SourceSystem
from rights_core.models import CanonicalModel


class ManagedRecording(CanonicalModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Til vurdering"
        ACTIVE = "active", "Aktiv forvaltning"
        INACTIVE = "inactive", "Ikke aktiv"

    library_entry = models.OneToOneField(
        MusicLibraryEntry,
        verbose_name="musikkarkivpost",
        on_delete=models.PROTECT,
        related_name="managed_recording",
        help_text="Databasekoblingen sikrer at forvaltet musikk alltid finnes i Musikkarkivet.",
    )
    status = models.CharField(
        "forvaltningsstatus",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    source_system = models.ForeignKey(
        SourceSystem,
        verbose_name="kilde",
        on_delete=models.PROTECT,
        related_name="managed_recordings",
        null=True,
        blank=True,
    )
    notes = models.TextField("merknader", blank=True)

    class Meta:
        verbose_name = "forvaltet innspilling"
        verbose_name_plural = "forvaltet musikk"
        ordering = ("library_entry__recording__title", "id")
        indexes = [models.Index(fields=("status",), name="managed_status_idx")]

    @property
    def recording(self):
        return self.library_entry.recording

    def __str__(self):
        return self.library_entry.recording.title
