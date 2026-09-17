from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from catalogue.models import Recording
from media_assets.models import FileAsset, FileLocation
from rights_core.models import CanonicalModel


class DeliveryProfile(models.TextChoices):
    INTERNAL_COMPLETE = "internal_complete", "Intern – komplett"
    EXTERNAL_RADIO = "external_radio", "Ekstern radio"


class Delivery(CanonicalModel):
    class Status(models.TextChoices):
        PREPARING = "preparing", "Forbereder"
        READY = "ready", "Klar"
        PARTIAL = "partial", "Delvis"
        FAILED = "failed", "Kan ikke leveres"
        EXPIRED = "expired", "Artefakt utløpt"

    class Purpose(models.TextChoices):
        INTERNAL = "internal", "Intern bruk"
        BROADCAST = "broadcast", "Kringkasting"
        ARCHIVE = "archive", "Arkivarbeid"
        RIGHTSHOLDER = "rightsholder", "Levering til artist/rettighetshaver"
        DISTRIBUTION = "distribution", "Distribusjonsrelatert arbeid"
        OTHER = "other", "Annet"

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="deliveries",
    )
    status = models.CharField(max_length=20, choices=Status.choices)
    profile = models.CharField(max_length=30, choices=DeliveryProfile.choices)
    purpose = models.CharField(max_length=30, choices=Purpose.choices)
    purpose_description = models.TextField(blank=True)
    retain_for_future_use = models.BooleanField(default=False)
    other_use = models.BooleanField(default=False)
    other_use_description = models.TextField(blank=True)
    recipient_name = models.CharField(max_length=255)
    recipient_organization = models.CharField(max_length=255, blank=True)
    manifest_snapshot = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ("-created_at", "id")
        permissions = [
            ("create_delivery", "Kan opprette leveranse"),
            ("download_delivery", "Kan laste ned leveranse"),
            ("create_external_delivery", "Kan opprette ekstern leveranse"),
        ]

    def clean(self):
        super().clean()
        if (
            self.purpose == self.Purpose.OTHER
            and not self.purpose_description.strip()
        ):
            raise ValidationError(
                {"purpose_description": "Beskriv formålet når Annet er valgt."}
            )
        if self.other_use and not self.other_use_description.strip():
            raise ValidationError(
                {"other_use_description": "Beskriv den andre bruken."}
            )


class DeliveryItem(CanonicalModel):
    class Status(models.TextChoices):
        READY = "ready", "Klar"
        SKIPPED = "skipped", "Hoppet over"
        FAILED = "failed", "Feilet"

    delivery = models.ForeignKey(
        Delivery, on_delete=models.CASCADE, related_name="items"
    )
    recording = models.ForeignKey(
        Recording, on_delete=models.PROTECT, related_name="delivery_items"
    )
    source_file_asset = models.ForeignKey(
        FileAsset,
        on_delete=models.PROTECT,
        related_name="delivery_items",
        null=True,
        blank=True,
    )
    source_file_location = models.ForeignKey(
        FileLocation,
        on_delete=models.PROTECT,
        related_name="delivery_items",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=20, choices=Status.choices)
    output_filename = models.CharField(max_length=255, blank=True)
    recording_uuid_snapshot = models.UUIDField()
    title_snapshot = models.CharField(max_length=500)
    artist_snapshot = models.CharField(max_length=500, blank=True)
    isrc_snapshot = models.CharField(max_length=30, blank=True)
    source_asset_uuid_snapshot = models.UUIDField(null=True, blank=True)
    source_sha256_snapshot = models.CharField(max_length=64, blank=True)
    delivered_metadata = models.JSONField(default=dict, blank=True)
    message = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("delivery", "recording"),
                name="delivery_one_item_per_recording",
            )
        ]

    def clean(self):
        super().clean()
        if self.status == self.Status.READY:
            if (
                not self.source_file_asset_id
                or not self.source_file_location_id
            ):
                raise ValidationError(
                    "Et klart element må fryse både filressurs og plassering."
                )
            if (
                self.source_file_asset.recording_id != self.recording_id
                or self.source_file_asset.role != FileAsset.Role.RADIO_FLAC
                or self.source_file_location.asset_id
                != self.source_file_asset_id
            ):
                raise ValidationError(
                    "Leveransekilden må være en radio-FLAC for samme Recording."
                )


class DeliveryArtifact(CanonicalModel):
    class Kind(models.TextChoices):
        TRANSFORMED_FLAC = "transformed_flac", "Transformert FLAC"
        ZIP = "zip", "ZIP-arkiv"

    delivery = models.ForeignKey(
        Delivery, on_delete=models.CASCADE, related_name="artifacts"
    )
    kind = models.CharField(max_length=30, choices=Kind.choices)
    relative_path = models.CharField(max_length=500)
    filename = models.CharField(max_length=255)
    size_bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "id")


class DownloadEvent(CanonicalModel):
    class EventType(models.TextChoices):
        REQUESTED = "requested", "Nedlasting forespurt"
        SERVED = "served", "Nedlasting servert"

    delivery = models.ForeignKey(
        Delivery, on_delete=models.CASCADE, related_name="download_events"
    )
    delivery_item = models.ForeignKey(
        DeliveryItem,
        on_delete=models.CASCADE,
        related_name="download_events",
        null=True,
        blank=True,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT
    )
    event_type = models.CharField(max_length=20, choices=EventType.choices)

    class Meta:
        ordering = ("-created_at", "id")
