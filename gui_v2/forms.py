from django import forms
from django.forms import formset_factory

from catalogue.models import ExternalIdentifier, Recording, Release
from catalogue.services import find_recording_candidates
from catalogue.validators import normalize_isrc, normalize_trade_item_number
from music_library.models import Channel, MusicLibraryEntry, TargetAudience

from .presentation import observed_genres, radio_language_name

from django.conf import settings
from media_assets.storage import MUSIC_LIBRARY_ROOT


class MasterRegistrationForm(forms.Form):
    root_key = forms.ChoiceField(label="Lagringsrot", choices=())
    relative_path = forms.CharField(
        label="Relativ filsti",
        max_length=1000,
        help_text="Stien må ligge innenfor valgt og konfigurert storage-root.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = []
        if str(getattr(settings, "P7_MUSIC_ROOT", "") or "").strip():
            choices.append((MUSIC_LIBRARY_ROOT, "Musikkarkiv"))
        for key in getattr(settings, "P7_STORAGE_ROOTS", {}) or {}:
            if key != MUSIC_LIBRARY_ROOT:
                choices.append((key, key.replace("_", " ").title()))
        self.fields["root_key"].choices = choices


class MusicLibraryFilterForm(forms.Form):
    MATCH_CHOICES = (("any", "Minst én valgt"), ("all", "Alle valgte"))

    q = forms.CharField(required=False, label="Søk")
    genre = forms.ChoiceField(
        required=False, choices=(("", "Alle"),), label="Radiosjanger"
    )
    language = forms.ChoiceField(
        required=False, choices=(("", "Alle"),), label="Radiospråk"
    )
    energy = forms.TypedChoiceField(
        required=False,
        coerce=int,
        empty_value=None,
        choices=(
            ("", "Alle"),
            *((value, str(value)) for value in range(1, 6)),
        ),
        label="Energy",
    )
    gender = forms.ChoiceField(
        required=False,
        choices=(("", "Alle"), *MusicLibraryEntry.Gender.choices),
        label="Vokalklassifisering",
    )
    rotation_suitability = forms.ChoiceField(
        required=False,
        choices=(
            ("", "Alle"),
            *MusicLibraryEntry.RotationSuitability.choices,
        ),
        label="Rotasjonsvurdering",
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
        choices=(
            ("", "Alle"),
            ("yes", "Forvaltet"),
            ("release_only", "Kun via forvaltet utgivelse"),
            ("no", "Ikke forvaltet"),
        ),
        label="Forvaltning",
    )
    follow_up = forms.ChoiceField(
        required=False,
        choices=(
            ("", "Alle"),
            ("yes", "Krever oppfølging"),
            ("no", "Ingen kjent oppfølging"),
        ),
        label="Oppfølging",
    )
    isrc_file_collision = forms.BooleanField(
        required=False,
        label="Flere radio-FLAC med samme ISRC",
    )
    ordering = forms.ChoiceField(
        required=False,
        choices=(
            ("title", "Tittel A–Å"),
            ("-title", "Tittel Å–A"),
            ("artist", "Artist A–Å"),
            ("-artist", "Artist Å–A"),
            ("isrc", "ISRC stigende"),
            ("-isrc", "ISRC synkende"),
            ("duration", "Kortest varighet"),
            ("-duration", "Lengst varighet"),
            ("genre", "Sjanger A–Å"),
            ("-genre", "Sjanger Å–A"),
            ("language", "Språk A–Å"),
            ("-language", "Språk Å–A"),
            ("energy", "Lavest Energy"),
            ("-energy", "Høyest Energy"),
            ("rotation", "Rotasjon stigende"),
            ("-rotation", "Rotasjon synkende"),
            ("channels", "Kanal A–Å"),
            ("-channels", "Kanal Å–A"),
            ("targets", "Målgruppe A–Å"),
            ("-targets", "Målgruppe Å–A"),
            ("file_status", "Filstatus stigende"),
            ("-file_status", "Filstatus synkende"),
            ("managed", "Ikke forvaltet først"),
            ("-managed", "Forvaltet først"),
            ("follow_up", "Uten oppfølging først"),
            ("-follow_up", "Oppfølging først"),
            ("-updated", "Sist endret først"),
            ("updated", "Eldst endret først"),
        ),
        initial="title",
        label="Sortering",
    )
    per_page = forms.ChoiceField(
        required=False,
        choices=(
            ("40", "40"),
            ("100", "100"),
            ("250", "250"),
            ("500", "500"),
            ("all", "Alle"),
        ),
        initial="40",
        label="Rader per side",
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
        required=False,
        choices=MATCH_CHOICES,
        initial="any",
        label="Målgruppevalg",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["q"].widget.attrs.update(
            {
                "placeholder": "Tittel, artist eller ISRC",
                "data-library-search": "",
                "autocomplete": "off",
            }
        )
        genres = observed_genres(
            MusicLibraryEntry.objects.exclude(genre="")
            .order_by()
            .values_list("genre", flat=True)
            .distinct()
        )
        languages = list(
            MusicLibraryEntry.objects.exclude(language="")
            .order_by()
            .values_list("language", flat=True)
            .distinct()
        )
        self.fields["genre"].choices = (
            ("", "Alle"),
            *((value, value) for value in genres),
        )
        self.fields["language"].choices = (
            ("", "Alle"),
            *(
                (value, radio_language_name(value))
                for value in sorted(
                    languages,
                    key=lambda item: radio_language_name(item).casefold(),
                )
            ),
        )


class ReleaseMetadataForm(forms.ModelForm):
    barcode = forms.CharField(
        required=False,
        max_length=30,
        label="Strekkode (UPC/EAN/GTIN)",
        help_text="8, 12, 13 eller 14 sifre med gyldig kontrollsiffer.",
    )

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
        widgets = {
            "release_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.is_bound:
            identifier = self.instance.identifiers.filter(
                scheme__in=(
                    ExternalIdentifier.Scheme.UPC,
                    ExternalIdentifier.Scheme.EAN,
                    ExternalIdentifier.Scheme.GTIN,
                )
            ).first()
            self.initial["barcode"] = identifier.value if identifier else ""

    def clean_barcode(self):
        value = (self.cleaned_data.get("barcode") or "").strip()
        if not value:
            self.barcode_scheme = None
            self.normalized_barcode = ""
            return ""
        digits = value.replace("-", "").replace(" ", "")
        scheme = {
            8: ExternalIdentifier.Scheme.EAN,
            12: ExternalIdentifier.Scheme.UPC,
            13: ExternalIdentifier.Scheme.EAN,
            14: ExternalIdentifier.Scheme.GTIN,
        }.get(len(digits))
        if not scheme:
            raise forms.ValidationError(
                "Strekkoden må ha 8, 12, 13 eller 14 sifre."
            )
        normalized = normalize_trade_item_number(digits, scheme)
        conflict_query = ExternalIdentifier.objects.filter(
            scheme=scheme, namespace="", normalized_value=normalized
        )
        if self.instance.pk:
            conflict_query = conflict_query.exclude(release=self.instance)
        conflict = conflict_query.exists()
        if conflict:
            raise forms.ValidationError(
                "Strekkoden er allerede knyttet til en annen utgivelse."
            )
        self.barcode_scheme = scheme
        self.normalized_barcode = normalized
        return value

    def save_barcode(self):
        identifiers = self.instance.identifiers.filter(
            scheme__in=(
                ExternalIdentifier.Scheme.UPC,
                ExternalIdentifier.Scheme.EAN,
                ExternalIdentifier.Scheme.GTIN,
            )
        )
        if not self.cleaned_data.get("barcode"):
            identifiers.delete()
            return
        identifier = identifiers.filter(scheme=self.barcode_scheme).first()
        if identifier is None:
            identifier = identifiers.first() or ExternalIdentifier(
                release=self.instance
            )
        identifier.scheme = self.barcode_scheme
        identifier.value = self.cleaned_data["barcode"]
        identifier.full_clean()
        identifier.save()
        identifiers.exclude(pk=identifier.pk).delete()


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
    disc_number = forms.IntegerField(
        min_value=1, required=False, label="Plate"
    )
    side = forms.CharField(max_length=10, required=False, label="Side")
    track_number = forms.IntegerField(
        min_value=1, required=False, label="Spor"
    )
    title_override = forms.CharField(
        max_length=500, required=False, label="Sportittel"
    )
    recording_title = forms.CharField(
        max_length=500, required=False, label="Innspilling"
    )
    artists = forms.CharField(
        max_length=1000, required=False, label="Artister"
    )
    composers = forms.CharField(
        max_length=1000, required=False, label="Komponister"
    )
    lyricists = forms.CharField(
        max_length=1000, required=False, label="Tekstforfattere"
    )
    arrangers = forms.CharField(
        max_length=1000, required=False, label="Arrangører"
    )
    duration = forms.CharField(max_length=12, required=False, label="Varighet")
    isrc = forms.CharField(max_length=30, required=False, label="ISRC")
    force_create = forms.BooleanField(required=False, widget=forms.HiddenInput)
    update_shared_recording = forms.BooleanField(
        required=False, label="Oppdater den felles innspillingen"
    )
    remove = forms.BooleanField(required=False, label="Fjern spor")

    def __init__(self, *args, release=None, **kwargs):
        self.release = release
        self.duplicate_candidates = ()
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs.setdefault("aria-label", field.label)

    def clean_recording_id(self):
        value = self.cleaned_data.get("recording_id")
        if value and not Recording.objects.filter(pk=value).exists():
            raise forms.ValidationError(
                "Den valgte innspillingen finnes ikke."
            )
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
            self.add_error(
                "recording_title",
                "Velg en innspilling eller skriv inn en ny tittel.",
            )
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
