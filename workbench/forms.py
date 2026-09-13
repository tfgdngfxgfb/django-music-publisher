from django import forms
from django.forms import formset_factory

from catalogue.forms import TrackCreationForm
from catalogue.models import (
    ExternalIdentifier,
    Recording,
    RecordingContribution,
    Release,
)
from media_assets.models import FileAsset, FileLocation
from music_library.models import MusicLibraryEntry
from parties.models import ArtistIdentity, Party
from provenance.models import SourceSystem


class SearchForm(forms.Form):
    q = forms.CharField(label="Søk", required=False)


class MusicLibraryFilterForm(SearchForm):
    genre = forms.CharField(label="Sjanger", required=False)
    language = forms.CharField(label="Språk", required=False)
    order = forms.ChoiceField(
        label="Sortering",
        required=False,
        choices=(
            ("title", "Tittel A–Å"),
            ("-title", "Tittel Å–A"),
            ("recent", "Sist endret"),
        ),
    )
    status = forms.ChoiceField(
        label="Verifikasjon",
        required=False,
        choices=(
            ("", "Alle statuser"),
            *MusicLibraryEntry._meta.get_field("verification_status").choices,
        ),
    )
    managed = forms.ChoiceField(
        label="Forvaltning",
        required=False,
        choices=(
            ("", "Alle"),
            ("yes", "Registrert i Forvaltet musikk"),
            ("no", "Ikke forvaltet"),
        ),
    )
    source = forms.ModelChoiceField(
        SourceSystem.objects.all(), label="Kilde", required=False
    )


class ManagedFilterForm(SearchForm):
    status = forms.ChoiceField(
        label="Status",
        required=False,
        choices=(
            ("", "Alle statuser"),
            ("pending", "Til vurdering"),
            ("active", "Aktiv forvaltning"),
            ("inactive", "Ikke aktiv"),
        ),
    )
    source = forms.ModelChoiceField(
        SourceSystem.objects.all(), label="Kilde", required=False
    )


class ReleaseFilterForm(SearchForm):
    release_type = forms.ChoiceField(
        label="Type",
        required=False,
        choices=(("", "Alle typer"), *Release.Type.choices),
    )
    status = forms.ChoiceField(
        label="Verifikasjon",
        required=False,
        choices=(
            ("", "Alle statuser"),
            *Release._meta.get_field("verification_status").choices,
        ),
    )
    source = forms.ModelChoiceField(
        SourceSystem.objects.all(), label="Kilde", required=False
    )


class RecordingForm(forms.ModelForm):
    class Meta:
        model = Recording
        fields = (
            "title",
            "version_designation",
            "recording_kind",
            "duration_ms",
            "language",
            "metadata_status",
        )


class RadioMetadataForm(forms.ModelForm):
    class Meta:
        model = MusicLibraryEntry
        fields = (
            "genre",
            "language",
            "target",
            "channel",
            "gender",
            "rating",
            "energy",
            "verification_status",
            "notes",
        )


class ReleaseForm(forms.ModelForm):
    class Meta:
        model = Release
        fields = (
            "title",
            "release_type",
            "release_date",
            "release_year",
            "label",
            "catalogue_number",
            "verification_status",
            "notes",
        )
        widgets = {"release_date": forms.DateInput(attrs={"type": "date"})}


class PartyForm(forms.ModelForm):
    class Meta:
        model = Party
        fields = ("name", "kind")


class ArtistIdentityForm(forms.ModelForm):
    class Meta:
        model = ArtistIdentity
        fields = ("display_name", "party")


class FileAssetForm(forms.ModelForm):
    class Meta:
        model = FileAsset
        fields = (
            "recording",
            "release",
            "filename",
            "mime_type",
            "size_bytes",
            "sha256",
            "role",
            "technical_metadata",
        )


class FileLocationForm(forms.ModelForm):
    class Meta:
        model = FileLocation
        fields = (
            "storage_type",
            "relative_path",
            "status",
            "is_current",
            "verification_status",
            "ended_at",
            "google_drive_id",
            "google_drive_url",
        )
        widgets = {"ended_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}


class LibraryMembershipForm(forms.Form):
    recording = forms.ModelChoiceField(
        Recording.objects.all(), label="Innspilling som skal legges til"
    )


class WorkbenchTrackCreationForm(TrackCreationForm):
    existing_recording_search = forms.CharField(
        label="Søk etter eksisterende innspilling",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "recording-autocomplete",
                "autocomplete": "off",
                "placeholder": "Søk på tittel, artist eller ISRC",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["recording"].widget = forms.HiddenInput(
            attrs={"class": "recording-id"}
        )
        self.order_fields(
            (
                "existing_recording_search",
                "recording",
                "new_recording_title",
                "new_isrc",
                "artist_identity",
                "disc_number",
                "side",
                "track_number",
                "sequence_number",
                "title_override",
                "duration_ms",
                "force_create",
            )
        )


class RecordingIdentifierForm(forms.ModelForm):
    class Meta:
        model = ExternalIdentifier
        fields = ("scheme", "namespace", "value")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["scheme"].choices = (
            (ExternalIdentifier.Scheme.ISRC, "ISRC"),
            (ExternalIdentifier.Scheme.EXTERNAL, "Ekstern system-ID"),
        )


class ReleaseIdentifierForm(forms.ModelForm):
    class Meta:
        model = ExternalIdentifier
        fields = ("scheme", "namespace", "value")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["scheme"].choices = tuple(
            choice
            for choice in ExternalIdentifier.Scheme.choices
            if choice[0] != ExternalIdentifier.Scheme.ISRC
        )


class ContributionForm(forms.ModelForm):
    class Meta:
        model = RecordingContribution
        fields = ("party", "role", "artist_identity", "credited_as", "display_order")


class AssertionActionForm(forms.Form):
    action = forms.ChoiceField(
        choices=(
            ("confirm", "Bekreft opplysningen"),
            ("apply", "Bruk som gjeldende verdi"),
            ("confirm_apply", "Bekreft og bruk"),
            ("dispute", "Bestrid"),
            ("reject", "Avvis"),
            ("correct", "Korriger med ny kildeverdi"),
        ),
        widget=forms.HiddenInput,
    )
    expected_revision = forms.IntegerField(widget=forms.HiddenInput, required=False)
    correction = forms.CharField(
        label="Korrigert kildeverdi",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    note = forms.CharField(
        label="Begrunnelse", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )

    def clean(self):
        data = super().clean()
        if (
            data.get("action") == "correct"
            and not (data.get("correction") or "").strip()
        ):
            self.add_error("correction", "Skriv inn den korrigerte kildeverdien.")
        return data


TrackFormSet = formset_factory(WorkbenchTrackCreationForm, extra=5, max_num=25)
