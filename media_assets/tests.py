from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from catalogue.models import Recording

from media_assets.models import FileAsset, FileLocation


class FileAssetTests(TestCase):
    def test_asset_identity_is_independent_of_locations(self):
        asset = FileAsset.objects.create(
            filename="track01.flac", role=FileAsset.Role.RADIO_FLAC
        )
        self.assertEqual(asset.locations.count(), 0)
        first = FileLocation.objects.create(
            asset=asset,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="Mudi/Lynor/LYNOR123/Audio/track01.flac",
        )
        first.status = FileLocation.Status.MOVED
        first.is_current = False
        first.save()
        current = FileLocation.objects.create(
            asset=asset,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="Arkiv/Lynor/LYNOR123/track01.flac",
        )
        self.assertEqual(asset.locations.count(), 2)
        self.assertFalse(first.is_current)
        self.assertTrue(current.is_current)

    @override_settings(P7_NAS_ROOT="D:/P7-Musikk")
    def test_nas_root_is_configuration_not_persisted_identity(self):
        asset = FileAsset.objects.create(
            filename="track.wav", role=FileAsset.Role.EDITED_WAV_MASTER
        )
        location = FileLocation.objects.create(
            asset=asset,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="Lynor/track.wav",
        )
        self.assertEqual(location.relative_path, "Lynor/track.wav")
        self.assertEqual(
            str(location.resolved_nas_path()).replace("\\", "/"),
            "D:/P7-Musikk/Lynor/track.wav",
        )

    def test_absolute_or_parent_path_is_rejected(self):
        asset = FileAsset.objects.create(
            filename="track.wav", role=FileAsset.Role.OTHER
        )
        for path in ("C:\\Music\\track.wav", "/music/track.wav", "../track.wav"):
            with self.subTest(path=path), self.assertRaises(ValidationError):
                FileLocation.objects.create(
                    asset=asset,
                    storage_type=FileLocation.StorageType.NAS,
                    relative_path=path,
                )

    def test_file_does_not_imply_ownership(self):
        recording = Recording.objects.create(title="Master")
        asset = FileAsset.objects.create(
            recording=recording,
            filename="master.wav",
            role=FileAsset.Role.EDITED_WAV_MASTER,
        )
        self.assertEqual(asset.recording, recording)
        self.assertFalse(hasattr(asset, "owner"))
