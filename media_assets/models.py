import re
from pathlib import Path, PurePosixPath, PureWindowsPath

from django.conf import settings
from django.core.exceptions import ValidationError
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
    technical_metadata = models.JSONField("tekniske metadata", default=dict, blank=True)
    metadata_read_at = models.DateTimeField(
        "filmetadata sist lest", null=True, blank=True
    )
    source_modified_at = models.DateTimeField(
        "fil sist endret", null=True, blank=True
    )
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
            models.Index(
                fields=("role", "sync_status"), name="file_asset_sync_idx"
            )
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
        "logisk relativ sti", max_length=1000, validators=[validate_logical_path]
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
                fields=("storage_type", "relative_path"), name="file_location_path_idx"
            ),
            models.Index(
                fields=("status", "is_current"), name="file_location_state_idx"
            ),
            models.Index(
                fields=("verification_status",), name="file_location_verify_idx"
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("storage_type", "relative_path"),
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
        if self.storage_type != self.StorageType.NAS or not settings.P7_NAS_ROOT:
            return None
        return Path(
            settings.P7_NAS_ROOT,
            *PurePosixPath(self.relative_path.replace("\\", "/")).parts,
        )

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
