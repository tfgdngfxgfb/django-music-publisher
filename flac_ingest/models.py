from django.conf import settings
from django.db import models

from catalogue.models import Recording, Release, ReleaseTrack
from media_assets.models import FileAsset, validate_logical_path
from provenance.models import ImportBatch, SourceRecord
from rights_core.models import CanonicalModel


class FlacIngestBatch(CanonicalModel):
    class Status(models.TextChoices):
        PREVIEW = "preview", "Forhåndsvisning klar"
        PARTIAL = "partial", "Delvis brukt"
        APPLIED = "applied", "Brukt"

    import_batch = models.OneToOneField(
        ImportBatch,
        on_delete=models.PROTECT,
        related_name="flac_ingest",
        verbose_name="provenancebatch",
    )
    relative_root = models.CharField(
        "valgt mappe",
        max_length=1000,
        default=".",
        validators=[validate_logical_path],
    )
    recursive = models.BooleanField("inkluder undermapper", default=True)
    allow_uuid_recovery = models.BooleanField(
        "tillat gjenoppretting fra P7UUID",
        default=False,
        help_text=(
            "Eksplisitt administratoroverstyring for test, regenerering eller "
            "kontrollert reimport. Skal vurderes før operativ produksjonsbruk."
        ),
    )
    status = models.CharField(
        "status", max_length=20, choices=Status.choices, default=Status.PREVIEW
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="flac_ingest_batches",
        verbose_name="opprettet av",
    )

    class Meta:
        verbose_name = "FLAC-innlesing"
        verbose_name_plural = "FLAC-innlesinger"
        ordering = ("-created_at", "id")
        permissions = (("apply_flacingestbatch", "Kan bruke FLAC-forhåndsvisning"),)

    def __str__(self):
        return f"{self.relative_root} – {self.created_at:%Y-%m-%d %H:%M}"


class FlacIngestItem(CanonicalModel):
    class Action(models.TextChoices):
        NEW = "new", "Ny innspilling"
        MATCHED = "matched", "Eksisterende treff"
        UPDATED = "updated", "Endret fil"
        UNCHANGED = "unchanged", "Uendret fil"
        RETRY = "retry", "Prøv igjen"
        CONFLICT = "conflict", "Må kontrolleres"
        INVALID = "invalid", "Kan ikke leses"

    batch = models.ForeignKey(
        FlacIngestBatch,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="innlesing",
    )
    relative_path = models.CharField(
        "relativ filsti", max_length=1000, validators=[validate_logical_path]
    )
    action = models.CharField("forslag", max_length=20, choices=Action.choices)
    match_method = models.CharField("treffgrunnlag", max_length=50, blank=True)
    raw_tags = models.JSONField("alle råtags", default=dict, blank=True)
    parsed_metadata = models.JSONField("tolkede metadata", default=dict, blank=True)
    technical_metadata = models.JSONField(
        "tekniske lydopplysninger", default=dict, blank=True
    )
    file_size = models.PositiveBigIntegerField("filstørrelse", null=True, blank=True)
    source_modified_at = models.DateTimeField("fil endret", null=True, blank=True)
    sha256 = models.CharField("SHA-256", max_length=64, blank=True)
    candidates = models.JSONField("mulige treff", default=list, blank=True)
    messages = models.JSONField("kontrollmeldinger", default=list, blank=True)
    recording = models.ForeignKey(
        Recording,
        on_delete=models.PROTECT,
        related_name="flac_ingest_items",
        null=True,
        blank=True,
    )
    release = models.ForeignKey(
        Release,
        on_delete=models.PROTECT,
        related_name="flac_ingest_items",
        null=True,
        blank=True,
    )
    release_track = models.ForeignKey(
        ReleaseTrack,
        on_delete=models.PROTECT,
        related_name="flac_ingest_items",
        null=True,
        blank=True,
    )
    file_asset = models.ForeignKey(
        FileAsset,
        on_delete=models.PROTECT,
        related_name="flac_ingest_items",
        null=True,
        blank=True,
    )
    source_record = models.OneToOneField(
        SourceRecord,
        on_delete=models.PROTECT,
        related_name="flac_ingest_item",
        null=True,
        blank=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reviewed_flac_ingest_items",
        null=True,
        blank=True,
        verbose_name="kontrollert av",
    )
    reviewed_at = models.DateTimeField("kontrollert", null=True, blank=True)
    review_note = models.TextField("kontrollmerknad", blank=True)
    applied_at = models.DateTimeField("brukt", null=True, blank=True)

    class Meta:
        verbose_name = "FLAC-fil i forhåndsvisning"
        verbose_name_plural = "FLAC-filer i forhåndsvisning"
        ordering = ("relative_path", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("batch", "relative_path"), name="flac_batch_unique_path"
            )
        ]

    @property
    def can_apply(self):
        return (
            self.action in {
                self.Action.NEW,
                self.Action.MATCHED,
                self.Action.UPDATED,
            }
            and not self.applied_at
        )

    def __str__(self):
        return self.relative_path


class FlacSyncLog(CanonicalModel):
    class Result(models.TextChoices):
        SUCCESS = "success", "Synkronisert"
        MISSING = "missing", "Fil ikke funnet"
        CONFLICT = "conflict", "Konflikt"
        FAILED = "failed", "Feilet"

    asset = models.ForeignKey(
        FileAsset,
        on_delete=models.PROTECT,
        related_name="sync_logs",
        verbose_name="radio-FLAC",
    )
    result = models.CharField("resultat", max_length=20, choices=Result.choices)
    written_tags = models.JSONField("skrevne katalogtags", default=dict, blank=True)
    protected_tags = models.JSONField("bevarte radiotags", default=dict, blank=True)
    error = models.TextField("feil", blank=True)

    class Meta:
        verbose_name = "FLAC-synkroniseringslogg"
        verbose_name_plural = "FLAC-synkroniseringslogger"
        ordering = ("-created_at", "id")

    def __str__(self):
        return f"{self.asset} – {self.get_result_display()}"
