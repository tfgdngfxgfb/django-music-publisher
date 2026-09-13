from django.core.exceptions import ValidationError
from django.test import TestCase

from catalogue.models import Recording

from music_library.models import MusicLibraryEntry


class MusicLibraryTests(TestCase):
    def test_entry_can_exist_without_managed_music(self):
        recording = Recording.objects.create(title="Kun i arkivet")
        entry = MusicLibraryEntry.objects.create(
            recording=recording,
            genre="Pop",
            language="nb",
            target=MusicLibraryEntry.Target.FAMILY,
            channel="P7 Kristen Riksradio",
            gender=MusicLibraryEntry.Gender.MIXED,
            rating=4,
            energy=3,
        )
        self.assertFalse(hasattr(entry, "managed_recording"))

    def test_rating_and_energy_ranges_are_validated(self):
        recording = Recording.objects.create(title="Test")
        with self.assertRaises(ValidationError):
            MusicLibraryEntry.objects.create(recording=recording, rating=6)
