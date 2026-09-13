from django import forms
from django.core.exceptions import ValidationError

from catalogue.models import Recording
from media_assets.models import FileAsset

from .models import Agreement, AgreementDocument, AgreementParty, RightsClaim
from .services import _validate_territory_scope


class RightsClaimForm(forms.ModelForm):
    class Meta:
        model = RightsClaim
        fields = (
            "right_type",
            "rights_holder",
            "grantor",
            "share",
            "territory_mode",
            "territories",
            "valid_from",
            "valid_until",
            "evidence_strength",
            "source_record",
            "agreement",
            "notes",
        )
        widgets = {
            "valid_from": forms.DateInput(attrs={"type": "date"}),
            "valid_until": forms.DateInput(attrs={"type": "date"}),
            "territories": forms.CheckboxSelectMultiple,
        }
        labels = {"territories": "Territorier"}

    def clean(self):
        data = super().clean()
        try:
            _validate_territory_scope(
                data.get("territory_mode"), data.get("territories") or ()
            )
        except ValidationError as error:
            self.add_error("territories", error)
        return data


class ReleaseRightsClaimForm(RightsClaimForm):
    recordings = forms.ModelMultipleChoiceField(
        queryset=Recording.objects.none(),
        label="Spor/innspillinger som omfattes",
        widget=forms.CheckboxSelectMultiple,
        help_text=(
            "Det opprettes ett krav per unik innspilling. Utgivelsen brukes som "
            "arbeidskontekst, ikke som eier av masterrettigheten."
        ),
    )

    def __init__(self, *args, release, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = (
            Recording.objects.filter(release_tracks__release=release)
            .distinct()
            .order_by("title", "id")
        )
        self.fields["recordings"].queryset = queryset
        self.initial.setdefault("recordings", queryset)
        self.order_fields(("recordings", *self._meta.fields))


class RightsDecisionForm(forms.Form):
    action = forms.ChoiceField(
        choices=(("confirm", "Bekreft"), ("dispute", "Bestrid"), ("reject", "Avvis")),
        widget=forms.HiddenInput,
    )
    note = forms.CharField(
        label="Begrunnelse", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )


class AgreementForm(forms.ModelForm):
    class Meta:
        model = Agreement
        fields = (
            "title",
            "internal_reference",
            "agreement_type",
            "effective_date",
            "expiry_date",
            "status",
            "notes",
        )
        widgets = {
            "effective_date": forms.DateInput(attrs={"type": "date"}),
            "expiry_date": forms.DateInput(attrs={"type": "date"}),
        }


class AgreementPartyForm(forms.ModelForm):
    class Meta:
        model = AgreementParty
        fields = ("party", "role", "notes")


class AgreementDocumentForm(forms.ModelForm):
    class Meta:
        model = AgreementDocument
        fields = ("file_asset", "description")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["file_asset"].queryset = FileAsset.objects.filter(
            role=FileAsset.Role.DOCUMENT
        )


class ClaimAgreementForm(forms.Form):
    agreement = forms.ModelChoiceField(Agreement.objects.all(), label="Avtale")
    note = forms.CharField(
        label="Begrunnelse", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )
