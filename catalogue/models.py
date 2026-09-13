from django.core.exceptions import ValidationError
from django.db import models

from parties.models import ArtistIdentity, Party
from rights_core.models import CanonicalModel, validate_not_blank
from .validators import normalize_isrc, validate_language


class Recording(CanonicalModel):
    class Kind(models.TextChoices):
        SOUND = "sound", "Lydinnspilling"
        VIDEO = "video", "Musikkvideoinnspilling"

    class Status(models.TextChoices):
        DRAFT = "draft", "Utkast"
        REVIEWED = "reviewed", "Metadata kontrollert"

    title = models.CharField("tittel", max_length=500, validators=[validate_not_blank])
    version_designation = models.CharField(
        "versjonsbetegnelse", max_length=255, blank=True
    )
    recording_kind = models.CharField(
        "innspillingstype", max_length=20, choices=Kind.choices, blank=True
    )
    duration_ms = models.PositiveBigIntegerField(
        "varighet (millisekunder)", null=True, blank=True
    )
    language = models.CharField(
        max_length=64,
        blank=True,
        validators=[validate_language],
        verbose_name="språk",
        help_text="Valgfri språkkode, for eksempel nb eller en.",
    )
    metadata_status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name="metadatastatus",
        help_text="Beskriver bare metadata og dokumenterer ikke rettigheter eller klarering.",
    )

    class Meta:
        verbose_name = "innspilling"
        verbose_name_plural = "innspillinger"
        ordering = ("title", "id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(title__regex=r".*\S.*"),
                name="recording_nonblank_title",
            ),
            models.CheckConstraint(
                condition=models.Q(metadata_status__in=["draft", "reviewed"]),
                name="recording_valid_status",
            ),
            models.CheckConstraint(
                condition=models.Q(recording_kind__in=["", "sound", "video"]),
                name="recording_valid_kind",
            ),
        ]

    def __str__(self):
        return self.title


class RecordingContribution(CanonicalModel):
    class Role(models.TextChoices):
        PRIMARY = "primary_artist", "Hovedartist"
        FEATURED = "featured_artist", "Medvirkende artist"
        MUSICIAN = "musician", "Musiker"
        VOCALIST = "vocalist", "Vokalist"
        PRODUCER = "producer", "Produsent"
        ENGINEER = "engineer", "Lydtekniker"
        CONDUCTOR = "conductor", "Dirigent"
        CHOIR = "choir", "Kor"

    recording = models.ForeignKey(
        Recording,
        verbose_name="innspilling",
        on_delete=models.CASCADE,
        related_name="contributions",
    )
    party = models.ForeignKey(
        Party,
        verbose_name="person/organisasjon",
        on_delete=models.PROTECT,
        related_name="recording_contributions",
    )
    role = models.CharField("rolle", max_length=30, choices=Role.choices)
    artist_identity = models.ForeignKey(
        ArtistIdentity,
        verbose_name="artistidentitet",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    credited_as = models.CharField("kreditert som", max_length=255, blank=True)
    display_order = models.PositiveIntegerField("visningsrekkefølge", default=0)

    class Meta:
        verbose_name = "medvirkende"
        verbose_name_plural = "medvirkende"
        ordering = ("display_order", "id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    role__in=[
                        "primary_artist",
                        "featured_artist",
                        "musician",
                        "vocalist",
                        "producer",
                        "engineer",
                        "conductor",
                        "choir",
                    ]
                ),
                name="contribution_valid_role",
            )
        ]

    def clean(self):
        super().clean()
        if (
            self.artist_identity_id
            and self.party_id
            and self.artist_identity.party_id != self.party_id
        ):
            raise ValidationError(
                {
                    "artist_identity": (
                        "Artistidentiteten må tilhøre valgt person eller organisasjon."
                    )
                }
            )

    def __str__(self):
        return f"{self.party} — {self.get_role_display()} — {self.recording}"


class ExternalIdentifier(CanonicalModel):
    class Scheme(models.TextChoices):
        ISRC = "ISRC", "ISRC"

    recording = models.ForeignKey(
        Recording,
        verbose_name="innspilling",
        on_delete=models.CASCADE,
        related_name="identifiers",
    )
    scheme = models.CharField(
        "type", max_length=30, choices=Scheme.choices, default=Scheme.ISRC
    )
    namespace = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="navnerom",
        help_text="ISRC er global: la feltet stå tomt. Feltet er reservert for senere identifikatortyper.",
    )
    value = models.CharField(
        max_length=255,
        verbose_name="verdi",
        help_text="Den innskrevne verdien beholdes. Normalisering skjer automatisk.",
    )
    normalized_value = models.CharField(
        "normalisert verdi", max_length=255, editable=False
    )

    class Meta:
        verbose_name = "ekstern identifikator"
        verbose_name_plural = "eksterne identifikatorer"
        ordering = ("scheme", "normalized_value")
        constraints = [
            models.UniqueConstraint(
                fields=("scheme", "namespace", "normalized_value"),
                name="identifier_unique_assignment",
            ),
            models.UniqueConstraint(
                fields=("recording", "scheme"), name="recording_one_isrc"
            ),
            models.CheckConstraint(
                condition=models.Q(scheme="ISRC", namespace=""),
                name="identifier_supported_scheme",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    normalized_value__regex=r"^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$"
                ),
                name="identifier_valid_isrc",
            ),
        ]

    def clean_fields(self, exclude=None):
        if self.scheme == self.Scheme.ISRC:
            try:
                self.normalized_value = normalize_isrc(self.value)
            except ValidationError as error:
                raise ValidationError({"value": error.messages}) from error
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        if self.namespace:
            raise ValidationError(
                {"namespace": "ISRC bruker et globalt navnerom. La feltet stå tomt."}
            )

    def __str__(self):
        return f"{self.scheme}: {self.normalized_value}"

    def save(self, *args, **kwargs):
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {
                "normalized_value"
            }
        return super().save(*args, **kwargs)
