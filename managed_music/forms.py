from django import forms

from catalogue.models import Recording
from catalogue.services import find_recording_candidates
from parties.models import ArtistIdentity
from provenance.models import SourceSystem

from .models import ManagedRecording


class ManagedRecordingCreationForm(forms.Form):
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
    force_create = forms.BooleanField(
        label="Opprett ny likevel",
        required=False,
        help_text="Bruk bare når forslagene er kontrollert og ikke er samme innspilling.",
    )
    status = forms.ChoiceField(
        label="Forvaltningsstatus", choices=ManagedRecording.Status.choices
    )
    source_system = forms.ModelChoiceField(
        SourceSystem.objects.all(), label="Kilde", required=False
    )
    notes = forms.CharField(label="Merknader", widget=forms.Textarea, required=False)

    def clean(self):
        data = super().clean()
        recording = data.get("recording")
        title = (data.get("new_recording_title") or "").strip()
        if bool(recording) == bool(title):
            raise forms.ValidationError(
                "Velg én eksisterende innspilling eller skriv inn tittel for én ny."
            )
        if (
            recording
            and hasattr(recording, "music_library_entry")
            and hasattr(recording.music_library_entry, "managed_recording")
        ):
            self.add_error(
                "recording", "Innspillingen finnes allerede i Forvaltet musikk."
            )
        if recording and data.get("new_isrc"):
            self.add_error("new_isrc", "Legg ISRC på den eksisterende innspillingen.")
        if title and not data.get("force_create"):
            matches = find_recording_candidates(
                title=title,
                isrc=data.get("new_isrc") or "",
                artist_identity=data.get("artist_identity"),
            )
            if matches:
                suggestions = "; ".join(
                    f"{match.recording.title} – {', '.join(match.signals)} – UUID {match.recording.pk}"
                    for match in matches[:5]
                )
                self.add_error(
                    "new_recording_title",
                    f"Mulig dublett: {suggestions}. Velg eksisterende eller marker «Opprett ny likevel».",
                )
        return data
