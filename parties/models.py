from django.core.exceptions import ValidationError
from django.db import models

from rights_core.models import CanonicalModel, validate_not_blank


class Party(CanonicalModel):
    class Kind(models.TextChoices):
        PERSON = "person", "Person"
        ORGANIZATION = "organization", "Organisasjon"
        GROUP = "group", "Gruppe"

    name = models.CharField(
        "navn",
        max_length=255,
        validators=[validate_not_blank],
        help_text="Navnet på personen, organisasjonen eller gruppen. Navnet dokumenterer ikke eierskap.",
    )
    kind = models.CharField("type", max_length=20, choices=Kind.choices)

    class Meta:
        verbose_name = "person/organisasjon"
        verbose_name_plural = "personer og organisasjoner"
        ordering = ("name", "id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kind__in=["person", "organization", "group"]),
                name="party_valid_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(name__regex=r".*\S.*"),
                name="party_nonblank_name",
            ),
        ]

    def __str__(self):
        return self.name


class ArtistIdentity(CanonicalModel):
    party = models.ForeignKey(
        Party,
        verbose_name="person/organisasjon",
        on_delete=models.PROTECT,
        related_name="artist_identities",
    )
    display_name = models.CharField(
        "artistnavn", max_length=255, validators=[validate_not_blank]
    )

    class Meta:
        verbose_name = "artistidentitet"
        verbose_name_plural = "artistidentiteter"
        ordering = ("display_name", "id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(display_name__regex=r".*\S.*"),
                name="artist_nonblank_name",
            )
        ]

    def clean(self):
        super().clean()
        if not self._state.adding:
            original = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("party_id", flat=True)
                .first()
            )
            if original and original != self.party_id:
                raise ValidationError(
                    {
                        "party": "Opprett en ny artistidentitet for en annen person eller organisasjon."
                    }
                )

    def __str__(self):
        return self.display_name
