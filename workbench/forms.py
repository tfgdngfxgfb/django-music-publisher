from pathlib import Path

from django import forms
from django.conf import settings
from django.forms import formset_factory

from catalogue.forms import TrackCreationForm
from catalogue.models import (
    ExternalIdentifier,
    Recording,
    RecordingContribution,
    Release,
)
from catalogue.validators import normalize_isrc
from flac_ingest.adapter import normalize_energy
from media_assets.models import FileAsset, FileLocation
from music_library.models import (
    Channel,
    MusicLibraryChannel,
    MusicLibraryEntry,
    MusicLibraryTargetAudience,
    TargetAudience,
)
from music_library.validators import validate_radio_language
from parties.models import ArtistIdentity, Party
from provenance.models import SourceSystem
from rights.summaries import OwnershipCategory


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
    ownership = forms.ChoiceField(
        label="Eierskap",
        required=False,
        choices=(("", "Alt forvaltet"), *OwnershipCategory.choices),
    )
    local_administration = forms.ChoiceField(
        label="Administrasjon",
        required=False,
        choices=(
            ("", "Alle"),
            ("yes", "Administreres av lokal organisasjon"),
            ("no", "Administreres ikke av lokal organisasjon"),
        ),
    )
    local_distribution = forms.ChoiceField(
        label="Distribusjon",
        required=False,
        choices=(
            ("", "Alle"),
            ("yes", "Distribueres av lokal organisasjon"),
            ("no", "Distribueres ikke av lokal organisasjon"),
        ),
    )
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
    energy = forms.TypedChoiceField(
        label="Energy",
        required=False,
        coerce=int,
        empty_value=None,
        choices=(
            ("", "Ikke registrert"),
            *((str(value), f"{value} av 5") for value in range(1, 6)),
        ),
        help_text="P7s energinivå 1–5. Leses fra FLAC-taggen RATING.",
    )
    channels = forms.ModelMultipleChoiceField(
        Channel.objects.filter(is_active=True),
        label="Kanaler",
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "radio-choice-grid"}),
    )
    target_audiences = forms.ModelMultipleChoiceField(
        TargetAudience.objects.filter(is_active=True),
        label="Målgrupper",
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "radio-choice-grid"}),
    )

    class Meta:
        model = MusicLibraryEntry
        fields = (
            "genre",
            "language",
            "gender",
            "energy",
            "channels",
            "target_audiences",
            "verification_status",
            "notes",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["channels"].initial = self.instance.channels.all()
            self.fields["target_audiences"].initial = (
                self.instance.target_audiences.all()
            )

    def _save_m2m(self):
        """Persist canonical through rows without Django's bulk-create shortcut."""
        entry = self.instance
        MusicLibraryChannel.objects.filter(library_entry=entry).delete()
        for channel in self.cleaned_data["channels"]:
            MusicLibraryChannel.objects.create(library_entry=entry, channel=channel)
        MusicLibraryTargetAudience.objects.filter(library_entry=entry).delete()
        for target in self.cleaned_data["target_audiences"]:
            MusicLibraryTargetAudience.objects.create(
                library_entry=entry, target_audience=target
            )


class FlacScanForm(forms.Form):
    relative_root = forms.ChoiceField(label="Mappe i musikkarkivet", choices=())
    recursive = forms.BooleanField(
        label="Ta med undermapper", required=False, initial=True
    )
    allow_uuid_recovery = forms.BooleanField(
        label="Gjenopprett manglende innspillinger fra P7UUID",
        required=False,
        help_text=(
            "Administratoroverstyring for test eller kontrollert reimport. "
            "En gyldig P7UUID som ikke finnes i databasen brukes på den nye "
            "innspillingen. Identitetskonflikter blir fortsatt stoppet."
        ),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not user or not user.is_superuser:
            self.fields.pop("allow_uuid_recovery")
        configured = str(getattr(settings, "P7_MUSIC_ROOT", "") or "").strip()
        choices = []
        if configured:
            root = Path(configured).expanduser()
            if root.is_dir():
                choices.append((".", "Hele musikkarkivet"))
                choices.extend(
                    (path.name, path.name)
                    for path in sorted(
                        root.iterdir(), key=lambda item: item.name.casefold()
                    )
                    if path.is_dir()
                )
        self.fields["relative_root"].choices = choices
        if not choices:
            self.fields["relative_root"].help_text = (
                "P7_MUSIC_ROOT er ikke konfigurert eller finnes ikke."
            )

    def clean_relative_root(self):
        value = self.cleaned_data["relative_root"]
        if not self.fields["relative_root"].choices:
            raise forms.ValidationError("Musikkroten er ikke tilgjengelig.")
        return value


class FlacIngestReviewForm(forms.Form):
    resolution = forms.ChoiceField(label="Kobling til innspilling", choices=())
    title = forms.CharField(label="Tittel", max_length=500)
    artists = forms.CharField(
        label="Artisttekst",
        required=False,
        help_text="Skill flere navn med semikolon. Det opprettes ikke juridiske personer automatisk.",
    )
    isrc = forms.CharField(label="ISRC", max_length=32, required=False)
    p7uuid = forms.UUIDField(
        label="P7UUID",
        required=False,
        help_text="Kan korrigeres eller tømmes. Råverdien fra filen bevares uansett.",
    )
    genre = forms.CharField(label="Radiosjanger", max_length=100, required=False)
    language = forms.CharField(label="Radiospråk", max_length=64, required=False)
    energy = forms.TypedChoiceField(
        label="Energy",
        required=False,
        coerce=int,
        empty_value=None,
        choices=(
            ("", "Ikke registrert"),
            *((str(value), str(value)) for value in range(1, 6)),
        ),
    )
    channels = forms.CharField(
        label="Kanaler", required=False, help_text="Skill flere verdier med semikolon."
    )
    target_audiences = forms.CharField(
        label="Målgrupper",
        required=False,
        help_text="Skill flere verdier med semikolon.",
    )
    gender = forms.ChoiceField(
        label="Kjønn",
        required=False,
        choices=(("", "Ikke registrert"), *MusicLibraryEntry.Gender.choices),
    )
    review_note = forms.CharField(
        label="Kontrollmerknad",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def __init__(self, *args, item, **kwargs):
        super().__init__(*args, **kwargs)
        self.item = item
        choices = [("new", "Opprett en ny innspilling")]
        seen = set()
        if item.recording_id:
            value = f"recording:{item.recording_id}"
            choices.insert(0, (value, f"Bruk foreslått: {item.recording.title}"))
            seen.add(str(item.recording_id))
        for candidate in item.candidates:
            candidate_id = candidate.get("recording_uuid")
            if not candidate_id or candidate_id in seen:
                continue
            choices.append(
                (
                    f"recording:{candidate_id}",
                    f"Bruk eksisterende: {candidate.get('title') or candidate_id}",
                )
            )
            seen.add(candidate_id)
        self.fields["resolution"].choices = choices
        parsed = item.parsed_metadata
        energy = parsed.get("energy")
        if energy is None:
            energy = normalize_energy(parsed.get("energy_invalid"))
        self.initial.update(
            {
                "resolution": (
                    f"recording:{item.recording_id}" if item.recording_id else "new"
                ),
                "title": parsed.get("title", ""),
                "artists": "; ".join(parsed.get("artists", [])),
                "isrc": parsed.get("isrc", ""),
                "p7uuid": parsed.get("p7uuid", ""),
                "genre": parsed.get("genre", ""),
                "language": parsed.get("language", ""),
                "energy": energy,
                "channels": "; ".join(parsed.get("channels", [])),
                "target_audiences": "; ".join(parsed.get("target_audiences", [])),
                "gender": parsed.get("gender", ""),
            }
        )

    def clean_isrc(self):
        value = self.cleaned_data["isrc"].strip()
        if value:
            normalize_isrc(value)
        return value

    def clean_language(self):
        value = self.cleaned_data["language"].strip()
        if value:
            validate_radio_language(value)
        return value

    @staticmethod
    def _values(value):
        return [
            part.strip() for part in value.replace("\n", ";").split(";") if part.strip()
        ]

    def interpreted_metadata(self):
        parsed = dict(self.item.parsed_metadata)
        parsed["title"] = self.cleaned_data["title"].strip()
        optional_values = {
            "artists": self._values(self.cleaned_data["artists"]),
            "genre": self.cleaned_data["genre"].strip(),
            "language": self.cleaned_data["language"],
            "channels": self._values(self.cleaned_data["channels"]),
            "target_audiences": self._values(self.cleaned_data["target_audiences"]),
            "gender": self.cleaned_data["gender"],
        }
        for field, value in optional_values.items():
            if value:
                parsed[field] = value
            else:
                parsed.pop(field, None)
        for field in ("isrc", "p7uuid", "energy"):
            value = self.cleaned_data[field]
            if value in (None, ""):
                parsed.pop(field, None)
            else:
                parsed[field] = str(value) if field == "p7uuid" else value
        parsed.pop("energy_invalid", None)
        parsed.pop("gender_invalid", None)
        parsed.pop("p7uuid_invalid", None)
        return parsed


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
    duration_display = forms.CharField(
        label="Varighet",
        required=False,
        help_text="Minutter og sekunder, for eksempel 3:42.",
        widget=forms.TextInput(
            attrs={
                "placeholder": "3:42",
                "inputmode": "numeric",
                "data-track-field": "duration_display",
            }
        ),
    )
    existing_recording_search = forms.CharField(
        label="Søk etter eksisterende innspilling",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "recording-autocomplete",
                "autocomplete": "off",
                "placeholder": "Søk på tittel, artist eller ISRC",
                "data-track-field": "existing_recording_search",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["recording"].widget = forms.HiddenInput(
            attrs={"class": "recording-id"}
        )
        self.fields["duration_ms"].widget = forms.HiddenInput()
        field_attributes = {
            "disc_number": {"placeholder": "1", "data-track-field": "disc_number"},
            "side": {"placeholder": "A", "data-track-field": "side"},
            "track_number": {"placeholder": "1", "data-track-field": "track_number"},
            "sequence_number": {
                "placeholder": "Rekkefølge",
                "data-track-field": "sequence_number",
            },
            "title_override": {
                "placeholder": "Tittel slik den står på utgivelsen",
                "data-track-field": "title_override",
            },
            "artist_identity": {"data-track-field": "artist_identity"},
            "new_isrc": {"placeholder": "NO-XXX-26-00001", "data-track-field": "new_isrc"},
            "new_recording_title": {
                "placeholder": "Tittel på ny innspilling",
                "data-track-field": "new_recording_title",
            },
        }
        for name, attributes in field_attributes.items():
            self.fields[name].widget.attrs.update(attributes)
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
                "duration_display",
                "duration_ms",
                "force_create",
            )
        )

    def clean(self):
        value = (self.cleaned_data.get("duration_display") or "").strip()
        if value:
            try:
                if ":" in value:
                    minutes, seconds = value.split(":", 1)
                    if not minutes.isdigit() or not seconds.isdigit():
                        raise ValueError
                    seconds_value = int(seconds)
                    if seconds_value > 59:
                        raise ValueError
                    total_seconds = int(minutes) * 60 + seconds_value
                else:
                    total_seconds = int(value)
                if total_seconds < 0:
                    raise ValueError
            except ValueError:
                self.add_error(
                    "duration_display",
                    "Bruk minutter:sekunder, for eksempel 3:42.",
                )
            else:
                self.cleaned_data["duration_ms"] = total_seconds * 1000
        return super().clean()


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
