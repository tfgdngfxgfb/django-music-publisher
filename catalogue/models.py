from django.core.exceptions import ValidationError
from django.db import models

from parties.models import ArtistIdentity, Party
from rights_core.models import CanonicalModel, validate_not_blank
from .validators import normalize_isrc, validate_language


class Recording(CanonicalModel):
    class Kind(models.TextChoices):
        SOUND = "sound", "Sound recording"
        VIDEO = "video", "Music video recording"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        REVIEWED = "reviewed", "Metadata reviewed"

    title = models.CharField(max_length=500, validators=[validate_not_blank])
    version_designation = models.CharField(max_length=255, blank=True)
    recording_kind = models.CharField(max_length=20, choices=Kind.choices, blank=True)
    duration_ms = models.PositiveBigIntegerField(
        "Duration (milliseconds)", null=True, blank=True
    )
    language = models.CharField(
        max_length=64,
        blank=True,
        validators=[validate_language],
        help_text="Optional language tag, e.g. nb or en.",
    )
    metadata_status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        help_text="Describes metadata only; does not establish rights or clearance.",
    )

    class Meta:
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
        PRIMARY = "primary_artist", "Primary artist"
        FEATURED = "featured_artist", "Featured artist"
        MUSICIAN = "musician", "Musician"
        VOCALIST = "vocalist", "Vocalist"
        PRODUCER = "producer", "Producer"
        ENGINEER = "engineer", "Engineer"
        CONDUCTOR = "conductor", "Conductor"
        CHOIR = "choir", "Choir"

    recording = models.ForeignKey(
        Recording, on_delete=models.CASCADE, related_name="contributions"
    )
    party = models.ForeignKey(
        Party, on_delete=models.PROTECT, related_name="recording_contributions"
    )
    role = models.CharField(max_length=30, choices=Role.choices)
    artist_identity = models.ForeignKey(
        ArtistIdentity, on_delete=models.PROTECT, null=True, blank=True
    )
    credited_as = models.CharField(max_length=255, blank=True)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
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
                        "Artist identity must belong to the selected party."
                    )
                }
            )

    def __str__(self):
        return f"{self.party} — {self.get_role_display()} — {self.recording}"


class ExternalIdentifier(CanonicalModel):
    class Scheme(models.TextChoices):
        ISRC = "ISRC", "ISRC"

    recording = models.ForeignKey(
        Recording, on_delete=models.CASCADE, related_name="identifiers"
    )
    scheme = models.CharField(
        max_length=30, choices=Scheme.choices, default=Scheme.ISRC
    )
    namespace = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="ISRC is global: leave empty. Reserved for future issuer-scoped schemes.",
    )
    value = models.CharField(
        max_length=255,
        help_text="Original entered value is retained; normalization is automatic.",
    )
    normalized_value = models.CharField(max_length=255, editable=False)

    class Meta:
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
                {"namespace": "ISRC uses the global namespace (leave empty)."}
            )

    def __str__(self):
        return f"{self.scheme}: {self.normalized_value}"

    def save(self, *args, **kwargs):
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {
                "normalized_value"
            }
        return super().save(*args, **kwargs)
