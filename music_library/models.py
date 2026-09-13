from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from catalogue.models import Recording
from catalogue.validators import validate_language
from rights_core.models import CanonicalModel, VerificationStatus


code_validator = RegexValidator(
    regex=r"^[a-z0-9][a-z0-9_-]*$",
    message="Bruk små bokstaver, tall, bindestrek eller understrek i koden.",
)


class Channel(CanonicalModel):
    code = models.CharField(
        "kode", max_length=50, unique=True, validators=[code_validator]
    )
    name = models.CharField("navn", max_length=100, unique=True)
    is_active = models.BooleanField("aktiv", default=True)

    class Meta:
        verbose_name = "kanal"
        verbose_name_plural = "kanaler"
        ordering = ("name", "id")

    def __str__(self):
        return self.name


class TargetAudience(CanonicalModel):
    code = models.CharField(
        "kode", max_length=50, unique=True, validators=[code_validator]
    )
    name = models.CharField("navn", max_length=100, unique=True)
    is_active = models.BooleanField("aktiv", default=True)

    class Meta:
        verbose_name = "målgruppe"
        verbose_name_plural = "målgrupper"
        ordering = ("name", "id")

    def __str__(self):
        return self.name


class MusicLibraryEntry(CanonicalModel):

    class Gender(models.TextChoices):
        FEMALE = "female", "Kvinne"
        MALE = "male", "Mann"
        MIXED = "mixed", "Blandet"
        OTHER = "other", "Annet / ikke relevant"

    recording = models.OneToOneField(
        Recording,
        verbose_name="innspilling",
        on_delete=models.PROTECT,
        related_name="music_library_entry",
    )
    genre = models.CharField("sjanger", max_length=100, blank=True)
    language = models.CharField(
        "språk", max_length=64, blank=True, validators=[validate_language]
    )
    channels = models.ManyToManyField(
        Channel,
        through="MusicLibraryChannel",
        related_name="library_entries",
        verbose_name="kanaler",
        blank=True,
    )
    target_audiences = models.ManyToManyField(
        TargetAudience,
        through="MusicLibraryTargetAudience",
        related_name="library_entries",
        verbose_name="målgrupper",
        blank=True,
    )
    gender = models.CharField(
        "kjønn", max_length=20, choices=Gender.choices, blank=True
    )
    energy = models.PositiveSmallIntegerField(
        "Energy",
        null=True,
        blank=True,
        help_text="P7s energinivå 1–5. Leses fra FLAC-taggen RATING.",
    )
    verification_status = models.CharField(
        "verifikasjonsstatus",
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.UNVERIFIED,
    )
    notes = models.TextField("merknader", blank=True)

    class Meta:
        verbose_name = "musikkarkivpost"
        verbose_name_plural = "musikkarkiv"
        ordering = ("recording__title", "id")
        indexes = [
            models.Index(fields=("genre",), name="library_genre_idx"),
            models.Index(fields=("verification_status",), name="library_verify_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(energy__isnull=True)
                | models.Q(energy__gte=1, energy__lte=5),
                name="library_energy_range",
            ),
        ]

    def clean(self):
        super().clean()
        if not self._state.adding:
            original = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("recording_id", flat=True)
                .first()
            )
            if original and original != self.recording_id:
                raise ValidationError(
                    {
                        "recording": "Opprett en ny musikkarkivpost for en annen innspilling."
                    }
                )

    def __str__(self):
        return self.recording.title


class MusicLibraryChannel(CanonicalModel):
    library_entry = models.ForeignKey(
        MusicLibraryEntry, on_delete=models.CASCADE, related_name="channel_links"
    )
    channel = models.ForeignKey(
        Channel, on_delete=models.PROTECT, related_name="library_links"
    )

    class Meta:
        verbose_name = "kanalvalg"
        verbose_name_plural = "kanalvalg"
        constraints = [
            models.UniqueConstraint(
                fields=("library_entry", "channel"),
                name="library_unique_channel",
            )
        ]


class MusicLibraryTargetAudience(CanonicalModel):
    library_entry = models.ForeignKey(
        MusicLibraryEntry, on_delete=models.CASCADE, related_name="target_links"
    )
    target_audience = models.ForeignKey(
        TargetAudience, on_delete=models.PROTECT, related_name="library_links"
    )

    class Meta:
        verbose_name = "målgruppevalg"
        verbose_name_plural = "målgruppevalg"
        constraints = [
            models.UniqueConstraint(
                fields=("library_entry", "target_audience"),
                name="library_unique_target",
            )
        ]
