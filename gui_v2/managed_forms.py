"""GUI adapters; management validation and mutations remain in 6D/6E."""

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from catalogue.models import Recording, Release
from managed_music.forms import ManagedRecordingCreationForm
from provenance.models import MetadataAssertion, SourceRecord, SourceSystem
from rights.scope import OwnershipCategory
from gui_v2.managed_music import STATUS_LABELS


def existing_catalogue_source(recording):
    """Earliest explicitly linked source, never inferred from names or paths.

    A UI default for origin, not evidence that a master right is confirmed.
    Newer rescans must not silently replace the original source selection.
    """
    assertions = MetadataAssertion.objects.filter(
        Q(
            entity_type=MetadataAssertion.EntityType.RECORDING,
            entity_uuid=recording.pk,
        )
        | Q(
            entity_type=MetadataAssertion.EntityType.MUSIC_LIBRARY_ENTRY,
            entity_uuid=recording.music_library_entry.pk,
        )
    )
    return (
        SourceRecord.objects.filter(
            Q(pk__in=assertions.values("source_record_id"))
            | Q(recording_contributions__recording=recording)
            | Q(
                flac_ingest_item__recording=recording,
                flac_ingest_item__applied_at__isnull=False,
            )
        )
        .select_related("source_system")
        .distinct()
        .order_by("created_at", "pk")
        .first()
    )


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
        chosen = (
            self.fields["recording"]
            .queryset.select_related("music_library_entry")
            .first()
        )
        self.default_source = (
            existing_catalogue_source(chosen) if chosen else None
        )
        if self.default_source:
            self.initial.setdefault("source_record", self.default_source.pk)
            self.initial.setdefault(
                "source_system", self.default_source.source_system_id
            )
            if self.is_bound:
                self.data = self.data.copy()
                if not self.data.get("source_record", "").strip():
                    self.data["source_record"] = str(self.default_source.pk)
                if not self.data.get("source_system", "").strip():
                    self.data["source_system"] = str(
                        self.default_source.source_system_id
                    )
        self.initial.setdefault("ownership_share", 100)
        self.fields["ownership_share"].help_text = (
            "Forhåndsutfylt 100 %. Endre andelen dersom P7 eier mindre. Brukes bare ved mastereierskap."
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
            "Eksisterende provenance gjenbrukes automatisk når den finnes. Du kan angi UUID til en annen eksisterende kildepost. Originalen bevares."
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
