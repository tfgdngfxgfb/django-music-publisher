import shutil
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from mutagen.flac import FLAC

from catalogue.models import (
    ExternalIdentifier,
    Recording,
    RecordingContribution,
    Release,
    ReleaseTrack,
)
from managed_music.models import ManagedRecording
from media_assets.models import FileAsset, FileLocation
from music_library.models import MusicLibraryEntry
from provenance.models import SourceRecord

from flac_ingest.adapter import read_flac, write_catalogue_tags
from flac_ingest.models import FlacIngestItem, FlacSyncLog
from flac_ingest.services import (
    apply_batch,
    resolve_music_path,
    scan_directory,
    sync_file_asset,
    sync_recording_files,
)

FIXTURE = Path(__file__).parent / "test_fixtures" / "silence.flac"


class FlacTestMixin:
    def setUp(self):
        super().setUp()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.user = get_user_model().objects.create_superuser(
            username="flac-admin", email="flac@example.invalid", password="test"
        )

    def make_flac(self, name="radio.flac", **tags):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURE, path)
        audio = FLAC(path)
        for key, value in tags.items():
            audio[key] = value if isinstance(value, list) else str(value)
        audio.save()
        return path


class FlacAdapterTests(FlacTestMixin, TestCase):
    def test_all_tags_radio_mapping_and_technical_metadata(self):
        path = self.make_flac(
            TITLE="Norsk vår",
            ARTIST=["Artist A", "Artist B"],
            GENRE="Gospel",
            LANGUAGE="nb",
            RATING="4",
            KANAL=["P7 Riks", "P7 Ung"],
            TARGET=["Ung voksen", "Voksen"],
            X_UNKNOWN="rå verdi",
        )
        snapshot = read_flac(path)
        self.assertEqual(snapshot.parsed["energy"], 4)
        self.assertEqual(snapshot.parsed["channels"], ["P7 Riks", "P7 Ung"])
        self.assertEqual(snapshot.parsed["target_audiences"], ["Ung voksen", "Voksen"])
        raw_tags = {key.upper(): values for key, values in snapshot.raw_tags.items()}
        self.assertEqual(raw_tags["X_UNKNOWN"], ["rå verdi"])
        self.assertEqual(snapshot.technical["sample_rate"], 44100)
        self.assertEqual(snapshot.technical["bits_per_sample"], 16)
        self.assertEqual(snapshot.technical["channels"], 1)

    def test_writeback_changes_allowlist_and_preserves_radio_and_unknown_tags(self):
        path = self.make_flac(
            TITLE="Før",
            GENRE="Pop",
            LANGUAGE="nb",
            RATING="5",
            KANAL=["P7 Riks", "P7 Ung"],
            TARGET="Voksen",
            X_UNKNOWN="behold meg",
        )
        write_catalogue_tags(path, {"TITLE": "Etter", "P7UUID": "test-id"})
        tags = {key.upper(): list(values) for key, values in FLAC(path).tags.items()}
        self.assertEqual(tags["TITLE"], ["Etter"])
        self.assertEqual(tags["RATING"], ["5"])
        self.assertEqual(tags["KANAL"], ["P7 Riks", "P7 Ung"])
        self.assertEqual(tags["X_UNKNOWN"], ["behold meg"])


class FlacIngestTests(FlacTestMixin, TestCase):
    def scan(self):
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            return scan_directory(relative_root=".", recursive=True, user=self.user)

    def apply(self, batch):
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            with self.captureOnCommitCallbacks(execute=True):
                return apply_batch(batch, user=self.user)

    def test_new_tagged_file_creates_operational_catalogue_and_is_idempotent(self):
        self.make_flac(
            "Album/spor01.flac",
            TITLE="Lyset vender",
            ARTIST="Uavklart artistnavn",
            ALBUMARTIST="Uavklart artistnavn",
            ALBUM="Prøvealbum",
            DATE="2026",
            CATALOGNUMBER="P7-TEST-001",
            TRACKNUMBER="1",
            DISCNUMBER="1",
            ISRC="NO-P7T-26-00001",
            COMPOSER="Komponist uten identitetsmatch",
            LYRICIST="Tekstforfatter uten identitetsmatch",
            GENRE="Pop",
            LANGUAGE="nb",
            RATING="3",
            KANAL=["P7 Riks", "P7 Ung"],
            TARGET=["Ung voksen", "Voksen"],
            X_UNKNOWN="bevares",
        )
        batch = self.scan()
        item = batch.items.get()
        self.assertEqual(item.action, FlacIngestItem.Action.NEW)
        self.assertEqual(self.apply(batch), 1)
        item.refresh_from_db()
        recording = item.recording
        self.assertEqual(recording.title, "Lyset vender")
        self.assertEqual(
            recording.identifiers.get(
                scheme=ExternalIdentifier.Scheme.ISRC
            ).normalized_value,
            "NOP7T2600001",
        )
        self.assertTrue(
            recording.contributions.filter(
                role=RecordingContribution.Role.COMPOSER,
                party__isnull=True,
                credited_as="Komponist uten identitetsmatch",
            ).exists()
        )
        entry = recording.music_library_entry
        self.assertEqual(entry.energy, 3)
        self.assertEqual(
            set(entry.channels.values_list("name", flat=True)), {"P7 Riks", "P7 Ung"}
        )
        self.assertEqual(
            set(entry.target_audiences.values_list("name", flat=True)),
            {"Ung voksen", "Voksen"},
        )
        self.assertEqual(Release.objects.get().title, "Prøvealbum")
        self.assertEqual(ReleaseTrack.objects.get().recording_id, recording.pk)
        asset = FileAsset.objects.get(recording=recording)
        self.assertEqual(asset.release_track_id, item.release_track_id)
        self.assertEqual(asset.sync_status, FileAsset.SyncStatus.SYNCED)
        self.assertTrue(SourceRecord.objects.filter(pk=item.source_record_id).exists())
        self.assertEqual(
            read_flac(self.root / item.relative_path).parsed["p7uuid"],
            str(recording.pk),
        )
        self.client.force_login(self.user)
        library_response = self.client.get(reverse("workbench:library"))
        self.assertEqual(library_response.status_code, 200)
        self.assertContains(library_response, "Uavklart artistnavn")

        second = self.scan()
        self.assertEqual(second.items.get().action, FlacIngestItem.Action.UNCHANGED)
        self.client.force_login(self.user)
        preview = self.client.get(
            reverse("workbench:flac_ingest_preview", args=[second.pk])
        )
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "Uendret fil")
        self.assertEqual(Recording.objects.count(), 1)
        self.assertEqual(Release.objects.count(), 1)
        self.assertEqual(ReleaseTrack.objects.count(), 1)
        self.assertEqual(FileAsset.objects.count(), 1)

        audio = FLAC(self.root / item.relative_path)
        audio["TITLE"] = "Lyset vender igjen"
        audio["RATING"] = "5"
        audio.save()
        changed = self.scan()
        self.assertEqual(changed.items.get().match_method, "file_location")
        self.apply(changed)
        recording.refresh_from_db()
        entry.refresh_from_db()
        self.assertEqual(recording.title, "Lyset vender igjen")
        self.assertEqual(entry.energy, 5)
        self.assertEqual(Recording.objects.count(), 1)
        self.assertEqual(Release.objects.count(), 1)
        self.assertEqual(ReleaseTrack.objects.count(), 1)

        third = self.scan()
        self.assertEqual(third.items.get().action, FlacIngestItem.Action.UNCHANGED)

    def test_files_with_same_new_isrc_reuse_recording_when_batch_is_applied(self):
        common_tags = {
            "ARTIST": "Uavklart felles artist",
            "ISRC": "NO-P7T-26-00999",
        }
        self.make_flac("sett/spor-a.flac", TITLE="Første fil", **common_tags)
        self.make_flac("sett/spor-b.flac", TITLE="Andre fil", **common_tags)

        batch = self.scan()
        self.assertEqual(
            list(batch.items.values_list("action", flat=True)),
            [FlacIngestItem.Action.NEW, FlacIngestItem.Action.NEW],
        )
        self.assertEqual(self.apply(batch), 2)

        recording_ids = set(batch.items.values_list("recording_id", flat=True))
        self.assertEqual(len(recording_ids), 1)
        self.assertEqual(Recording.objects.count(), 1)
        self.assertEqual(ExternalIdentifier.objects.count(), 1)
        self.assertEqual(FileAsset.objects.count(), 2)

    def test_managed_catalogue_is_protected_but_radio_metadata_updates(self):
        recording = Recording.objects.create(title="Databasefasit")
        ExternalIdentifier.objects.create(
            recording=recording,
            scheme=ExternalIdentifier.Scheme.ISRC,
            value="NO-P7T-26-00002",
        )
        library = MusicLibraryEntry.objects.create(recording=recording, genre="Før")
        ManagedRecording.objects.create(library_entry=library)
        path = self.make_flac(
            TITLE="Feil filtittel",
            ISRC="NO-P7T-26-00002",
            P7UUID=str(recording.pk),
            GENRE="Ny radiosjanger",
            LANGUAGE="nn",
            RATING="5",
            KANAL=["P7 Riks", "P7 Ung"],
            TARGET="Voksen",
            X_UNKNOWN="beholdes",
        )
        batch = self.scan()
        self.assertEqual(batch.items.get().action, FlacIngestItem.Action.MATCHED)
        self.apply(batch)
        recording.refresh_from_db()
        library.refresh_from_db()
        self.assertEqual(recording.title, "Databasefasit")
        self.assertEqual(library.genre, "Ny radiosjanger")
        self.assertEqual(library.language, "nn")
        self.assertEqual(library.energy, 5)
        tags = {key.upper(): list(values) for key, values in FLAC(path).tags.items()}
        self.assertEqual(tags["TITLE"], ["Databasefasit"])
        self.assertEqual(tags["RATING"], ["5"])
        self.assertEqual(tags["X_UNKNOWN"], ["beholdes"])
        self.assertEqual(Release.objects.count(), 0)

        recording.title = "Endret gjennom P7"
        recording.save(update_fields=("title",))
        asset = recording.file_assets.get()
        asset.refresh_from_db()
        self.assertEqual(asset.sync_status, FileAsset.SyncStatus.PENDING)
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            results = sync_recording_files(recording)
        self.assertEqual(results[0].result, FlacSyncLog.Result.SUCCESS)
        tags = {key.upper(): list(values) for key, values in FLAC(path).tags.items()}
        self.assertEqual(tags["TITLE"], ["Endret gjennom P7"])
        self.assertEqual(tags["GENRE"], ["Ny radiosjanger"])
        self.assertEqual(tags["RATING"], ["5"])

    def test_conflicting_p7uuid_and_isrc_is_not_applied(self):
        first = Recording.objects.create(title="Første")
        second = Recording.objects.create(title="Andre")
        ExternalIdentifier.objects.create(
            recording=second,
            scheme=ExternalIdentifier.Scheme.ISRC,
            value="NO-P7T-26-00003",
        )
        self.make_flac(
            TITLE="Konflikt",
            P7UUID=str(first.pk),
            ISRC="NO-P7T-26-00003",
        )
        batch = self.scan()
        item = batch.items.get()
        self.assertEqual(item.action, FlacIngestItem.Action.CONFLICT)
        self.assertEqual(self.apply(batch), 0)
        batch.refresh_from_db()
        self.assertEqual(batch.status, batch.Status.PARTIAL)
        self.assertEqual(Recording.objects.count(), 2)

    def test_missing_release_metadata_creates_recording_without_release(self):
        self.make_flac(TITLE="Kun innspilling", ARTIST="Uavklart")
        batch = self.scan()
        self.apply(batch)
        self.assertEqual(Recording.objects.count(), 1)
        self.assertEqual(Release.objects.count(), 0)
        self.assertEqual(ReleaseTrack.objects.count(), 0)

    def test_path_must_stay_inside_configured_root(self):
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            with self.assertRaises(ValidationError):
                resolve_music_path("../utenfor")

    def test_invalid_controlled_radio_value_is_sent_to_control(self):
        self.make_flac(TITLE="Ugyldig Energy", RATING="9")
        item = self.scan().items.get()
        self.assertEqual(item.action, FlacIngestItem.Action.CONFLICT)
        self.assertIn("RATING må være et Energy-nivå fra 1 til 5.", item.messages)

    def test_sync_failure_keeps_database_change_and_retry_state(self):
        recording = Recording.objects.create(title="Ny databaseverdi")
        library = MusicLibraryEntry.objects.create(recording=recording)
        ManagedRecording.objects.create(library_entry=library)
        asset = FileAsset.objects.create(
            recording=recording,
            filename="mangler.flac",
            role=FileAsset.Role.RADIO_FLAC,
            sync_status=FileAsset.SyncStatus.PENDING,
        )
        FileLocation.objects.create(
            asset=asset,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="mangler.flac",
        )
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            result = sync_file_asset(asset.pk)
        recording.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(recording.title, "Ny databaseverdi")
        self.assertEqual(result.result, FlacSyncLog.Result.MISSING)
        self.assertEqual(asset.sync_status, FileAsset.SyncStatus.MISSING)
        self.assertTrue(asset.sync_error)


class FlacWorkbenchPermissionTests(FlacTestMixin, TestCase):
    def test_ingest_requires_login_and_server_side_permission(self):
        url = reverse("workbench:flac_ingest_start")
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            staff = get_user_model().objects.create_user(
                username="staff", password="test", is_staff=True
            )
            self.client.force_login(staff)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.client.force_login(self.user)
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Les inn fra musikkarkiv")
