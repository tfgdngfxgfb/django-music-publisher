from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from music_metadata.territories.territory import Territory as IndustryTerritory

from catalogue.models import Recording
from media_assets.models import FileAsset
from parties.models import Party
from provenance.models import SourceRecord
from rights_core.models import (
    CanonicalModel,
    VerificationStatus,
    validate_not_blank,
)

from .help_content import RIGHTS_HELP


class Territory(CanonicalModel):
    code = models.CharField(
        "ISO-landkode",
        max_length=2,
        unique=True,
        help_text="ISO 3166-1 alfa-2, for eksempel NO, SE eller DK.",
    )
    name_nb = models.CharField(
        "norsk navn", max_length=100, validators=[validate_not_blank]
    )

    class Meta:
        verbose_name = "territorium"
        verbose_name_plural = "territorier"
        ordering = ("name_nb", "code")

    def clean_fields(self, exclude=None):
        self.code = self.code.strip().upper()
        territory = IndustryTerritory.get(self.code)
        if not territory or not territory.is_country or len(self.code) != 2:
            raise ValidationError(
                {"code": "Bruk en gyldig ISO alfa-2-landkode."}
            )
        super().clean_fields(exclude=exclude)

    def __str__(self):
        return self.name_nb


class Agreement(CanonicalModel):
    class Type(models.TextChoices):
        TRANSFER = "transfer", "Overdragelse"
        LICENSE = "license", "Lisens"
        ADMINISTRATION = "administration", "Administrasjonsavtale"
        DISTRIBUTION = "distribution", "Distribusjonsavtale"
        OTHER = "other", "Annen avtale"

    class Status(models.TextChoices):
        DRAFT = "draft", "Utkast"
        ACTIVE = "active", "Aktiv"
        EXPIRED = "expired", "Utløpt"
        TERMINATED = "terminated", "Avsluttet"

    title = models.CharField(
        "tittel", max_length=500, validators=[validate_not_blank]
    )
    internal_reference = models.CharField(
        "intern referanse", max_length=100, blank=True
    )
    agreement_type = models.CharField(
        "avtaletype", max_length=30, choices=Type.choices
    )
    effective_date = models.DateField("virkningsdato", null=True, blank=True)
    expiry_date = models.DateField("utløpsdato", null=True, blank=True)
    status = models.CharField(
        "status", max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    notes = models.TextField("merknader", blank=True)
    parties = models.ManyToManyField(
        Party, through="AgreementParty", related_name="rights_agreements"
    )
    documents = models.ManyToManyField(
        FileAsset,
        through="AgreementDocument",
        related_name="rights_agreements",
    )

    class Meta:
        verbose_name = "avtale"
        verbose_name_plural = "avtaler"
        ordering = ("title", "id")
        permissions = (("manage_agreement", "Kan administrere avtaler"),)
        constraints = [
            models.UniqueConstraint(
                fields=("internal_reference",),
                condition=~models.Q(internal_reference=""),
                name="rights_agreement_unique_reference",
            ),
            models.CheckConstraint(
                condition=models.Q(expiry_date__isnull=True)
                | models.Q(effective_date__isnull=True)
                | models.Q(expiry_date__gte=models.F("effective_date")),
                name="rights_agreement_valid_period",
            ),
        ]

    def clean(self):
        super().clean()
        if (
            self.effective_date
            and self.expiry_date
            and self.expiry_date < self.effective_date
        ):
            raise ValidationError(
                {"expiry_date": "Utløpsdato kan ikke være før virkningsdato."}
            )

    def __str__(self):
        return self.title


class AgreementParty(CanonicalModel):
    class Role(models.TextChoices):
        ASSIGNOR = "assignor", "Overdrager"
        ASSIGNEE = "assignee", "Mottaker"
        LICENSOR = "licensor", "Lisensgiver"
        LICENSEE = "licensee", "Lisenshaver"
        DISTRIBUTOR = "distributor", "Distributør"
        ADMINISTRATOR = "administrator", "Administrator"
        OTHER = "other", "Annen rolle"

    agreement = models.ForeignKey(
        Agreement,
        on_delete=models.PROTECT,
        related_name="party_roles",
        verbose_name="avtale",
    )
    party = models.ForeignKey(
        Party,
        on_delete=models.PROTECT,
        related_name="agreement_roles",
        verbose_name="part",
    )
    role = models.CharField("rolle", max_length=30, choices=Role.choices)
    notes = models.CharField("merknad", max_length=500, blank=True)

    class Meta:
        verbose_name = "avtalepart"
        verbose_name_plural = "avtaleparter"
        ordering = ("agreement", "role", "party")
        constraints = [
            models.UniqueConstraint(
                fields=("agreement", "party", "role"),
                name="rights_agreement_party_unique_role",
            )
        ]

    def __str__(self):
        return f"{self.party} — {self.get_role_display()}"


class AgreementDocument(CanonicalModel):
    agreement = models.ForeignKey(
        Agreement,
        on_delete=models.PROTECT,
        related_name="document_links",
        verbose_name="avtale",
    )
    file_asset = models.ForeignKey(
        FileAsset,
        on_delete=models.PROTECT,
        related_name="agreement_links",
        verbose_name="dokumentfil",
    )
    description = models.CharField("beskrivelse", max_length=500, blank=True)

    class Meta:
        verbose_name = "avtaledokument"
        verbose_name_plural = "avtaledokumenter"
        constraints = [
            models.UniqueConstraint(
                fields=("agreement", "file_asset"),
                name="rights_agreement_unique_document",
            )
        ]

    def clean(self):
        super().clean()
        if (
            self.file_asset_id
            and self.file_asset.role != FileAsset.Role.DOCUMENT
        ):
            raise ValidationError(
                {
                    "file_asset": "Avtalegrunnlag må være registrert med filrollen Dokument."
                }
            )

    def __str__(self):
        return self.description or self.file_asset.filename


class RightsClaim(CanonicalModel):
    class RightType(models.TextChoices):
        OWNERSHIP = "master_ownership", "Mastereierskap"
        ADMINISTRATION = "master_administration", "Administrasjon"
        DISTRIBUTION = "distribution", "Distribusjon"

    class TerritoryMode(models.TextChoices):
        WORLD = "world", "Hele verden"
        INCLUDE = "include", "Bare angitte territorier"
        EXCLUDE = "exclude", "Hele verden unntatt angitte territorier"

    class EvidenceStrength(models.TextChoices):
        NOT_ASSESSED = "not_assessed", "Ikke vurdert"
        WEAK = "weak", "Svak indikasjon"
        PROBABLE = "probable", "Sannsynlig"
        STRONG = "strong", "Sterkt underbygget"
        DOCUMENTED = "documented", "Dokumentert"

    recording = models.ForeignKey(
        Recording,
        on_delete=models.PROTECT,
        related_name="rights_claims",
        verbose_name="innspilling",
    )
    right_type = models.CharField(
        "rettighetstype", max_length=30, choices=RightType.choices
    )
    rights_holder = models.ForeignKey(
        Party,
        on_delete=models.PROTECT,
        related_name="rights_held_claims",
        verbose_name="rettighetshaver",
    )
    grantor = models.ForeignKey(
        Party,
        on_delete=models.PROTECT,
        related_name="rights_granted_claims",
        verbose_name="rettighetsgiver",
        null=True,
        blank=True,
    )
    share = models.DecimalField(
        "andel i prosent",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    territory_mode = models.CharField(
        "territorieomfang",
        max_length=20,
        choices=TerritoryMode.choices,
        default=TerritoryMode.WORLD,
    )
    territories = models.ManyToManyField(
        Territory,
        through="ClaimTerritory",
        related_name="rights_claims",
        blank=True,
    )
    valid_from = models.DateField("gyldig fra", null=True, blank=True)
    valid_until = models.DateField("gyldig til", null=True, blank=True)
    status = models.CharField(
        "status",
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.UNVERIFIED,
        editable=False,
    )
    evidence_strength = models.CharField(
        "dokumentasjonsstyrke",
        max_length=20,
        choices=EvidenceStrength.choices,
        default=EvidenceStrength.NOT_ASSESSED,
        help_text=RIGHTS_HELP["evidence_strength"].short,
    )
    source_record = models.ForeignKey(
        SourceRecord,
        on_delete=models.PROTECT,
        related_name="rights_claims",
        verbose_name="kildepost",
        null=True,
        blank=True,
    )
    agreement = models.ForeignKey(
        Agreement,
        on_delete=models.PROTECT,
        related_name="rights_claims",
        verbose_name="avtale",
        null=True,
        blank=True,
    )
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="superseded_by",
        verbose_name="erstatter krav",
        null=True,
        blank=True,
    )
    notes = models.TextField("merknader", blank=True)

    class Meta:
        verbose_name = "rettighetskrav"
        verbose_name_plural = "rettighetskrav"
        ordering = ("recording", "right_type", "status", "rights_holder")
        permissions = (("decide_rightsclaim", "Kan beslutte rettighetskrav"),)
        indexes = [
            models.Index(
                fields=("recording", "right_type", "status"),
                name="rights_claim_summary_idx",
            ),
            models.Index(
                fields=("rights_holder", "right_type"),
                name="rights_claim_holder_idx",
            ),
            models.Index(
                fields=("valid_from", "valid_until"),
                name="rights_claim_period_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(share__isnull=True)
                | models.Q(share__gte=Decimal("0"), share__lte=Decimal("100")),
                name="rights_claim_share_range",
            ),
            models.CheckConstraint(
                condition=models.Q(valid_until__isnull=True)
                | models.Q(valid_from__isnull=True)
                | models.Q(valid_until__gte=models.F("valid_from")),
                name="rights_claim_valid_period",
            ),
        ]

    def clean(self):
        super().clean()
        if (
            self.valid_from
            and self.valid_until
            and self.valid_until < self.valid_from
        ):
            raise ValidationError(
                {"valid_until": "Sluttdato kan ikke være før startdato."}
            )
        if self.supersedes_id and (
            self.supersedes.recording_id != self.recording_id
            or self.supersedes.right_type != self.right_type
        ):
            raise ValidationError(
                {
                    "supersedes": "Et erstatningskrav må gjelde samme innspilling og rettighetstype."
                }
            )
        if not self._state.adding:
            original = (
                type(self)
                .objects.filter(pk=self.pk)
                .values(
                    "recording_id",
                    "right_type",
                    "rights_holder_id",
                    "grantor_id",
                    "share",
                    "territory_mode",
                    "valid_from",
                    "valid_until",
                    "source_record_id",
                    "agreement_id",
                    "supersedes_id",
                    "status",
                    "evidence_strength",
                )
                .first()
            )
            if (
                original
                and original["status"] != self.status
                and not getattr(self, "_allow_status_transition", False)
            ):
                raise ValidationError(
                    {
                        "status": "Status må endres gjennom en loggført rettighetsbeslutning."
                    }
                )
            protected_fields = (
                "recording_id",
                "right_type",
                "rights_holder_id",
                "grantor_id",
                "share",
                "territory_mode",
                "valid_from",
                "valid_until",
                "source_record_id",
                "agreement_id",
                "supersedes_id",
                "evidence_strength",
            )
            if (
                original
                and any(
                    original[field] != getattr(self, field)
                    for field in protected_fields
                )
                and not getattr(self, "_allow_claim_update", False)
            ):
                raise ValidationError(
                    "Et registrert rettighetskrav må korrigeres ved å opprette et erstatningskrav."
                )

    @property
    def territory_display(self):
        if self.territory_mode == self.TerritoryMode.WORLD:
            return "Hele verden"
        cached = getattr(self, "_prefetched_objects_cache", {}).get(
            "territories"
        )
        names = ", ".join(
            territory.name_nb
            for territory in (cached or self.territories.all())
        )
        prefix = (
            "Bare "
            if self.territory_mode == self.TerritoryMode.INCLUDE
            else "Hele verden unntatt "
        )
        return prefix + names

    def __str__(self):
        return f"{self.get_right_type_display()}: {self.rights_holder} — {self.recording}"


class ClaimTerritory(CanonicalModel):
    claim = models.ForeignKey(
        RightsClaim,
        on_delete=models.PROTECT,
        related_name="territory_links",
        verbose_name="rettighetskrav",
    )
    territory = models.ForeignKey(
        Territory,
        on_delete=models.PROTECT,
        related_name="claim_links",
        verbose_name="territorium",
    )

    class Meta:
        verbose_name = "kravterritorium"
        verbose_name_plural = "kravterritorier"
        constraints = [
            models.UniqueConstraint(
                fields=("claim", "territory"),
                name="rights_claim_unique_territory",
            )
        ]

    def clean(self):
        super().clean()
        if (
            self.claim_id
            and self.claim.territory_mode == RightsClaim.TerritoryMode.WORLD
        ):
            raise ValidationError(
                {
                    "claim": "Et verdensomspennende krav skal ikke ha territorieliste."
                }
            )


class RightsDecision(CanonicalModel):
    class Decision(models.TextChoices):
        CONFIRMED = VerificationStatus.CONFIRMED, "Bekreftet"
        DISPUTED = VerificationStatus.DISPUTED, "Bestridt"
        REJECTED = VerificationStatus.REJECTED, "Avvist"
        SUPERSEDED = VerificationStatus.SUPERSEDED, "Erstattet"
        DOCUMENTED = "documented", "Dokumentasjon oppdatert"

    claim = models.ForeignKey(
        RightsClaim,
        on_delete=models.PROTECT,
        related_name="decisions",
        verbose_name="rettighetskrav",
    )
    decision = models.CharField(
        "beslutning", max_length=20, choices=Decision.choices
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="rights_decisions",
        verbose_name="besluttet av",
    )
    note = models.TextField("begrunnelse", blank=True)

    class Meta:
        verbose_name = "rettighetsbeslutning"
        verbose_name_plural = "rettighetsbeslutninger"
        ordering = ("-created_at", "id")

    def clean(self):
        super().clean()
        if not self._state.adding:
            raise ValidationError(
                "En rettighetsbeslutning er historikk og kan ikke endres."
            )

    def __str__(self):
        return f"{self.claim} — {self.get_decision_display()}"


class RightsConfiguration(CanonicalModel):
    singleton = models.BooleanField(default=True, editable=False, unique=True)
    local_organization = models.OneToOneField(
        Party,
        on_delete=models.PROTECT,
        related_name="local_rights_configuration",
        verbose_name="lokal organisasjon",
    )

    class Meta:
        verbose_name = "rettighetsinnstilling"
        verbose_name_plural = "rettighetsinnstillinger"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(singleton=True),
                name="rights_configuration_singleton",
            )
        ]

    def __str__(self):
        return f"Lokal organisasjon: {self.local_organization}"
