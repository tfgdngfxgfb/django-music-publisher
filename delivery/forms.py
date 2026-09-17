from django import forms

from .models import Delivery, DeliveryProfile


class DeliveryForm(forms.Form):
    profile = forms.ChoiceField(
        choices=DeliveryProfile.choices, label="Profil"
    )
    purpose = forms.ChoiceField(
        choices=Delivery.Purpose.choices, label="Formål"
    )
    purpose_description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Formålsbeskrivelse",
    )
    recipient_name = forms.CharField(max_length=255, label="Mottaker")
    recipient_organization = forms.CharField(
        max_length=255, required=False, initial="P7", label="Organisasjon"
    )
    retain_for_future_use = forms.BooleanField(
        required=False,
        label="Mottaker/behov tilsier at filene beholdes for senere bruk",
    )
    other_use = forms.BooleanField(required=False, label="Annen bruk")
    other_use_description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Beskrivelse av annen bruk",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and not self.is_bound:
            self.initial.setdefault(
                "recipient_name", user.get_full_name() or user.get_username()
            )
            self.initial.setdefault("purpose", Delivery.Purpose.INTERNAL)
            self.initial.setdefault(
                "profile", DeliveryProfile.INTERNAL_COMPLETE
            )
        if user and not user.has_perm("delivery.create_external_delivery"):
            self.fields["profile"].choices = [
                (
                    DeliveryProfile.INTERNAL_COMPLETE,
                    DeliveryProfile.INTERNAL_COMPLETE.label,
                )
            ]

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("purpose") == Delivery.Purpose.OTHER
            and not cleaned.get("purpose_description", "").strip()
        ):
            self.add_error(
                "purpose_description", "Beskriv formålet når Annet er valgt."
            )
        if (
            cleaned.get("other_use")
            and not cleaned.get("other_use_description", "").strip()
        ):
            self.add_error(
                "other_use_description", "Beskriv den andre bruken."
            )
        return cleaned
