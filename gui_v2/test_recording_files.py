import hashlib
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from catalogue.models import Recording, RecordingContribution
from media_assets.models import FileAsset, FileChecksum, FileLocation
from media_assets.playback import RadioPlaybackStatus
from music_library.models import MusicLibraryEntry


class RecordingFilesPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="file-admin", password="test-password"
        )
        self.client.force_login(self.user)
        self.recording = Recording.objects.create(
            title="Nordlys over byen", duration_ms=222000
        )
        RecordingContribution.objects.create(
            recording=self.recording,
            role=RecordingContribution.Role.PRIMARY,
            credited_as="Ingrid Solheim",
        )
        self.entry = MusicLibraryEntry.objects.create(recording=self.recording)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def _url(self, **query):
        url = reverse("gui_v2:recording_files", args=[self.recording.pk])
        if query:
            from urllib.parse import urlencode

            return f"{url}?{urlencode(query)}"
        return url

    def _settings(self, **extra):
        values = {
            "P7_MUSIC_ROOT": str(self.root),
            "P7_NAS_ROOT": str(self.root),
            "P7_MUSIC_CLIENT_ROOT": r"\\P7-CLIENT\Music",
        }
        values.update(extra)
        return override_settings(**values)

    def _radio_file(self, filename="Nordlys.flac", *, status="active"):
        path = self.root / "Norsk" / "Ingrid" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fLaC-recording-files-page")
        asset = FileAsset.objects.create(
            recording=self.recording,
            filename=filename,
            role=FileAsset.Role.RADIO_FLAC,
            mime_type="audio/flac",
            size_bytes=78_400_000,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            technical_metadata={
                "sample_rate": 96000,
                "bits_per_sample": 24,
                "channels": 2,
                "duration_ms": 222000,
                "audio_md5": "1" * 32,
            },
        )
        location = FileLocation.objects.create(
            asset=asset,
            storage_type=FileLocation.StorageType.NAS,
            relative_path=f"Norsk/Ingrid/{filename}",
            status=status,
            is_current=status == FileLocation.Status.ACTIVE,
            verification_status=FileLocation.VerificationStatus.VERIFIED,
        )
        FileChecksum.objects.create(
            asset=asset,
            sha256=asset.sha256,
            reason=FileChecksum.Reason.INGEST,
        )
        return asset, location, path

    def test_requires_recording_and_file_view_permissions(self):
        viewer = get_user_model().objects.create_user(username="viewer", password="x")
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(self._url()).status_code, 403)
        viewer.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="catalogue", codename="view_recording"
            ),
            *Permission.objects.filter(
                content_type__app_label="media_assets",
                codename__in=("view_fileasset", "view_filelocation"),
            ),
        )
        self.client.force_login(get_user_model().objects.get(pk=viewer.pk))
        self.assertEqual(self.client.get(self._url()).status_code, 200)

    def test_empty_recording_has_complete_neutral_file_page(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ingen radiofil registrert")
        self.assertContains(response, "Ingen filer registrert for denne innspillingen")
        self.assertContains(response, 'aria-current="page" href="')
        self.assertNotContains(response, "data-file-row")

    def test_current_radio_file_uses_playback_and_client_path(self):
        asset, _, _ = self._radio_file()
        with self._settings():
            response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["file_view"]["resolution"].status,
            RadioPlaybackStatus.AVAILABLE,
        )
        self.assertEqual(
            response.context["file_view"]["current_radio"]["object"], asset
        )
        for text in (
            "Gjeldende radiofil",
            "Nordlys.flac",
            "Radio-FLAC",
            "96 kHz",
            "24 bit",
            "2 (stereo)",
            "03:42",
            "74,8 MB",
            r"\\P7-CLIENT\Music\Norsk\Ingrid\Nordlys.flac",
            "Kopier filsti",
            "Kopier mappesti",
            "PCM-MD5",
        ):
            self.assertContains(response, text)
        self.assertContains(
            response, reverse("gui_v2:recording_audio", args=[self.recording.pk])
        )
        self.assertContains(response, "data-player-primary")
        self.assertNotContains(response, "autoplay")
        self.assertNotContains(response, str(self.root))

    def test_ambiguous_radio_files_are_not_selected_or_playable(self):
        first, _, _ = self._radio_file("første.flac")
        self._radio_file("andre.flac")
        with self._settings():
            response = self.client.get(self._url(selected_file=first.pk))
        self.assertEqual(
            response.context["file_view"]["resolution"].status,
            RadioPlaybackStatus.AMBIGUOUS,
        )
        self.assertContains(response, "Ingen entydig gjeldende radiofil")
        self.assertContains(response, "2 aktive radiofiler")
        self.assertIsNone(response.context["file_view"]["current_radio"])
        self.assertNotContains(response, "data-play-recording")

    def test_missing_file_reference_remains_visible(self):
        asset = FileAsset.objects.create(
            recording=self.recording,
            filename="savnet.flac",
            role=FileAsset.Role.RADIO_FLAC,
            sync_status=FileAsset.SyncStatus.MISSING,
        )
        FileLocation.objects.create(
            asset=asset,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="Savnet/savnet.flac",
            status=FileLocation.Status.MISSING,
            is_current=False,
        )
        with self._settings():
            response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Radiofil sist registrert utilgjengelig")
        self.assertContains(response, "savnet.flac")
        self.assertContains(response, "Fil ikke funnet")

    def test_multiple_locations_and_selected_asset_are_rendered_separately(self):
        radio, current, _ = self._radio_file()
        document = FileAsset.objects.create(
            recording=self.recording,
            filename="notat.pdf",
            role=FileAsset.Role.DOCUMENT,
            mime_type="application/pdf",
        )
        current_document = FileLocation.objects.create(
            asset=document,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="Dokumenter/notat.pdf",
            status=FileLocation.Status.ACTIVE,
            is_current=True,
        )
        FileLocation.objects.create(
            asset=document,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="Historikk/notat.pdf",
            status=FileLocation.Status.HISTORICAL,
            is_current=False,
        )
        with self._settings():
            response = self.client.get(self._url(selected_file=document.pk))
        self.assertEqual(response.context["file_view"]["selected"]["object"], document)
        self.assertContains(response, "notat.pdf")
        self.assertContains(response, "Historisk")
        self.assertContains(response, r"\\P7-CLIENT\Music\Dokumenter\notat.pdf")
        self.assertContains(response, r"\\P7-CLIENT\Music\Historikk\notat.pdf")
        self.assertContains(response, r"\\P7-CLIENT\Music\Norsk\Ingrid\Nordlys.flac")
        self.assertEqual(len(response.context["file_view"]["selected"]["locations"]), 2)
        self.assertTrue(current_document.is_current)
        self.assertTrue(current.is_current)
        self.assertContains(response, str(radio.pk))

    def test_general_copy_action_requires_one_current_client_location(self):
        asset, _, _ = self._radio_file()
        FileLocation.objects.create(
            asset=asset,
            storage_type=FileLocation.StorageType.LOCAL,
            relative_path="Alternativ/Nordlys.flac",
            status=FileLocation.Status.ACTIVE,
            is_current=True,
        )
        with self._settings():
            response = self.client.get(self._url(selected_file=asset.pk))
        selected = response.context["file_view"]["selected"]
        self.assertIsNone(selected["primary_client_location"])
        self.assertTrue(selected["client_location_ambiguous"])
        self.assertContains(
            response, "Velg plassering nedenfor for å kopiere riktig sti."
        )

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_current_radio_exposes_existing_rescan_action(self):
        asset, _, _ = self._radio_file()
        with self._settings():
            response = self.client.get(self._url(selected_file=asset.pk))
        self.assertContains(
            response,
            reverse("gui_v2:rescan_library_file", args=[self.entry.pk, asset.pk]),
        )
        self.assertContains(response, "Les fil på nytt")

    def test_page_render_is_read_only_for_file_and_database(self):
        asset, location, path = self._radio_file()
        before_file = (path.read_bytes(), path.stat().st_mtime_ns)
        before = (self.recording.revision, asset.revision, location.revision)
        with self._settings():
            response = self.client.get(self._url())
        self.recording.refresh_from_db()
        asset.refresh_from_db()
        location.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before_file)
        self.assertEqual(
            (self.recording.revision, asset.revision, location.revision), before
        )

    def test_without_client_root_only_logical_path_is_shown(self):
        self._radio_file()
        with self._settings(P7_MUSIC_CLIENT_ROOT=""):
            response = self.client.get(self._url())
        self.assertContains(response, "Logisk sti")
        self.assertContains(response, "Ingen klient-/Windows-sti er konfigurert")
        self.assertNotContains(response, "Kopier mappesti")
        self.assertNotContains(response, str(self.root))
