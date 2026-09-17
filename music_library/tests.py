from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection
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
        channel = Channel.objects.create(
            code="p7", name="P7 Kristen Riksradio"
        )
        audience = TargetAudience.objects.create(
            code="familie", name="Familie"
        )
        MusicLibraryChannel.objects.create(
            library_entry=entry, channel=channel
        )
        MusicLibraryTargetAudience.objects.create(
            library_entry=entry, target_audience=audience
        )
        self.assertFalse(hasattr(entry, "managed_recording"))

    def test_energy_range_is_validated(self):
        recording = Recording.objects.create(title="Test")
        with self.assertRaises(ValidationError):
            MusicLibraryEntry.objects.create(recording=recording, energy=6)

    def test_onetagger_language_group_is_valid_radio_metadata(self):
        recording = Recording.objects.create(title="Afrikansk språkgruppe")

        entry = MusicLibraryEntry.objects.create(
            recording=recording,
            language="Afrikanske språk",
        )

        self.assertEqual(entry.language, "Afrikanske språk")

    def test_channel_rotation_backfill_preserves_explicit_assessments(self):
        channel = Channel.objects.create(code="p7", name="P7 Riks")
        entries = []
        for title, rotation, has_channel in (
            ("Kanal uten vurdering", "", True),
            ("Ikke rotasjonsverdig", "not_suitable", True),
            ("Ikke vurdert", "unassessed", True),
            ("Uten kanal", "", False),
        ):
            recording = Recording.objects.create(title=title)
            entry = MusicLibraryEntry.objects.create(
                recording=recording, rotation_suitability=rotation
            )
            if has_channel:
                MusicLibraryChannel.objects.create(
                    library_entry=entry, channel=channel
                )
            entries.append(entry)

        migration = import_module(
            "music_library.migrations.0008_backfill_rotation_from_channels"
        )
        migration.backfill_rotation_from_channels(
            apps, SimpleNamespace(connection=connection)
        )
        for entry, expected in zip(
            entries, ("suitable", "not_suitable", "unassessed", "")
        ):
            entry.refresh_from_db()
            self.assertEqual(entry.rotation_suitability, expected)
            self.assertEqual(
                entry.revision, 2 if expected == "suitable" else 1
            )
