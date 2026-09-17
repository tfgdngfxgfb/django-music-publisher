import hashlib
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from catalogue.models import Recording, Release, ReleaseTrack
from media_assets.models import FileAsset, FileLocation
from media_assets.playback import (
    RadioPlaybackStatus,
    resolve_current_radio_asset,
)
from music_library.models import MusicLibraryEntry


class RadioPlaybackTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = b"fLaC" + bytes(range(256)) * 8
        self.path = self.root / "radio" / "test.flac"
        self.path.parent.mkdir()
        self.path.write_bytes(self.payload)
        self.recording = Recording.objects.create(title="Spillbar innspilling")
        self.entry = MusicLibraryEntry.objects.create(recording=self.recording)
        self.asset = FileAsset.objects.create(
            recording=self.recording,
            filename=self.path.name,
            role=FileAsset.Role.RADIO_FLAC,
            mime_type="audio/flac",
        )
        self.location = FileLocation.objects.create(
            asset=self.asset,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="radio/test.flac",
            status=FileLocation.Status.ACTIVE,
            is_current=True,
        )
        self.user = get_user_model().objects.create_user(
            username="listener", password="test-password"
        )
        self.user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label__in={
                    "catalogue",
                    "media_assets",
                    "music_library",
                },
                codename__in={
                    "view_recording",
                    "view_release",
                    "view_fileasset",
                    "view_filelocation",
                    "view_musiclibraryentry",
                },
            )
        )
        self.client.force_login(self.user)
        self.url = reverse("gui_v2:recording_audio", args=[self.recording.pk])

    def _get(self, **headers):
        with override_settings(P7_MUSIC_ROOT=self.root, P7_NAS_ROOT=self.root):
            return self.client.get(self.url, **headers)

    def test_global_player_has_recording_audio_route_for_reload(self):
        response = self.client.get(
            reverse("gui_v2:recording_detail", args=[self.recording.pk])
        )
        template_url = reverse(
            "gui_v2:recording_audio",
            args=["00000000-0000-0000-0000-000000000000"],
        )
        self.assertContains(response, f'data-audio-url-template="{template_url}"')
        self.assertContains(response, "gui_v2/player.css")
        self.assertNotContains(response, "data-player-hide")
        self.assertNotContains(response, "data-player-show")

    def test_resolver_returns_one_current_radio_asset(self):
        result = resolve_current_radio_asset(self.recording)
        self.assertEqual(result.status, RadioPlaybackStatus.AVAILABLE)
        self.assertEqual(result.asset, self.asset)
        self.assertEqual(result.location, self.location)

    def test_resolver_has_no_fallback_and_rejects_ambiguity(self):
        empty = Recording.objects.create(title="Uten fil")
        self.assertEqual(
            resolve_current_radio_asset(empty).status,
            RadioPlaybackStatus.NO_RADIO_FILE,
        )
        other = FileAsset.objects.create(
            recording=self.recording,
            filename="annen.flac",
            role=FileAsset.Role.RADIO_FLAC,
        )
        FileLocation.objects.create(
            asset=other,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="radio/annen.flac",
            status=FileLocation.Status.ACTIVE,
            is_current=True,
        )
        self.assertEqual(
            resolve_current_radio_asset(self.recording).status,
            RadioPlaybackStatus.AMBIGUOUS,
        )

    def test_historical_location_is_not_selected_over_current_asset(self):
        old = FileAsset.objects.create(
            recording=self.recording,
            filename="historisk.flac",
            role=FileAsset.Role.RADIO_FLAC,
        )
        FileLocation.objects.create(
            asset=old,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="radio/historisk.flac",
            status=FileLocation.Status.HISTORICAL,
            is_current=False,
        )
        result = resolve_current_radio_asset(self.recording)
        self.assertEqual(result.status, RadioPlaybackStatus.AVAILABLE)
        self.assertEqual(result.asset, self.asset)

    def test_missing_physical_file_is_unavailable_when_play_is_attempted(self):
        self.path.unlink()
        with override_settings(P7_MUSIC_ROOT=self.root, P7_NAS_ROOT=self.root):
            result = resolve_current_radio_asset(
                self.recording, verify_file=True
            )
        self.assertEqual(result.status, RadioPlaybackStatus.FILE_UNAVAILABLE)
        response = self._get()
        self.assertEqual(response.status_code, 404)
        self.assertContains(
            response,
            "Radiofilen er ikke tilgjengelig fra registrert plassering.",
            status_code=404,
        )

    def test_full_get_streams_inline_without_loading_response_body_eagerly(
        self,
    ):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.streaming)
        self.assertEqual(response["Content-Type"], "audio/flac")
        self.assertEqual(response["Accept-Ranges"], "bytes")
        self.assertEqual(int(response["Content-Length"]), len(self.payload))
        self.assertTrue(response["Content-Disposition"].startswith("inline;"))
        self.assertEqual(b"".join(response.streaming_content), self.payload)

    def test_range_get_returns_correct_partial_content(self):
        response = self._get(HTTP_RANGE="bytes=4-19")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(
            response["Content-Range"], f"bytes 4-19/{len(self.payload)}"
        )
        self.assertEqual(response["Content-Length"], "16")
        self.assertEqual(
            b"".join(response.streaming_content), self.payload[4:20]
        )

        suffix = self._get(HTTP_RANGE="bytes=-8")
        self.assertEqual(suffix.status_code, 206)
        self.assertEqual(b"".join(suffix.streaming_content), self.payload[-8:])

    def test_invalid_or_multiple_range_is_rejected(self):
        for value in (
            "bytes=99999-",
            "bytes=20-10",
            "bytes=0-1,4-5",
            "items=0-2",
        ):
            with self.subTest(value=value):
                response = self._get(HTTP_RANGE=value)
                self.assertEqual(response.status_code, 416)
                self.assertEqual(
                    response["Content-Range"], f"bytes */{len(self.payload)}"
                )

    def test_path_traversal_in_persisted_location_cannot_escape_root(self):
        # Simulate legacy/corrupt persisted data; normal model writes reject this.
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE media_assets_filelocation SET relative_path = %s WHERE id = %s",
                ["../hemmelig.flac", self.location.pk.hex],
            )
        response = self._get()
        self.assertEqual(response.status_code, 404)

    def test_unknown_recording_and_user_without_permissions_get_no_audio(self):
        unknown = reverse(
            "gui_v2:recording_audio",
            args=["00000000-0000-0000-0000-000000000001"],
        )
        with override_settings(P7_MUSIC_ROOT=self.root, P7_NAS_ROOT=self.root):
            self.assertEqual(self.client.get(unknown).status_code, 404)
        denied = get_user_model().objects.create_user(
            username="denied", password="x"
        )
        self.client.force_login(denied)
        with override_settings(P7_MUSIC_ROOT=self.root, P7_NAS_ROOT=self.root):
            self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_playback_is_read_only_for_file_and_catalogue_objects(self):
        before_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        before_mtime = self.path.stat().st_mtime_ns
        recording_updated = self.recording.updated_at
        asset_updated = self.asset.updated_at
        location_updated = self.location.updated_at
        response = self._get(HTTP_RANGE="bytes=0-31")
        self.assertEqual(
            b"".join(response.streaming_content), self.payload[:32]
        )
        self.recording.refresh_from_db()
        self.asset.refresh_from_db()
        self.location.refresh_from_db()
        self.assertEqual(
            hashlib.sha256(self.path.read_bytes()).hexdigest(), before_hash
        )
        self.assertEqual(self.path.stat().st_mtime_ns, before_mtime)
        self.assertEqual(self.recording.updated_at, recording_updated)
        self.assertEqual(self.asset.updated_at, asset_updated)
        self.assertEqual(self.location.updated_at, location_updated)

    def test_all_three_gui_surfaces_use_same_recording_audio_url(self):
        release = Release.objects.create(title="Avspillingsutgivelse")
        track = ReleaseTrack.objects.create(
            release=release, recording=self.recording, sequence_number=1
        )
        expected = self.url
        library = self.client.get(reverse("gui_v2:music_library"))
        overview = self.client.get(
            reverse("gui_v2:recording_detail", args=[self.recording.pk])
        )
        release_page = self.client.get(
            reverse("gui_v2:release_detail", args=[release.pk]),
            {"track": track.pk},
        )
        for response in (library, overview, release_page):
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, expected)
            self.assertContains(response, "data-play-recording")
            self.assertContains(response, "data-player-primary")
            self.assertContains(response, "data-player-queue")
            self.assertContains(response, "data-player-mute")
            self.assertEqual(response.content.count(b'id="v2-audio"'), 1)
        self.assertNotContains(library, "autoplay")
        self.assertNotContains(overview, "autoplay")
        self.assertNotContains(release_page, "autoplay")

    def test_queue_snapshot_markup_preserves_release_sequence_and_unavailable_tracks(self):
        missing = Recording.objects.create(title="Andre spor uten radio")
        playable = Recording.objects.create(title="Tredje spor med radio")
        MusicLibraryEntry.objects.create(recording=missing)
        MusicLibraryEntry.objects.create(recording=playable)
        asset = FileAsset.objects.create(
            recording=playable, filename="third.flac", role=FileAsset.Role.RADIO_FLAC
        )
        FileLocation.objects.create(
            asset=asset,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="radio/third.flac",
            status=FileLocation.Status.ACTIVE,
            is_current=True,
        )
        release = Release.objects.create(title="Køutgivelse")
        ReleaseTrack.objects.create(
            release=release, recording=playable, sequence_number=3, disc_number=2
        )
        ReleaseTrack.objects.create(
            release=release, recording=self.recording, sequence_number=1, side="A"
        )
        ReleaseTrack.objects.create(
            release=release, recording=missing, sequence_number=2, side="B"
        )

        release_page = self.client.get(reverse("gui_v2:release_detail", args=[release.pk]))
        self.assertEqual(release_page.status_code, 200)
        self.assertContains(release_page, f'data-release-id="{release.pk}"')
        html = release_page.content
        positions = [html.index(f'data-playback-title="{title}"'.encode()) for title in (
            self.recording.title, missing.title, playable.title
        )]
        self.assertEqual(positions, sorted(positions))
        self.assertContains(release_page, 'data-playback-url=""')
        self.assertContains(release_page, "Andre spor uten radio")
        library = self.client.get(reverse("gui_v2:music_library"))
        self.assertContains(library, 'data-playback-title="Andre spor uten radio"')
        self.assertContains(library, 'data-playback-url=""')

    def test_ambiguous_recording_renders_no_playback_url(self):
        other = FileAsset.objects.create(
            recording=self.recording,
            filename="annen.flac",
            role=FileAsset.Role.RADIO_FLAC,
        )
        FileLocation.objects.create(
            asset=other,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="radio/annen.flac",
            status=FileLocation.Status.ACTIVE,
            is_current=True,
        )
        response = self.client.get(
            reverse("gui_v2:recording_detail", args=[self.recording.pk])
        )
        self.assertContains(response, "Flere mulige radiofiler er registrert")
        self.assertNotContains(response, f'data-play-url="{self.url}"')
