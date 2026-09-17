from django.test import TestCase

from catalogue.models import Recording

from media_assets.models import (
    FileAsset,
    FileLocation,
    RecordingMediaSelection,
)
from media_assets.selection import establish_current_radio_if_unambiguous


class CurrentRadioSelectionTests(TestCase):
    def _radio(self, recording, name):
        asset = FileAsset.objects.create(
            recording=recording,
            filename=name,
            mime_type="audio/flac",
            role=FileAsset.Role.RADIO_FLAC,
        )
        FileLocation.objects.create(
            asset=asset,
            storage_type=FileLocation.StorageType.NAS,
            relative_path=name,
            status=FileLocation.Status.ACTIVE,
            is_current=True,
        )
        return asset

    def test_promotes_one_unambiguous_radio_file(self):
        recording = Recording.objects.create(title="Entydig")
        asset = self._radio(recording, "one.flac")

        selected, changed = establish_current_radio_if_unambiguous(
            recording.pk
        )

        asset.refresh_from_db()
        selection = RecordingMediaSelection.objects.get(recording=recording)
        self.assertTrue(changed)
        self.assertEqual(selected, asset)
        self.assertEqual(
            asset.lifecycle_status, FileAsset.LifecycleStatus.CURRENT
        )
        self.assertEqual(selection.current_radio_id, asset.pk)

    def test_does_not_choose_between_multiple_radio_files(self):
        recording = Recording.objects.create(title="Tvetydig")
        first = self._radio(recording, "one.flac")
        second = self._radio(recording, "two.flac")

        selected, changed = establish_current_radio_if_unambiguous(
            recording.pk
        )

        self.assertIsNone(selected)
        self.assertFalse(changed)
        self.assertIsNone(
            RecordingMediaSelection.objects.get(
                recording=recording
            ).current_radio_id
        )
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(
            {first.lifecycle_status, second.lifecycle_status},
            {FileAsset.LifecycleStatus.UNCLASSIFIED},
        )

    def test_never_replaces_an_existing_current_radio(self):
        recording = Recording.objects.create(title="Har valg")
        current = self._radio(recording, "current.flac")
        current.lifecycle_status = FileAsset.LifecycleStatus.CURRENT
        current.save(update_fields=("lifecycle_status",))
        RecordingMediaSelection.objects.create(
            recording=recording,
            current_radio=current,
        )
        self._radio(recording, "new.flac")

        selected, changed = establish_current_radio_if_unambiguous(
            recording.pk
        )

        self.assertFalse(changed)
        self.assertEqual(selected, current)
        self.assertEqual(
            RecordingMediaSelection.objects.get(
                recording=recording
            ).current_radio_id,
            current.pk,
        )
