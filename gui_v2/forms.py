from django import forms
from django.forms import formset_factory

from catalogue.models import Recording
from catalogue.services import find_recording_candidates
from catalogue.validators import normalize_isrc
from music_library.models import Channel, MusicLibraryEntry, TargetAudience


class MusicLibraryFilterForm(forms.Form):
    MATCH_CHOICES = (("any", "Minst én valgt"), ("all", "Alle valgte"))

    q = forms.CharField(required=False, label="Søk")
    genre = forms.CharField(required=False, label="Radiosjanger")
    language = forms.CharField(required=False, label="Radiospråk")
    energy = forms.TypedChoiceField(
        required=False,
        coerce=int,
        empty_value=None,
        choices=(("", "Alle"), *((value, str(value)) for value in range(1, 6))),
        label="Energy",
    )
    gender = forms.ChoiceField(
        required=False,
        choices=(("", "Alle"), *MusicLibraryEntry.Gender.choices),
        label="Vokalklassifisering",
    )
    file_status = forms.ChoiceField(
        required=False,
        choices=(
            ("", "Alle"),
            ("available", "Fil registrert"),
            ("missing", "Fil mangler"),
            ("problem", "Filproblem"),
            ("none", "Ingen radiofil"),
        ),
        label="Filstatus",
    )
    managed = forms.ChoiceField(
        required=False,
        choices=(("", "Alle"), ("yes", "Forvaltet"), ("no", "Ikke forvaltet")),
        label="Forvaltning",
    )
    channels = forms.ModelMultipleChoiceField(
        required=False,
        queryset=Channel.objects.filter(is_active=True),
        widget=forms.CheckboxSelectMultiple,
        label="Kanaler",
    )
    channel_mode = forms.ChoiceField(
        required=False, choices=MATCH_CHOICES, initial="any", label="Kanalvalg"
    )
    target_audiences = forms.ModelMultipleChoiceField(
        required=False,
        queryset=TargetAudience.objects.filter(is_active=True),
        widget=forms.CheckboxSelectMultiple,
        label="Målgrupper",
    )
    target_mode = forms.ChoiceField(
        required=False, choices=MATCH_CHOICES, initial="any", label="Målgruppevalg"
    )


def _parse_duration(value):
    value = (value or "").strip()
    if not value:
        return None
    if value.isdigit():
        return int(value) * 1000
    parts = value.split(":")
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        minutes, seconds = (int(part) for part in parts)
        if seconds < 60:
            return (minutes * 60 + seconds) * 1000
    raise forms.ValidationError("Bruk mm:ss, for eksempel 03:42.")


class TrackRowForm(forms.Form):
    track_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    recording_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    sequence_number = forms.IntegerField(min_value=1, label="Rekkefølge")
    disc_number = forms.IntegerField(min_value=1, required=False, label="Plate")
    side = forms.CharField(max_length=10, required=False, label="Side")
    track_number = forms.IntegerField(min_value=1, required=False, label="Spor")
    title_override = forms.CharField(max_length=500, required=False, label="Sportittel")
    recording_title = forms.CharField(max_length=500, required=False, label="Innspilling")
    artists = forms.CharField(max_length=1000, required=False, label="Artister")
    composers = forms.CharField(max_length=1000, required=False, label="Komponister")
    lyricists = forms.CharField(max_length=1000, required=False, label="Tekstforfattere")
    duration = forms.CharField(max_length=12, required=False, label="Varighet")
    isrc = forms.CharField(max_length=30, required=False, label="ISRC")
    force_create = forms.BooleanField(required=False, label="Opprett ny likevel")
    update_shared_recording = forms.BooleanField(
        required=False, label="Oppdater den felles innspillingen"
    )
    remove = forms.BooleanField(required=False, label="Fjern spor")

    def __init__(self, *args, release=None, **kwargs):
        self.release = release
        self.duplicate_candidates = ()
        super().__init__(*args, **kwargs)

    def clean_recording_id(self):
        value = self.cleaned_data.get("recording_id")
        if value and not Recording.objects.filter(pk=value).exists():
            raise forms.ValidationError("Den valgte innspillingen finnes ikke.")
        return value

    def clean_isrc(self):
        value = (self.cleaned_data.get("isrc") or "").strip()
        return normalize_isrc(value) if value else ""

    def clean_duration(self):
        value = self.cleaned_data.get("duration") or ""
        self.cleaned_data["duration_ms"] = _parse_duration(value)
        return value

    def clean(self):
        data = super().clean()
        if data.get("remove") and data.get("track_id"):
            return data
        title = (data.get("recording_title") or "").strip()
        recording_id = data.get("recording_id")
        if not recording_id and not title:
            self.add_error("recording_title", "Velg en innspilling eller skriv inn en ny tittel.")
            return data
        data["duration_ms"] = self.cleaned_data.get("duration_ms")
        if not recording_id and title and not data.get("force_create"):
            self.duplicate_candidates = tuple(
                find_recording_candidates(
                    title=title,
                    isrc=data.get("isrc") or "",
                    duration_ms=data.get("duration_ms"),
                )[:5]
            )
            if self.duplicate_candidates:
                self.add_error(
                    "recording_title",
                    "Mulig dublett. Velg et forslag eller marker «Opprett ny likevel».",
                )
        return data


TrackRowFormSet = formset_factory(TrackRowForm, extra=0)
