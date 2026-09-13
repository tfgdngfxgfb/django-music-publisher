from django.conf import settings
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models

from rights_core.models import CanonicalModel, VerificationStatus, validate_not_blank


class SourceSystem(CanonicalModel):
    class Kind(models.TextChoices):
        MANUAL = "manual", "Manuell registrering"
        PHYSICAL = "physical", "Fysisk kilde"
        IMPORT = "import", "Filimport"
        API = "api", "Eksternt system/API"
        OTHER = "other", "Annen"

    name = models.CharField("navn", max_length=255, validators=[validate_not_blank])
    kind = models.CharField("kildetype", max_length=20, choices=Kind.choices)
    description = models.TextField("beskrivelse", blank=True)

    class Meta:
        verbose_name = "kildesystem"
        verbose_name_plural = "kildesystemer"
        ordering = ("name", "id")
        constraints = [
            models.UniqueConstraint(fields=("name",), name="source_system_unique_name"),
            models.CheckConstraint(
                condition=models.Q(name__regex=r".*\S.*"),
                name="source_system_nonblank_name",
            ),
        ]

    def __str__(self):
        return self.name


class ImportBatch(CanonicalModel):
    source_system = models.ForeignKey(
        SourceSystem,
        verbose_name="kildesystem",
        on_delete=models.PROTECT,
        related_name="import_batches",
    )
    external_batch_id = models.CharField("ekstern batch-ID", max_length=255, blank=True)
    imported_at = models.DateTimeField("importert", auto_now_add=True)
    notes = models.TextField("merknader", blank=True)

    class Meta:
        verbose_name = "importbatch"
        verbose_name_plural = "importbatcher"
        ordering = ("-imported_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("source_system", "external_batch_id"),
                condition=~models.Q(external_batch_id=""),
                name="import_batch_unique_external_id",
            )
        ]

    def __str__(self):
        return self.external_batch_id or str(self.id)


class SourceRecord(CanonicalModel):
    source_system = models.ForeignKey(
        SourceSystem,
        verbose_name="kildesystem",
        on_delete=models.PROTECT,
        related_name="source_records",
    )
    import_batch = models.ForeignKey(
        ImportBatch,
        verbose_name="importbatch",
        on_delete=models.PROTECT,
        related_name="source_records",
        null=True,
        blank=True,
    )
    external_record_id = models.CharField("ekstern post-ID", max_length=255, blank=True)
    source_locator = models.CharField(
        "kildehenvisning",
        max_length=500,
        blank=True,
        help_text="For eksempel LP-cover eller filnavn.",
    )
    raw_payload = models.JSONField(
        "originale kildedata",
        default=dict,
        blank=True,
        help_text="Bevares uendret som kildespor.",
    )

    class Meta:
        verbose_name = "kildepost"
        verbose_name_plural = "kildeposter"
        ordering = ("source_system", "external_record_id", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("source_system", "external_record_id"),
                condition=~models.Q(external_record_id=""),
                name="source_record_unique_external_id",
            )
        ]

    def clean(self):
        super().clean()
        if (
            self.import_batch_id
            and self.import_batch.source_system_id != self.source_system_id
        ):
            raise ValidationError(
                {"import_batch": "Importbatch og kildepost må bruke samme kildesystem."}
            )
        if not self._state.adding:
            original = type(self).objects.get(pk=self.pk)
            immutable = (
                "source_system_id",
                "import_batch_id",
                "external_record_id",
                "source_locator",
                "raw_payload",
            )
            if any(
                getattr(original, field) != getattr(self, field) for field in immutable
            ):
                raise ValidationError(
                    "Originale kildedata kan ikke overskrives. Opprett en ny kildepost."
                )

    def __str__(self):
        return self.external_record_id or self.source_locator or str(self.id)


class MetadataAssertion(CanonicalModel):
    class EntityType(models.TextChoices):
        RECORDING = "recording", "Innspilling"
        RELEASE = "release", "Utgivelse"
        RELEASE_TRACK = "release_track", "Spor på utgivelse"
        PARTY = "party", "Person/organisasjon"
        ARTIST_IDENTITY = "artist_identity", "Artistidentitet"
        LABEL = "label", "Label"
        MUSIC_LIBRARY_ENTRY = "music_library_entry", "Musikkarkivpost"
        MANAGED_RECORDING = "managed_recording", "Forvaltet innspilling"
        FILE_ASSET = "file_asset", "Filressurs"

    source_record = models.ForeignKey(
        SourceRecord,
        verbose_name="kildepost",
        on_delete=models.PROTECT,
        related_name="assertions",
    )
    entity_type = models.CharField(
        "objekttype", max_length=40, choices=EntityType.choices
    )
    entity_uuid = models.UUIDField("objektets UUID", db_index=True)
    field_name = models.CharField(
        "feltnavn",
        max_length=100,
        help_text="Stabilt teknisk feltnavn, for eksempel release_year.",
    )
    raw_value = models.TextField("original kildeverdi")
    normalized_value = models.JSONField("normalisert verdi", null=True, blank=True)
    status = models.CharField(
        "status",
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.UNVERIFIED,
    )
    supersedes = models.ForeignKey(
        "self",
        verbose_name="erstatter påstand",
        on_delete=models.PROTECT,
        related_name="superseded_by",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "metadatapåstand"
        verbose_name_plural = "metadatapåstander"
        ordering = ("status", "entity_type", "entity_uuid", "field_name", "-created_at")
        indexes = [
            models.Index(
                fields=("entity_type", "entity_uuid", "field_name"),
                name="assertion_target_field_idx",
            ),
            models.Index(fields=("status",), name="assertion_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(field_name__regex=r"^[a-z][a-z0-9_]*$"),
                name="assertion_valid_field_name",
            )
        ]

    def clean(self):
        super().clean()
        targets = {
            self.EntityType.RECORDING: ("catalogue", "Recording"),
            self.EntityType.RELEASE: ("catalogue", "Release"),
            self.EntityType.RELEASE_TRACK: ("catalogue", "ReleaseTrack"),
            self.EntityType.PARTY: ("parties", "Party"),
            self.EntityType.ARTIST_IDENTITY: ("parties", "ArtistIdentity"),
            self.EntityType.LABEL: ("catalogue", "Label"),
            self.EntityType.MUSIC_LIBRARY_ENTRY: ("music_library", "MusicLibraryEntry"),
            self.EntityType.MANAGED_RECORDING: ("managed_music", "ManagedRecording"),
            self.EntityType.FILE_ASSET: ("media_assets", "FileAsset"),
        }
        target = targets.get(self.entity_type)
        target_model = apps.get_model(*target) if target else None
        if (
            target_model
            and self.entity_uuid
            and not target_model.objects.filter(pk=self.entity_uuid).exists()
        ):
            raise ValidationError(
                {"entity_uuid": "Objektet finnes ikke i den valgte basen."}
            )
        if self.supersedes_id:
            if self.supersedes_id == self.id:
                raise ValidationError(
                    {"supersedes": "En påstand kan ikke erstatte seg selv."}
                )
            previous = self.supersedes
            if (
                previous.entity_type != self.entity_type
                or previous.entity_uuid != self.entity_uuid
                or previous.field_name != self.field_name
            ):
                raise ValidationError(
                    {"supersedes": "Erstattet påstand må gjelde samme objekt og felt."}
                )
        if not self._state.adding:
            original = type(self).objects.get(pk=self.pk)
            immutable = (
                "source_record_id",
                "entity_type",
                "entity_uuid",
                "field_name",
                "raw_value",
                "supersedes_id",
            )
            if any(
                getattr(original, field) != getattr(self, field) for field in immutable
            ):
                raise ValidationError(
                    "Kildepåstanden kan ikke overskrives. Opprett en ny påstand som erstatter den gamle."
                )

    def __str__(self):
        return f"{self.get_entity_type_display()}.{self.field_name}: {self.raw_value}"


class AssertionDecision(CanonicalModel):
    class Decision(models.TextChoices):
        CONFIRMED = VerificationStatus.CONFIRMED, "Bekreftet"
        DISPUTED = VerificationStatus.DISPUTED, "Bestridt"
        REJECTED = VerificationStatus.REJECTED, "Avvist"
        SUPERSEDED = VerificationStatus.SUPERSEDED, "Erstattet"

    assertion = models.ForeignKey(
        MetadataAssertion,
        verbose_name="metadatapåstand",
        on_delete=models.PROTECT,
        related_name="decisions",
    )
    decision = models.CharField("avgjørelse", max_length=20, choices=Decision.choices)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="avgjort av",
        on_delete=models.PROTECT,
        related_name="metadata_decisions",
        null=True,
        blank=True,
    )
    note = models.TextField("begrunnelse", blank=True)

    class Meta:
        verbose_name = "metadataavgjørelse"
        verbose_name_plural = "metadataavgjørelser"
        ordering = ("-created_at", "id")

    def clean(self):
        super().clean()
        if not self._state.adding:
            raise ValidationError(
                "En metadataavgjørelse er historikk og kan ikke endres."
            )

    def __str__(self):
        return f"{self.assertion} — {self.get_decision_display()}"
