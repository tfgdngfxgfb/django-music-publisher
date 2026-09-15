import re
from pathlib import PurePosixPath, PureWindowsPath

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models

from catalogue.models import Recording, Release, ReleaseTrack
from rights_core.models import CanonicalModel, validate_not_blank


def validate_logical_path(value):
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if (
        not value.strip()
        or path.is_absolute()
        or windows.is_absolute()
        or ".." in path.parts
    ):
        raise ValidationError(
            "Bruk en logisk relativ sti uten stasjonsbokstav eller «..»."
        )


class FileAsset(CanonicalModel):
    class Role(models.TextChoices):
        RAW_DIGITIZATION = "raw_digitization", "Rå digitalisering"
        EDITED_WAV_MASTER = "edited_wav_master", "Redigert WAV-master"
        RADIO_FLAC = "radio_flac", "Radio-FLAC"
        DISTRIBUTION = "distribution", "Distribusjonsfil"
        COVER_IMAGE = "cover_image", "Cover/bilde"
        DOCUMENT = "document", "Dokument"
        OTHER = "other", "Annen mediefil"

    class SyncStatus(models.TextChoices):
        NOT_APPLICABLE = "not_applicable", "Ikke aktuell"
        SYNCED = "synced", "Synkronisert"
        PENDING = "pending", "Venter på synkronisering"
        MISSING = "missing", "Fil ikke funnet"
        CONFLICT = "conflict", "Konflikt"
        FAILED = "failed", "Synkronisering feilet"

    class LifecycleStatus(models.TextChoices):
        UNCLASSIFIED = "unclassified", "Ikke klassifisert"
        CANDIDATE = "candidate", "Kandidat"
        CURRENT = "current", "Gjeldende"
        HISTORICAL = "historical", "Historisk"

    recording = models.ForeignKey(
        Recording,
        verbose_name="innspilling",
        on_delete=models.PROTECT,
        related_name="file_assets",
        null=True,
        blank=True,
    )
    release = models.ForeignKey(
        Release,
        verbose_name="utgivelse",
        on_delete=models.PROTECT,
        related_name="file_assets",
        null=True,
        blank=True,
    )
    release_track = models.ForeignKey(
        ReleaseTrack,
        verbose_name="spor på utgivelse",
        on_delete=models.PROTECT,
        related_name="file_assets",
        null=True,
        blank=True,
        help_text="Brukes bare når filens utgivelseskontekst er entydig.",
    )
    filename = models.CharField(
        "filnavn", max_length=500, validators=[validate_not_blank]
    )
    mime_type = models.CharField("MIME-type/format", max_length=255, blank=True)
    size_bytes = models.PositiveBigIntegerField(
        "størrelse i byte", null=True, blank=True
    )
    sha256 = models.CharField(
        "SHA-256",
        max_length=64,
        blank=True,
        help_text="64 heksadesimale tegn når kjent.",
    )
    role = models.CharField("filrolle", max_length=30, choices=Role.choices)
    lifecycle_status = models.CharField(
        "livssyklusstatus",
        max_length=20,
        choices=LifecycleStatus.choices,
        default=LifecycleStatus.UNCLASSIFIED,
    )
    technical_metadata = models.JSONField("tekniske metadata", default=dict, blank=True)
    metadata_read_at = models.DateTimeField(
        "filmetadata sist lest", null=True, blank=True
    )
    source_modified_at = models.DateTimeField("fil sist endret", null=True, blank=True)
    sync_status = models.CharField(
        "synkroniseringsstatus",
        max_length=20,
        choices=SyncStatus.choices,
        default=SyncStatus.NOT_APPLICABLE,
    )
    sync_requested_at = models.DateTimeField(
        "synkronisering bestilt", null=True, blank=True
    )
    synced_at = models.DateTimeField("synkronisert", null=True, blank=True)
    sync_error = models.TextField("synkroniseringsfeil", blank=True)

    class Meta:
        verbose_name = "filressurs"
        verbose_name_plural = "filressurser"
        ordering = ("filename", "id")
        indexes = [
            models.Index(fields=("role", "sync_status"), name="file_asset_sync_idx")
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(filename__regex=r".*\S.*"),
                name="file_asset_nonblank_name",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(release_track__isnull=True)
                    | models.Q(recording__isnull=False, release__isnull=True)
                ),
                name="file_track_requires_recording",
            ),
            models.CheckConstraint(
                condition=models.Q(recording__isnull=True)
                | models.Q(release__isnull=True),
                name="file_asset_no_recording_release_pair",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        lifecycle_status__in=(
                            "unclassified",
                            "historical",
                        )
                    )
                    | models.Q(
                        role="radio_flac",
                        recording__isnull=False,
                    )
                ),
                name="file_asset_active_lifecycle_is_radio",
            ),
            models.UniqueConstraint(
                fields=("recording",),
                condition=models.Q(lifecycle_status="current"),
                name="file_asset_one_current_radio",
            ),
        ]

    def clean_fields(self, exclude=None):
        if self.sha256:
            self.sha256 = self.sha256.strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
                raise ValidationError(
                    {"sha256": "SHA-256 må ha 64 heksadesimale tegn."}
                )
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        if self.release_track_id and (
            not self.recording_id
            or self.release_track.recording_id != self.recording_id
            or self.release_id
        ):
            raise ValidationError(
                {
                    "release_track": (
                        "Sporreferansen må tilhøre den valgte innspillingen, "
                        "og kan ikke kombineres med en egen utgivelsesreferanse."
                    )
                }
            )

    def __str__(self):
        return self.filename


class RecordingMediaSelection(CanonicalModel):
    """Protected choices for one Recording's master and operative radio file."""

    recording = models.OneToOneField(
        Recording,
        on_delete=models.PROTECT,
        related_name="media_selection",
        verbose_name="innspilling",
    )
    selected_master = models.ForeignKey(
        FileAsset,
        on_delete=models.PROTECT,
        related_name="selected_for_recordings",
        null=True,
        blank=True,
        verbose_name="valgt master",
    )
    current_radio = models.ForeignKey(
        FileAsset,
        on_delete=models.PROTECT,
        related_name="current_for_recordings",
        null=True,
        blank=True,
        verbose_name="gjeldende radiofil",
    )
    selected_master_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="selected_media_masters",
        null=True,
        blank=True,
    )
    current_radio_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="selected_current_radio_files",
        null=True,
        blank=True,
    )
    selected_master_at = models.DateTimeField(null=True, blank=True)
    current_radio_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "medievalg for innspilling"
        verbose_name_plural = "medievalg for innspillinger"

    def clean(self):
        super().clean()
        if self.selected_master_id:
            if (
                self.selected_master.recording_id != self.recording_id
                or self.selected_master.role != FileAsset.Role.EDITED_WAV_MASTER
            ):
                raise ValidationError(
                    {
                        "selected_master": "Valgt master må være en master for samme innspilling."
                    }
                )

        if self.current_radio_id:
            if (
                self.current_radio.recording_id != self.recording_id
                or self.current_radio.role != FileAsset.Role.RADIO_FLAC
                or self.current_radio.lifecycle_status
                != FileAsset.LifecycleStatus.CURRENT
            ):
                raise ValidationError(
                    {
                        "current_radio": (
                            "Gjeldende radiofil må være en aktivert radio-FLAC "
                            "for samme innspilling."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class FileDerivation(CanonicalModel):
    class RelationType(models.TextChoices):
        GENERATED_FROM = "generated_from", "Generert fra"

    source_asset = models.ForeignKey(
        FileAsset, on_delete=models.PROTECT, related_name="derived_files"
    )
    derived_asset = models.ForeignKey(
        FileAsset, on_delete=models.PROTECT, related_name="source_files"
    )
    relation_type = models.CharField(
        max_length=30,
        choices=RelationType.choices,
        default=RelationType.GENERATED_FROM,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="file_derivations",
        null=True,
        blank=True,
    )
    tool_name = models.CharField(max_length=100)
    tool_version = models.CharField(max_length=100, blank=True)
    parameters = models.JSONField(default=dict, blank=True)
    verification = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "filavledning"
        verbose_name_plural = "filavledninger"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(source_asset=models.F("derived_asset")),
                name="file_derivation_not_self",
            ),
            models.UniqueConstraint(
                fields=("source_asset", "derived_asset", "relation_type"),
                name="file_derivation_unique_relation",
            ),
        ]

    def clean(self):
        super().clean()
        if self.source_asset_id == self.derived_asset_id:
            raise ValidationError("En fil kan ikke være avledet fra seg selv.")
        if (
            self.source_asset.recording_id is None
            or self.source_asset.recording_id != self.derived_asset.recording_id
        ):
            raise ValidationError("Kilde og avledet fil må tilhøre samme innspilling.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class RadioFlacGeneration(CanonicalModel):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planlagt"
        GENERATING = "generating", "Generering pågår"
        VERIFIED = "verified", "Generert og verifisert"
        ACTIVATED = "activated", "Aktivert"
        FAILED = "failed", "Feilet"

    recording = models.ForeignKey(
        Recording,
        on_delete=models.PROTECT,
        related_name="radio_flac_generations",
    )
    master_asset = models.ForeignKey(
        FileAsset, on_delete=models.PROTECT, related_name="master_generations"
    )
    radio_metadata_source = models.ForeignKey(
        FileAsset,
        on_delete=models.PROTECT,
        related_name="metadata_source_generations",
        null=True,
        blank=True,
    )
    candidate_asset = models.OneToOneField(
        FileAsset,
        on_delete=models.PROTECT,
        related_name="generation",
        null=True,
        blank=True,
    )
    target_root_key = models.CharField(max_length=100)
    target_relative_path = models.CharField(
        max_length=1000, validators=[validate_logical_path]
    )
    technical_plan = models.JSONField(default=dict)
    expected_tags = models.JSONField(default=dict)
    metadata_diff = models.JSONField(default=list)
    verification = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices)
    failure_message = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="radio_flac_generations",
    )
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="activated_radio_flac_generations",
        null=True,
        blank=True,
    )
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "radio-FLAC-generering"
        verbose_name_plural = "radio-FLAC-genereringer"
        ordering = ("-created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("target_root_key", "target_relative_path"),
                name="radio_generation_unique_target",
            )
        ]

    def clean(self):
        super().clean()
        if (
            self.master_asset.recording_id != self.recording_id
            or self.master_asset.role != FileAsset.Role.EDITED_WAV_MASTER
        ):
            raise ValidationError("Genereringsmasteren må tilhøre innspillingen.")
        if self.radio_metadata_source_id and (
            self.radio_metadata_source.recording_id != self.recording_id
            or self.radio_metadata_source.role != FileAsset.Role.RADIO_FLAC
        ):
            raise ValidationError("Radiometadatakilden må tilhøre innspillingen.")
        if self.candidate_asset_id and (
            self.candidate_asset.recording_id != self.recording_id
            or self.candidate_asset.role != FileAsset.Role.RADIO_FLAC
        ):
            raise ValidationError("Kandidaten må være en radio-FLAC for innspillingen.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class MediaAssetEvent(CanonicalModel):
    class EventType(models.TextChoices):
        MASTER_REGISTERED = "master_registered", "Master registrert"
        MASTER_SELECTED = "master_selected", "Master valgt"
        CANDIDATE_GENERATED = "candidate_generated", "Kandidat generert"
        AUDIO_VERIFIED = "audio_verified", "Lyd verifisert"
        METADATA_VERIFIED = "metadata_verified", "Metadata verifisert"
        RADIO_ACTIVATED = "radio_activated", "Radiofil aktivert"
        RADIO_SUPERSEDED = "radio_superseded", "Radiofil avløst"

    recording = models.ForeignKey(
        Recording, on_delete=models.PROTECT, related_name="media_events"
    )
    asset = models.ForeignKey(
        FileAsset,
        on_delete=models.PROTECT,
        related_name="media_events",
        null=True,
        blank=True,
    )
    related_asset = models.ForeignKey(
        FileAsset,
        on_delete=models.PROTECT,
        related_name="related_media_events",
        null=True,
        blank=True,
    )
    event_type = models.CharField(max_length=30, choices=EventType.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="media_asset_events",
    )
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "mediefilhendelse"
        verbose_name_plural = "mediefilhendelser"
        ordering = ("-created_at", "id")

    def clean(self):
        super().clean()
        for field in ("asset", "related_asset"):
            value = getattr(self, field)
            if value and value.recording_id != self.recording_id:
                raise ValidationError({field: "Filressursen må tilhøre innspillingen."})
        if not self._state.adding:
            raise ValidationError("Mediefilhendelser er historikk og kan ikke endres.")


class FileLocation(CanonicalModel):
    class StorageType(models.TextChoices):
        NAS = "nas", "NAS"
        GOOGLE_DRIVE = "google_drive", "Google Drive"
        LOCAL = "local", "Lokal lagring"
        CLOUD = "cloud", "Annen skylagring"
        OTHER = "other", "Annen"

    class Status(models.TextChoices):
        ACTIVE = "active", "Aktiv"
        MOVED = "moved", "Flyttet"
        MISSING = "missing", "Mangler"
        HISTORICAL = "historical", "Historisk"

    class VerificationStatus(models.TextChoices):
        UNCHECKED = "unchecked", "Ikke kontrollert"
        VERIFIED = "verified", "Kontrollert plassering"

    asset = models.ForeignKey(
        FileAsset,
        verbose_name="filressurs",
        on_delete=models.PROTECT,
        related_name="locations",
    )
    storage_type = models.CharField(
        "lagringstype", max_length=30, choices=StorageType.choices
    )
    relative_path = models.CharField(
        "logisk relativ sti",
        max_length=1000,
        validators=[validate_logical_path],
    )
    storage_root_key = models.CharField(
        "storage-root",
        max_length=100,
        blank=True,
        help_text="Tom verdi bruker kompatibilitetsmapping fra lagringstype.",
    )
    status = models.CharField(
        "status", max_length=20, choices=Status.choices, default=Status.ACTIVE
    )
    is_current = models.BooleanField("nåværende plassering", default=True)
    verification_status = models.CharField(
        "kontrollstatus",
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.UNCHECKED,
    )
    observed_at = models.DateTimeField("observert", auto_now_add=True)
    ended_at = models.DateTimeField("avsluttet", null=True, blank=True)
    google_drive_id = models.CharField("Google Drive-ID", max_length=255, blank=True)
    google_drive_url = models.URLField("Google Drive-URL", max_length=1000, blank=True)

    class Meta:
        verbose_name = "filplassering"
        verbose_name_plural = "filplasseringer"
        ordering = ("asset", "-is_current", "-observed_at")
        indexes = [
            models.Index(
                fields=("storage_type", "relative_path"),
                name="file_location_path_idx",
            ),
            models.Index(
                fields=("status", "is_current"), name="file_location_state_idx"
            ),
            models.Index(
                fields=("verification_status",),
                name="file_location_verify_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("storage_root_key", "storage_type", "relative_path"),
                condition=models.Q(is_current=True),
                name="file_location_unique_current_path",
            )
        ]

    def clean_fields(self, exclude=None):
        self.relative_path = self.relative_path.strip().replace("\\", "/")
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        if (
            self.status in {self.Status.MOVED, self.Status.HISTORICAL}
            and self.is_current
        ):
            raise ValidationError(
                {
                    "is_current": "Flyttet eller historisk plassering kan ikke være nåværende."
                }
            )
        if self.storage_type != self.StorageType.GOOGLE_DRIVE and (
            self.google_drive_id or self.google_drive_url
        ):
            raise ValidationError(
                {
                    "google_drive_id": "Google Drive-felter krever lagringstype Google Drive."
                }
            )

    def resolved_nas_path(self):
        """Compatibility wrapper for callers not yet migrated to storage.py."""
        if self.storage_type != self.StorageType.NAS:
            return None
        from .storage import resolve_location

        try:
            return resolve_location(self).server_path
        except (ImproperlyConfigured, ValidationError, OSError):
            return None

    def __str__(self):
        return f"{self.get_storage_type_display()}: {self.relative_path}"


class FileChecksum(CanonicalModel):
    class Reason(models.TextChoices):
        INGEST = "ingest", "Innlesing"
        WRITEBACK = "writeback", "Etter metadataoppdatering"
        VERIFICATION = "verification", "Kontroll"

    asset = models.ForeignKey(
        FileAsset,
        on_delete=models.PROTECT,
        related_name="checksum_history",
        verbose_name="filressurs",
    )
    sha256 = models.CharField("SHA-256", max_length=64)
    reason = models.CharField("årsak", max_length=20, choices=Reason.choices)
    observed_at = models.DateTimeField("observert", auto_now_add=True)

    class Meta:
        verbose_name = "filkontrollsum"
        verbose_name_plural = "filkontrollsummer"
        ordering = ("-observed_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("asset", "sha256"), name="file_checksum_unique_value"
            )
        ]

    def clean_fields(self, exclude=None):
        self.sha256 = self.sha256.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValidationError({"sha256": "SHA-256 må ha 64 heksadesimale tegn."})
        super().clean_fields(exclude=exclude)

    def __str__(self):
        return f"{self.asset}: {self.sha256[:12]}…"
