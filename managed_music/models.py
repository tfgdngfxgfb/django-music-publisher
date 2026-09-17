from django.db import models

from catalogue.models import Release
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


class ManagedRelease(CanonicalModel):
    """P7 catalogue management of a Release, independent of track rights."""

    class Status(models.TextChoices):
        PENDING = "pending", "Til vurdering"
        ACTIVE = "active", "Aktiv forvaltning"
        INACTIVE = "inactive", "Ikke aktiv"

    class Relationship(models.TextChoices):
        OWNED_CATALOGUE = "owned_catalogue", "Eid/kontrollert katalog"
        MANAGED_CATALOGUE = "managed_catalogue", "Forvaltet på vegne av andre"

    release = models.OneToOneField(
        Release,
        verbose_name="utgivelse",
        on_delete=models.PROTECT,
        related_name="managed_release",
    )
    status = models.CharField(
        "forvaltningsstatus",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    relationship = models.CharField(
        "katalogforhold",
        max_length=30,
        choices=Relationship.choices,
        help_text=(
            "Gjelder utgivelsen som katalogobjekt og sier ikke hvem som eier "
            "masterne på sporene."
        ),
    )
    source_system = models.ForeignKey(
        SourceSystem,
        verbose_name="kilde",
        on_delete=models.PROTECT,
        related_name="managed_releases",
        null=True,
        blank=True,
    )
    notes = models.TextField("merknader", blank=True)

    class Meta:
        verbose_name = "forvaltet utgivelse"
        verbose_name_plural = "forvaltede utgivelser"
        ordering = ("release__title", "id")
        indexes = [
            models.Index(
                fields=("status",), name="managed_release_status_idx"
            ),
            models.Index(
                fields=("relationship",), name="managed_release_rel_idx"
            ),
        ]

    def __str__(self):
        return self.release.title
