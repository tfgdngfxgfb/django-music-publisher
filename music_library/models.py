from django.core.exceptions import ValidationError
from django.db import models

from catalogue.models import Recording
from catalogue.validators import validate_language
from rights_core.models import CanonicalModel, VerificationStatus


class MusicLibraryEntry(CanonicalModel):
    class Target(models.TextChoices):
        GENERAL = "general", "Alle"
        CHILDREN = "children", "Barn"
        YOUTH = "youth", "Ungdom"
        ADULT = "adult", "Voksne"
        FAMILY = "family", "Familie"

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
    target = models.CharField(
        "målgruppe", max_length=20, choices=Target.choices, blank=True
    )
    channel = models.CharField(
        "kanal",
        max_length=100,
        blank=True,
        help_text="Kontrollert kanalnavn eller kode når kjent.",
    )
    gender = models.CharField(
        "kjønn", max_length=20, choices=Gender.choices, blank=True
    )
    rating = models.PositiveSmallIntegerField(
        "rating", null=True, blank=True, help_text="Skala 1–5."
    )
    energy = models.PositiveSmallIntegerField(
        "energi", null=True, blank=True, help_text="Skala 1–5."
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
            models.Index(fields=("channel",), name="library_channel_idx"),
            models.Index(fields=("verification_status",), name="library_verify_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rating__isnull=True)
                | models.Q(rating__gte=1, rating__lte=5),
                name="library_rating_range",
            ),
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
