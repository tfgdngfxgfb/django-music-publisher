from django import forms

from parties.models import ArtistIdentity

from .models import Recording
from .services import find_recording_candidates


class TrackCreationForm(forms.Form):
    recording = forms.ModelChoiceField(
        Recording.objects.all(), label="Bruk eksisterende innspilling", required=False
    )
    new_recording_title = forms.CharField(
        label="Tittel på ny innspilling", max_length=500, required=False
    )
    new_isrc = forms.CharField(label="ISRC", max_length=30, required=False)
    artist_identity = forms.ModelChoiceField(
        ArtistIdentity.objects.all(), label="Artistidentitet", required=False
    )
    disc_number = forms.IntegerField(label="Disc/medium", min_value=1, required=False)
    side = forms.CharField(label="Side", max_length=10, required=False)
    track_number = forms.IntegerField(label="Spornummer", min_value=1, required=False)
    sequence_number = forms.IntegerField(label="Sorteringsrekkefølge", min_value=1)
    title_override = forms.CharField(
        label="Utgivelsesspesifikk tittel", max_length=500, required=False
    )
    duration_ms = forms.IntegerField(
        label="Varighet (millisekunder)", min_value=0, required=False
    )
    force_create = forms.BooleanField(
        label="Opprett ny likevel",
        required=False,
        help_text="Bruk bare når forslagene er kontrollert og ikke er samme innspilling.",
    )

    def clean(self):
        data = super().clean()
        self.duplicate_candidates = ()
        recording = data.get("recording")
        title = (data.get("new_recording_title") or "").strip()
        if bool(recording) == bool(title):
            raise forms.ValidationError(
                "Velg én eksisterende innspilling eller skriv inn tittel for én ny."
            )
        if recording and data.get("new_isrc"):
            self.add_error("new_isrc", "Legg ISRC på den eksisterende innspillingen.")
        if title and not data.get("force_create"):
            matches = find_recording_candidates(
                title=title,
                isrc=data.get("new_isrc") or "",
                duration_ms=data.get("duration_ms"),
                artist_identity=data.get("artist_identity"),
            )
            if matches:
                self.duplicate_candidates = tuple(matches[:5])
                suggestions = "; ".join(
                    f"{match.recording.title} – {', '.join(match.signals)} – UUID {match.recording.pk}"
                    for match in matches[:5]
                )
                self.add_error(
                    "new_recording_title",
                    f"Mulig dublett: {suggestions}. Velg eksisterende eller marker «Opprett ny likevel».",
                )
        return data
