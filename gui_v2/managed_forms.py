"""GUI adapters; management validation and mutations remain in 6D/6E."""

from django import forms
from django.core.exceptions import ValidationError
from catalogue.models import Recording, Release
from managed_music.forms import ManagedRecordingCreationForm
from provenance.models import SourceSystem
from rights.scope import OwnershipCategory
from gui_v2.managed_music import STATUS_LABELS


class ManagedFilterForm(forms.Form):
    q = forms.CharField(
        label="Søk etter tittel, artist eller ISRC", required=False
    )
    status = forms.ChoiceField(
        label="Forvaltningsstatus",
        required=False,
        choices=[
            ("", "Aktuelle og oppfølging"),
            ("all", "Alle, inkludert historiske"),
            *STATUS_LABELS.items(),
        ],
    )
    follow_up = forms.ChoiceField(
        label="Oppfølging",
        required=False,
        choices=[
            ("", "Alle"),
            ("yes", "Krever oppfølging"),
            ("no", "Ingen oppfølging"),
        ],
    )
    ownership = forms.ChoiceField(
        label="Mastereierskap",
        required=False,
        choices=[("", "Alle"), *OwnershipCategory.choices],
    )
    source = forms.ModelChoiceField(
        SourceSystem.objects.all(), label="Forvaltningskilde", required=False
    )

    def __init__(self, *args, can_view_rights, **kwargs):
        super().__init__(*args, **kwargs)
        if can_view_rights:
            for name, label in (
                ("administration", "Administrasjon"),
                ("distribution", "Distribusjon"),
            ):
                self.fields[name] = forms.ChoiceField(
                    label=label,
                    required=False,
                    choices=[
                        ("", "Alle"),
                        ("confirmed", "Bekreftet aktuelt P7-grunnlag"),
                        ("pending", "Uverifisert / bestridt"),
                        ("none", "Ingen slike grunnlag"),
                    ],
                )
        else:
            self.fields.pop("ownership")


class OnboardingForm(ManagedRecordingCreationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Normal GUI onboarding links existing catalogue data; creation remains
        # available in the shared backend for import/system workflows.
        for name in (
            "new_recording_title",
            "new_isrc",
            "artist_identity",
            "force_create",
        ):
            self.fields.pop(name)
        self.fields["recording"].required = True
        self.fields["recording"].widget = forms.HiddenInput()
        value = (
            self.data.get("recording")
            if self.is_bound
            else self.initial.get("recording")
        )
        try:
            selected = (
                Recording.objects.filter(
                    pk=value, music_library_entry__isnull=False
                )
                if value
                else Recording.objects.none()
            )
            # Evaluating now also validates malformed UUIDs before rendering.
            ids = list(selected.values_list("pk", flat=True))
        except (ValueError, ValidationError):
            ids = []
        self.fields["recording"].queryset = Recording.objects.filter(
            pk__in=ids
        )
        self.fields["release_scope"].queryset = Release.objects.filter(
            tracks__recording_id__in=ids
        ).distinct()
        self.fields["release_scope"].empty_label = "Generelt for innspillingen"
        self.fields["source_system"].label = "Forvaltningskilde"
        self.fields["source_record"].label = (
            "Opprinnelig kildepost (provenance)"
        )
        self.fields["notes"].widget.attrs["rows"] = 3
        # SourceRecord can contain the entire archive: do not render all rows.
        self.fields["source_record"].widget = forms.TextInput()
        self.fields["source_record"].help_text = (
            "Valgfri UUID til en eksisterende kildepost. Opprinnelig provenance beholdes."
        )


class ReasonForm(forms.Form):
    reason = forms.CharField(
        label="Begrunnelse",
        strip=True,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    confirm = forms.BooleanField(
        label="Jeg har lest konsekvensene og bekrefter denne handlingen."
    )
