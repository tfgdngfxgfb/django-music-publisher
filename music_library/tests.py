from django.core.exceptions import ValidationError
from django.test import TestCase

from catalogue.models import Recording

from music_library.models import (
    Channel,
    MusicLibraryChannel,
    MusicLibraryEntry,
    MusicLibraryTargetAudience,
    TargetAudience,
)


class MusicLibraryTests(TestCase):
    def test_entry_can_exist_without_managed_music(self):
        recording = Recording.objects.create(title="Kun i arkivet")
        entry = MusicLibraryEntry.objects.create(
            recording=recording,
            genre="Pop",
            language="nb",
            gender=MusicLibraryEntry.Gender.MIXED,
            energy=4,
        )
        channel = Channel.objects.create(code="p7", name="P7 Kristen Riksradio")
        audience = TargetAudience.objects.create(code="familie", name="Familie")
        MusicLibraryChannel.objects.create(library_entry=entry, channel=channel)
        MusicLibraryTargetAudience.objects.create(
            library_entry=entry, target_audience=audience
        )
        self.assertFalse(hasattr(entry, "managed_recording"))

    def test_energy_range_is_validated(self):
        recording = Recording.objects.create(title="Test")
        with self.assertRaises(ValidationError):
            MusicLibraryEntry.objects.create(recording=recording, energy=6)
