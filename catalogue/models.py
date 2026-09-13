from django.core.exceptions import ValidationError
from django.db import models

from parties.models import ArtistIdentity, Party
from rights_core.models import CanonicalModel, VerificationStatus, validate_not_blank
from .validators import normalize_isrc, normalize_trade_item_number, validate_language


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


class Label(CanonicalModel):
    name = models.CharField("navn", max_length=255, validators=[validate_not_blank])
    party = models.ForeignKey(
        Party,
        verbose_name="tilknyttet person/organisasjon",
        on_delete=models.PROTECT,
        related_name="labels",
        null=True,
        blank=True,
        help_text="Valgfritt. Tilknytningen dokumenterer ikke eierskap.",
    )
    notes = models.TextField("merknader", blank=True)

    class Meta:
        verbose_name = "label"
        verbose_name_plural = "labels"
        ordering = ("name", "id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(name__regex=r".*\S.*"), name="label_nonblank_name"
            )
        ]

    def __str__(self):
        return self.name


class Release(CanonicalModel):
    class Type(models.TextChoices):
        LP = "lp", "LP"
        CASSETTE = "cassette", "Kassett"
        CD = "cd", "CD"
        EP = "ep", "EP"
        SINGLE = "single", "Single"
        DIGITAL = "digital", "Digital utgivelse"
        OTHER = "other", "Annen"

    title = models.CharField("tittel", max_length=500, validators=[validate_not_blank])
    release_type = models.CharField(
        "utgivelsestype", max_length=20, choices=Type.choices, blank=True
    )
    release_date = models.DateField("utgivelsesdato", null=True, blank=True)
    release_year = models.PositiveSmallIntegerField(
        "utgivelsesår", null=True, blank=True, help_text="Bruk når bare året er kjent."
    )
    label = models.ForeignKey(
        Label,
        verbose_name="label",
        on_delete=models.PROTECT,
        related_name="releases",
        null=True,
        blank=True,
    )
    catalogue_number = models.CharField("katalognummer", max_length=100, blank=True)
    verification_status = models.CharField(
        "verifikasjonsstatus",
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.UNVERIFIED,
    )
    notes = models.TextField("merknader", blank=True)

    class Meta:
        verbose_name = "utgivelse"
        verbose_name_plural = "utgivelser"
        ordering = ("title", "id")
        indexes = [
            models.Index(fields=("catalogue_number",), name="release_catalogue_idx"),
            models.Index(fields=("release_year",), name="release_year_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(title__regex=r".*\S.*"),
                name="release_nonblank_title",
            ),
            models.CheckConstraint(
                condition=models.Q(release_year__isnull=True)
                | models.Q(release_year__gte=1800, release_year__lte=2200),
                name="release_sensible_year",
            ),
        ]

    def clean(self):
        super().clean()
        if (
            self.release_date
            and self.release_year
            and self.release_date.year != self.release_year
        ):
            raise ValidationError(
                {"release_year": "Utgivelsesår og utgivelsesdato må angi samme år."}
            )

    def __str__(self):
        return self.title


class ReleaseTrack(CanonicalModel):
    release = models.ForeignKey(
        Release,
        verbose_name="utgivelse",
        on_delete=models.CASCADE,
        related_name="tracks",
    )
    recording = models.ForeignKey(
        Recording,
        verbose_name="innspilling",
        on_delete=models.PROTECT,
        related_name="release_tracks",
    )
    disc_number = models.PositiveSmallIntegerField("disc/medium", null=True, blank=True)
    side = models.CharField(
        "side", max_length=10, blank=True, help_text="For eksempel A, B, C eller D."
    )
    track_number = models.PositiveSmallIntegerField("spornummer", null=True, blank=True)
    title_override = models.CharField(
        "utgivelsesspesifikk tittel", max_length=500, blank=True
    )
    duration_ms = models.PositiveBigIntegerField(
        "varighet på utgivelsen (millisekunder)", null=True, blank=True
    )
    sequence_number = models.PositiveIntegerField(
        "sorteringsrekkefølge", help_text="Unik rekkefølge innen utgivelsen."
    )

    class Meta:
        verbose_name = "spor på utgivelse"
        verbose_name_plural = "spor på utgivelser"
        ordering = ("release", "sequence_number", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("release", "sequence_number"),
                name="release_track_unique_sequence",
            ),
            models.CheckConstraint(
                condition=models.Q(sequence_number__gte=1),
                name="release_track_positive_sequence",
            ),
        ]
        indexes = [
            models.Index(
                fields=("release", "disc_number", "side", "track_number"),
                name="release_track_position_idx",
            ),
            models.Index(fields=("recording",), name="release_track_recording_idx"),
        ]

    @property
    def display_title(self):
        return self.title_override or self.recording.title

    def __str__(self):
        position = self.side or self.disc_number or ""
        number = self.track_number or self.sequence_number
        return f"{self.release}: {position}{number} — {self.display_title}"


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
        COMPOSER = "composer", "Komponist"
        LYRICIST = "lyricist", "Tekstforfatter"
        ARRANGER = "arranger", "Arrangør"

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
        null=True,
        blank=True,
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
    source_record = models.ForeignKey(
        "provenance.SourceRecord",
        verbose_name="kildepost",
        on_delete=models.PROTECT,
        related_name="recording_contributions",
        null=True,
        blank=True,
    )

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
                        "composer",
                        "lyricist",
                        "arranger",
                    ]
                ),
                name="contribution_valid_role",
            ),
            models.CheckConstraint(
                condition=models.Q(party__isnull=False)
                | ~models.Q(credited_as=""),
                name="contribution_has_identity_or_credit",
            ),
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
        name = self.credited_as or str(self.party)
        return f"{name} — {self.get_role_display()} — {self.recording}"


class ExternalIdentifier(CanonicalModel):
    class Scheme(models.TextChoices):
        ISRC = "ISRC", "ISRC"
        UPC = "UPC", "UPC"
        EAN = "EAN", "EAN"
        GTIN = "GTIN", "GTIN"
        EXTERNAL = "EXTERNAL", "Ekstern system-ID"

    recording = models.ForeignKey(
        Recording,
        verbose_name="innspilling",
        on_delete=models.CASCADE,
        related_name="identifiers",
        null=True,
        blank=True,
    )
    release = models.ForeignKey(
        Release,
        verbose_name="utgivelse",
        on_delete=models.CASCADE,
        related_name="identifiers",
        null=True,
        blank=True,
    )
    scheme = models.CharField(
        "type", max_length=30, choices=Scheme.choices, default=Scheme.ISRC
    )
    namespace = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="navnerom",
        help_text="La feltet stå tomt for ISRC og strekkoder. Oppgi systemnavn for ekstern ID.",
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
                fields=("recording", "scheme", "namespace"),
                condition=models.Q(recording__isnull=False),
                name="recording_one_identifier_per_scheme",
            ),
            models.UniqueConstraint(
                fields=("release", "scheme", "namespace"),
                condition=models.Q(release__isnull=False),
                name="release_one_identifier_per_scheme",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(recording__isnull=False, release__isnull=True)
                    | models.Q(recording__isnull=True, release__isnull=False)
                ),
                name="identifier_exactly_one_target",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        scheme="ISRC", recording__isnull=False, release__isnull=True
                    )
                    | models.Q(
                        scheme__in=["UPC", "EAN", "GTIN"],
                        recording__isnull=True,
                        release__isnull=False,
                    )
                    | models.Q(scheme="EXTERNAL")
                ),
                name="identifier_scheme_matches_target",
            ),
            models.CheckConstraint(
                condition=~models.Q(scheme="ISRC")
                | models.Q(normalized_value__regex=r"^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$"),
                name="identifier_valid_isrc",
            ),
        ]

    def clean_fields(self, exclude=None):
        if self.scheme == self.Scheme.ISRC:
            try:
                self.normalized_value = normalize_isrc(self.value)
            except ValidationError as error:
                raise ValidationError({"value": error.messages}) from error
        elif self.scheme in {self.Scheme.UPC, self.Scheme.EAN, self.Scheme.GTIN}:
            try:
                self.normalized_value = normalize_trade_item_number(
                    self.value, self.scheme
                )
            except ValidationError as error:
                raise ValidationError({"value": error.messages}) from error
        else:
            self.normalized_value = self.value.strip()
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        if self.scheme != self.Scheme.EXTERNAL and self.namespace:
            raise ValidationError(
                {
                    "namespace": "ISRC og strekkoder bruker globalt navnerom. La feltet stå tomt."
                }
            )
        if self.scheme == self.Scheme.EXTERNAL and not self.namespace.strip():
            raise ValidationError(
                {"namespace": "Oppgi kildesystem for en ekstern system-ID."}
            )

    def __str__(self):
        return f"{self.scheme}: {self.normalized_value}"

    def save(self, *args, **kwargs):
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {
                "normalized_value"
            }
        return super().save(*args, **kwargs)


class DuplicateCandidate(CanonicalModel):
    class Status(models.TextChoices):
        OPEN = "open", "Til vurdering"
        DISMISSED = "dismissed", "Avvist som dublett"
        RESOLVED = "resolved", "Løst manuelt"

    recording_a = models.ForeignKey(
        Recording,
        verbose_name="første innspilling",
        on_delete=models.PROTECT,
        related_name="duplicate_candidates_as_a",
    )
    recording_b = models.ForeignKey(
        Recording,
        verbose_name="andre innspilling",
        on_delete=models.PROTECT,
        related_name="duplicate_candidates_as_b",
    )
    signals = models.JSONField(
        "matchsignaler",
        default=list,
        help_text="Maskinlesbare grunner til mulig match.",
    )
    score = models.PositiveSmallIntegerField("matchpoeng", default=0)
    status = models.CharField(
        "status", max_length=20, choices=Status.choices, default=Status.OPEN
    )
    notes = models.TextField("vurdering", blank=True)

    class Meta:
        verbose_name = "mulig dublett"
        verbose_name_plural = "mulige dubletter"
        ordering = ("status", "-score", "created_at")
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(recording_a=models.F("recording_b")),
                name="duplicate_candidate_distinct_recordings",
            ),
            models.UniqueConstraint(
                fields=("recording_a", "recording_b"),
                name="duplicate_candidate_unique_pair",
            ),
            models.CheckConstraint(
                condition=models.Q(score__lte=100),
                name="duplicate_candidate_score_range",
            ),
        ]

    def clean(self):
        super().clean()
        if self.recording_a_id and self.recording_b_id:
            if self.recording_a_id == self.recording_b_id:
                raise ValidationError(
                    "En innspilling kan ikke være dublett av seg selv."
                )
            if str(self.recording_a_id) > str(self.recording_b_id):
                self.recording_a_id, self.recording_b_id = (
                    self.recording_b_id,
                    self.recording_a_id,
                )

    def __str__(self):
        return f"{self.recording_a} ↔ {self.recording_b}"
