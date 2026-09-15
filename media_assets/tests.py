import tempfile
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.test import TestCase, override_settings

from catalogue.models import Recording

from media_assets.models import FileAsset, FileLocation
from media_assets.storage import (
    ResolvedLocation,
    StorageFileUnavailable,
    StoragePathError,
    StorageRoot,
    get_client_folder,
    get_client_path,
    get_storage_root,
    location_exists,
    location_is_readable,
    open_for_read,
    resolve_location,
    resolve_storage_path,
    stat_location,
)


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

    @override_settings(P7_NAS_ROOT="D:/P7-Musikk", P7_MUSIC_ROOT="D:/P7-Musikk")
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


class ReadOnlyStorageTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "Artist" / "Album" / "track.flac"
        self.path.parent.mkdir(parents=True)
        self.payload = b"fLaC-read-only-storage"
        self.path.write_bytes(self.payload)
        self.asset = FileAsset.objects.create(
            filename="track.flac", role=FileAsset.Role.RADIO_FLAC
        )
        self.location = FileLocation.objects.create(
            asset=self.asset,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="Artist/Album/track.flac",
        )

    def _settings(self, **extra):
        values = {
            "P7_MUSIC_ROOT": str(self.root),
            "P7_NAS_ROOT": str(self.root),
            "P7_MUSIC_CLIENT_ROOT": "",
        }
        values.update(extra)
        return override_settings(**values)

    def test_resolves_registered_location_to_canonical_server_path(self):
        with self._settings():
            resolved = resolve_location(self.location, require_root=True)
            direct = resolve_storage_path("Artist/Album/track.flac")
        self.assertEqual(resolved.root.key, "music_library")
        self.assertEqual(resolved.server_path, self.path.resolve())
        self.assertEqual(direct.server_path, self.path.resolve())

    def test_missing_file_is_reported_without_mutating_location(self):
        self.path.unlink()
        original_status = self.location.status
        with self._settings():
            self.assertFalse(location_exists(self.location))
            self.assertFalse(location_is_readable(self.location))
            with self.assertRaises(StorageFileUnavailable):
                open_for_read(self.location)
        self.location.refresh_from_db()
        self.assertEqual(self.location.status, original_status)

    def test_traversal_and_absolute_paths_are_rejected(self):
        with self._settings():
            for value in ("../outside.flac", "/outside.flac", "C:/outside.flac"):
                with self.subTest(value=value), self.assertRaises(StoragePathError):
                    resolve_storage_path(value)

    def test_canonical_path_cannot_escape_through_symlink_when_supported(self):
        outside = self.root.parent / f"{self.root.name}-outside"
        outside.mkdir(exist_ok=True)
        self.addCleanup(lambda: outside.rmdir() if outside.exists() else None)
        link = self.root / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink-oppretting er ikke tilgjengelig i dette miljøet.")
        with self._settings(), self.assertRaises(StoragePathError):
            resolve_storage_path("escape/secret.flac")

    def test_canonical_result_outside_root_is_rejected(self):
        outside = self.root.parent / "outside.flac"
        with self._settings(), patch(
            "media_assets.storage.Path.resolve",
            side_effect=[self.root, outside],
        ), self.assertRaises(StoragePathError):
            resolve_storage_path("apparently-safe.flac")

    def test_client_path_is_distinct_and_optional(self):
        client_root = r"\\P7-CLIENT\Music"
        with self._settings(P7_MUSIC_CLIENT_ROOT=client_root):
            resolved = resolve_location(self.location)
            self.assertEqual(
                resolved.client_path,
                r"\\P7-CLIENT\Music\Artist\Album\track.flac",
            )
            self.assertEqual(get_client_path(self.location), resolved.client_path)
            self.assertEqual(
                get_client_folder(self.location),
                r"\\P7-CLIENT\Music\Artist\Album",
            )
            self.assertNotEqual(str(resolved.server_path), resolved.client_path)
        with self._settings():
            self.assertIsNone(get_client_path(self.location))
            self.assertIsNone(get_client_folder(self.location))

    def test_stat_and_open_are_read_only(self):
        before = self.path.stat()
        with self._settings():
            self.assertEqual(stat_location(self.location).st_size, len(self.payload))
            self.assertTrue(location_exists(self.location))
            self.assertTrue(location_is_readable(self.location))
            with open_for_read(self.location) as handle:
                self.assertEqual(handle.read(), self.payload)
                self.assertFalse(handle.writable())
        after = self.path.stat()
        self.assertEqual(self.path.read_bytes(), self.payload)
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)

    def test_open_revalidates_a_supplied_resolved_location(self):
        outside = self.root.parent / f"{self.root.name}-outside.flac"
        outside.write_bytes(b"outside-root")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        supplied = ResolvedLocation(
            root=StorageRoot(key="music_library", server_root=self.root),
            logical_path=PurePosixPath("Artist/Album/track.flac"),
            server_path=outside,
            client_path=None,
        )
        with self._settings(), open_for_read(supplied) as handle:
            self.assertEqual(handle.read(), self.payload)

        unsafe = ResolvedLocation(
            root=StorageRoot(key="music_library", server_root=self.root),
            logical_path=PurePosixPath("../outside.flac"),
            server_path=outside,
            client_path=None,
        )
        with self._settings(), self.assertRaises(StoragePathError):
            open_for_read(unsafe)

    def test_explicit_approved_root_configuration_is_supported(self):
        configured = {
            "music_library": {
                "server_root": str(self.root),
                "client_root": r"Z:\Radio",
                "backend": "filesystem",
                "read_only": True,
            }
        }
        with self._settings(P7_STORAGE_ROOTS=configured):
            root = get_storage_root(require_directory=True)
            resolved = resolve_location(self.location)
        self.assertTrue(root.read_only)
        self.assertEqual(resolved.server_path, self.path.resolve())
        self.assertEqual(resolved.client_path, r"Z:\Radio\Artist\Album\track.flac")

    def test_location_can_map_to_an_explicit_approved_root_key(self):
        configured = {
            "archive": {
                "server_root": str(self.root),
                "client_root": r"\\ARCHIVE\Radio",
            }
        }
        with self._settings(
            P7_STORAGE_ROOTS=configured,
            P7_STORAGE_TYPE_ROOTS={"nas": "archive"},
        ):
            resolved = resolve_location(self.location)
        self.assertEqual(resolved.root.key, "archive")
        self.assertEqual(resolved.server_path, self.path.resolve())

    def test_relative_client_root_is_rejected(self):
        with self._settings(P7_MUSIC_CLIENT_ROOT="relative/client"), self.assertRaises(
            ImproperlyConfigured
        ):
            get_storage_root()

    def test_unknown_root_is_not_approved(self):
        with self._settings(), self.assertRaises(ImproperlyConfigured):
            get_storage_root("not_configured")
