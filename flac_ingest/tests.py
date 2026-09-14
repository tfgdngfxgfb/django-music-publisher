import shutil
import tempfile
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
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
from parties.models import Party
from provenance.models import (
    AssertionDecision,
    MetadataAssertion,
    SourceRecord,
    SourceSystem,
)
from rights_core.models import VerificationStatus
from rights.models import Agreement, RightsClaim

from flac_ingest.adapter import (
    FlacWriteError,
    file_sha256,
    read_flac,
    write_catalogue_tags,
)
from flac_ingest.maintenance import (
    create_cleanup_preview,
    create_rebuild_preview,
    execute_cleanup,
    execute_rebuild,
)
from flac_ingest.models import (
    FlacIngestBatch,
    FlacIngestItem,
    FlacMaintenanceJob,
    FlacSyncLog,
)
from flac_ingest.services import (
    apply_batch,
    _source_record_reference,
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
        self.assertEqual(snapshot.technical["tag_adapter_version"], 3)

    def test_onetagger_percentage_rating_maps_to_p7_energy(self):
        for raw_rating, expected_energy in (
            ("20", 1),
            ("40", 2),
            ("60", 3),
            ("80", 4),
            ("100", 5),
        ):
            path = self.make_flac(f"rating-{raw_rating}.flac", RATING=raw_rating)
            self.assertEqual(read_flac(path).parsed["energy"], expected_energy)

    def test_track_and_disc_fraction_variants_keep_raw_values(self):
        for number, expected in (("3", 3), ("03", 3), ("3/12", 3)):
            path = self.make_flac(
                f"position-{number.replace('/', '-')}.flac",
                TRACKNUMBER=number,
                DISCNUMBER="1/2",
            )
            snapshot = read_flac(path)
            self.assertEqual(snapshot.parsed["track_number"], expected)
            self.assertEqual(snapshot.parsed["disc_number"], 1)
            self.assertEqual(snapshot.raw_tags["tracknumber"], [number])
            self.assertEqual(snapshot.raw_tags["discnumber"], ["1/2"])

    def test_stationplaylist_txxx_comments_map_to_radio_metadata(self):
        comments = [
            "TXXX:Språk - Norsk",
            "TXXX:Target - 60+",
            "TXXX:Gender - Group",
            "TXXX:Kanal - P7 Evangelisk",
            "TXXX:Kanal - P7 Riks",
            "TXXX:Rotasjon - Ikke rotasjonsverdig",
        ]
        path = self.make_flac("stationplaylist.flac", COMMENT=comments)

        snapshot = read_flac(path)

        self.assertEqual(snapshot.parsed["language"], "no")
        self.assertEqual(snapshot.parsed["target_audiences"], ["60+"])
        self.assertEqual(snapshot.parsed["gender"], "group")
        self.assertEqual(snapshot.parsed["channels"], ["P7 Evangelisk", "P7 Riks"])
        self.assertEqual(snapshot.parsed["rotation_suitability"], "not_suitable")
        self.assertEqual(snapshot.raw_tags["comment"], comments)

    def test_onetagger_african_languages_group_is_preserved(self):
        path = self.make_flac(
            "african-languages.flac",
            TITLE="Samlebetegnelse",
            LANGUAGE="Afrikanske språk",
        )

        snapshot = read_flac(path)

        self.assertEqual(snapshot.parsed["language"], "Afrikanske språk")
        self.assertEqual(snapshot.raw_tags["language"], ["Afrikanske språk"])

    def test_multiple_genres_are_preserved_in_the_interpreted_value(self):
        path = self.make_flac("multiple-genres.flac", GENRE=["Evangelisk", "Viser"])

        snapshot = read_flac(path)

        self.assertEqual(snapshot.parsed["genre"], "Evangelisk; Viser")
        self.assertEqual(snapshot.parsed["genre_values"], ["Evangelisk", "Viser"])
        self.assertEqual(snapshot.raw_tags["genre"], ["Evangelisk", "Viser"])

    def test_flac_without_vorbis_comments_keeps_technical_metadata(self):
        path = self.make_flac("without-comments.flac", TITLE="Fjernes")
        audio = FLAC(path)
        audio.clear()
        audio.save()

        snapshot = read_flac(path)

        self.assertEqual(snapshot.raw_tags, {})
        self.assertEqual(snapshot.parsed, {})
        self.assertEqual(snapshot.technical["container"], "FLAC")
        self.assertEqual(snapshot.technical["sample_rate"], 44100)

    @override_settings(P7_ALLOW_FILE_WRITES=True)
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

    def test_low_level_writeback_is_blocked_by_default(self):
        path = self.make_flac(TITLE="Uendret")
        before = path.read_bytes()
        with self.assertRaisesMessage(FlacWriteError, "Filskriving er deaktivert"):
            write_catalogue_tags(path, {"TITLE": "Skal ikke skrives"})
        self.assertEqual(path.read_bytes(), before)


class FlacIngestTests(FlacTestMixin, TestCase):
    def scan(self):
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            return scan_directory(relative_root=".", recursive=True, user=self.user)

    def apply(self, batch):
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            with self.captureOnCommitCallbacks(execute=True):
                return apply_batch(batch, user=self.user)

    def test_long_path_uses_bounded_source_identity_and_preserves_full_path(self):
        relative_path = f"lang-katalog/{'svært-lang-kildetittel-' * 24}.flac"
        item = SimpleNamespace(
            batch_id=uuid4(), pk=uuid4(), relative_path=relative_path
        )
        external_record_id, source_locator = _source_record_reference(item)

        self.assertLessEqual(len(external_record_id), 255)
        self.assertLessEqual(len(source_locator), 500)
        self.assertTrue(external_record_id.startswith("flac:"))
        self.assertTrue(source_locator.endswith(".flac"))

    def test_onetagger_language_group_imports_without_manual_review(self):
        self.make_flac(
            TITLE="Samlebetegnelse fra OneTagger",
            LANGUAGE="Afrikanske språk",
        )

        batch = self.scan()
        item = batch.items.get()

        self.assertEqual(item.action, FlacIngestItem.Action.NEW)
        self.assertEqual(item.messages, [])
        self.assertEqual(self.apply(batch), 1)
        item.refresh_from_db()
        self.assertEqual(
            item.recording.music_library_entry.language,
            "Afrikanske språk",
        )

    def test_new_tagged_file_creates_operational_catalogue_and_is_idempotent(self):
        path = self.make_flac(
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
            COMMENT="TXXX:Rotasjon - Ikke Rotasjonsverdig",
            X_UNKNOWN="bevares",
        )
        original_bytes = path.read_bytes()
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
        self.assertEqual(entry.rotation_suitability, "not_suitable")
        self.assertEqual(entry.verification_status, VerificationStatus.CONFIRMED)
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
        flac_assertions = MetadataAssertion.objects.filter(
            source_record_id=item.source_record_id
        )
        self.assertTrue(flac_assertions.exists())
        self.assertFalse(
            flac_assertions.exclude(status=VerificationStatus.CONFIRMED).exists()
        )
        self.assertEqual(
            AssertionDecision.objects.filter(
                assertion__source_record_id=item.source_record_id,
                decision=VerificationStatus.CONFIRMED,
            ).count(),
            flac_assertions.count(),
        )
        self.assertNotIn("p7uuid", read_flac(path).parsed)
        self.assertEqual(path.read_bytes(), original_bytes)
        self.client.force_login(self.user)
        library_response = self.client.get(reverse("workbench:library"))
        self.assertEqual(library_response.status_code, 200)
        self.assertContains(library_response, "Uavklart artistnavn")
        contributors_response = self.client.get(
            reverse("workbench:recording", args=[recording.pk]) + "?fane=contributors"
        )
        self.assertEqual(contributors_response.status_code, 200)
        self.assertContains(contributors_response, "Komponist uten identitetsmatch")
        release_response = self.client.get(
            reverse("workbench:release", args=[Release.objects.get().pk])
        )
        self.assertEqual(release_response.status_code, 200)
        self.assertContains(release_response, "Uavklart artistnavn")

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

    def test_new_adapter_version_reprocesses_unchanged_registered_file(self):
        self.make_flac(
            TITLE="Eldre innlesing",
            COMMENT=[
                "TXXX:Kanal - P7 Evangelisk",
                "TXXX:Target - 60+",
            ],
        )
        first = self.scan()
        self.assertEqual(self.apply(first), 1)
        first_item = first.items.get()
        entry = first_item.recording.music_library_entry
        self.assertEqual(
            list(entry.channels.values_list("name", flat=True)), ["P7 Evangelisk"]
        )

        entry.channel_links.all().delete()
        asset = first_item.file_asset
        asset.technical_metadata.pop("tag_adapter_version", None)
        asset.save(update_fields=("technical_metadata",))

        second = self.scan()
        second_item = second.items.get()
        self.assertEqual(second_item.action, FlacIngestItem.Action.UPDATED)
        self.assertEqual(second_item.parsed_metadata["channels"], ["P7 Evangelisk"])
        self.assertEqual(self.apply(second), 1)
        self.assertEqual(
            list(entry.channels.values_list("name", flat=True)), ["P7 Evangelisk"]
        )

    def test_release_context_version_backfills_structure_for_existing_file(self):
        self.make_flac(
            "Artist/Album/01.flac",
            TITLE="Eksisterende radiospor",
            ARTIST="Testartist",
            ALBUM="Album",
            TRACKNUMBER="1",
        )
        first = self.scan()
        self.assertEqual(self.apply(first), 1)
        first_item = first.items.get()
        asset = first_item.file_asset
        asset.release_track = None
        asset.technical_metadata.pop("release_context_version", None)
        asset.save(update_fields=("release_track", "technical_metadata"))
        first_item.release_track = None
        first_item.release = None
        first_item.save(update_fields=("release_track", "release"))
        ReleaseTrack.objects.all().delete()
        Release.objects.all().delete()

        second = self.scan()
        self.assertEqual(second.items.get().action, FlacIngestItem.Action.UPDATED)
        self.assertEqual(self.apply(second), 1)
        asset.refresh_from_db()
        self.assertIsNotNone(asset.release_track_id)
        self.assertEqual(Release.objects.count(), 1)
        self.assertEqual(ReleaseTrack.objects.count(), 1)
        self.assertEqual(asset.technical_metadata["release_context_version"], 1)

        third = self.scan()
        self.assertEqual(third.items.get().action, FlacIngestItem.Action.UNCHANGED)

    def test_broken_file_is_isolated_from_readable_files(self):
        self.make_flac("good.flac", TITLE="Lesbar")
        (self.root / "broken.flac").write_bytes(b"not a flac file")

        batch = self.scan()

        self.assertEqual(batch.items.count(), 2)
        self.assertEqual(
            batch.items.get(relative_path="broken.flac").action,
            FlacIngestItem.Action.INVALID,
        )
        self.assertEqual(
            batch.items.get(relative_path="good.flac").action,
            FlacIngestItem.Action.NEW,
        )
        self.assertEqual(self.apply(batch), 1)
        self.assertEqual(Recording.objects.get().title, "Lesbar")

    def test_missing_and_overlong_title_are_isolated_as_actionable_conflicts(self):
        empty_path = self.make_flac("empty-tags.flac", TITLE="Fjernes")
        empty_audio = FLAC(empty_path)
        empty_audio.clear()
        empty_audio.save()
        self.make_flac("long-title.flac", TITLE="Æ" * 501)
        self.make_flac("good.flac", TITLE="Gyldig")

        batch = self.scan()

        empty_item = batch.items.get(relative_path="empty-tags.flac")
        long_item = batch.items.get(relative_path="long-title.flac")
        self.assertEqual(empty_item.action, FlacIngestItem.Action.CONFLICT)
        self.assertIn("TITLE mangler", empty_item.messages[0])
        self.assertEqual(long_item.action, FlacIngestItem.Action.CONFLICT)
        self.assertIn("TITLE er lengre enn 500 tegn", long_item.messages[0])
        self.assertEqual(self.apply(batch), 1)
        self.assertEqual(
            list(Recording.objects.values_list("title", flat=True)), ["Gyldig"]
        )

    def test_file_changed_during_scan_is_deferred_without_catalogue_writes(self):
        path = self.make_flac("changing.flac", TITLE="Ustabil")
        real_hash = file_sha256

        def hash_then_change(value):
            checksum = real_hash(value)
            with Path(value).open("ab") as stream:
                stream.write(b"changed")
            return checksum

        with patch("flac_ingest.services.file_sha256", side_effect=hash_then_change):
            batch = self.scan()

        item = batch.items.get()
        self.assertEqual(item.action, FlacIngestItem.Action.RETRY)
        self.assertIn("endret under skanning", item.messages[0])
        self.assertEqual(Recording.objects.count(), 0)
        self.assertTrue(path.exists())

    def test_missing_file_at_apply_is_isolated_and_other_file_is_imported(self):
        missing = self.make_flac("a-missing.flac", TITLE="Forsvunnet")
        self.make_flac("b-good.flac", TITLE="Beholdt")
        batch = self.scan()
        missing.unlink()

        self.assertEqual(self.apply(batch), 1)

        missing_item = batch.items.get(relative_path="a-missing.flac")
        self.assertEqual(missing_item.action, FlacIngestItem.Action.RETRY)
        self.assertIn("ikke tilgjengelig", missing_item.messages[0])
        self.assertEqual(
            list(Recording.objects.values_list("title", flat=True)), ["Beholdt"]
        )
        self.assertEqual(SourceRecord.objects.count(), 1)

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
        self.assertEqual(
            ExternalIdentifier.objects.filter(
                scheme=ExternalIdentifier.Scheme.ISRC
            ).count(),
            1,
        )
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
        self.assertEqual(tags["TITLE"], ["Feil filtittel"])
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
        self.assertEqual(results, [None])
        self.assertEqual(list(FLAC(path).tags["TITLE"]), ["Feil filtittel"])
        with override_settings(P7_MUSIC_ROOT=str(self.root), P7_ALLOW_FILE_WRITES=True):
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

    def test_missing_p7uuid_stays_a_conflict_without_recovery_override(self):
        missing_uuid = uuid4()
        self.make_flac(TITLE="Ukjent UUID", P7UUID=str(missing_uuid))

        item = self.scan().items.get()

        self.assertEqual(item.action, FlacIngestItem.Action.CONFLICT)
        self.assertIn("P7UUID peker ikke", item.messages[0])
        self.assertFalse(Recording.objects.filter(pk=missing_uuid).exists())

    def test_administrator_can_restore_recording_with_uuid_from_file(self):
        restored_uuid = uuid4()
        self.make_flac(
            TITLE="Gjenopprettet innspilling",
            P7UUID=str(restored_uuid),
            ISRC="NO-P7T-26-00888",
        )
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            batch = scan_directory(
                relative_root=".",
                recursive=True,
                user=self.user,
                allow_uuid_recovery=True,
            )

        item = batch.items.get()
        self.assertTrue(batch.allow_uuid_recovery)
        self.assertEqual(item.action, FlacIngestItem.Action.NEW)
        self.assertEqual(item.match_method, "p7uuid_recovery")
        self.assertEqual(self.apply(batch), 1)
        item.refresh_from_db()
        self.assertEqual(item.recording_id, restored_uuid)
        self.assertEqual(Recording.objects.get().pk, restored_uuid)

        repeated = self.scan()
        self.assertEqual(repeated.items.get().action, FlacIngestItem.Action.UNCHANGED)

    def test_uuid_recovery_does_not_override_existing_isrc_identity(self):
        existing = Recording.objects.create(title="Eksisterende ISRC")
        ExternalIdentifier.objects.create(
            recording=existing,
            scheme=ExternalIdentifier.Scheme.ISRC,
            value="NO-P7T-26-00889",
        )
        self.make_flac(
            TITLE="Motstridende UUID",
            P7UUID=str(uuid4()),
            ISRC="NO-P7T-26-00889",
        )
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            batch = scan_directory(
                relative_root=".",
                recursive=True,
                user=self.user,
                allow_uuid_recovery=True,
            )

        item = batch.items.get()
        self.assertEqual(item.action, FlacIngestItem.Action.CONFLICT)
        self.assertIn("konflikt i kildekatalogen", item.messages[0])
        self.assertEqual(self.apply(batch), 0)
        self.assertEqual(Recording.objects.count(), 1)

    def test_missing_release_metadata_creates_recording_without_release(self):
        self.make_flac(TITLE="Kun innspilling", ARTIST="Uavklart")
        batch = self.scan()
        self.apply(batch)
        self.assertEqual(Recording.objects.count(), 1)
        self.assertEqual(Release.objects.count(), 0)
        self.assertEqual(ReleaseTrack.objects.count(), 0)

    def test_album_artist_year_and_track_build_one_release_without_barcode(self):
        shared = {
            "ALBUM": "Album uten katalognummer",
            "ALBUMARTIST": "Samlet artist",
            "DATE": "1978-04-03",
        }
        self.make_flac("album/01.flac", TITLE="Første spor", TRACKNUMBER="1", **shared)
        self.make_flac("album/02.flac", TITLE="Andre spor", TRACKNUMBER="2", **shared)

        batch = self.scan()
        self.assertEqual(self.apply(batch), 2)

        release = Release.objects.get()
        self.assertEqual(release.title, "Album uten katalognummer")
        self.assertEqual(release.release_year, 1978)
        self.assertEqual(release.verification_status, VerificationStatus.CONFIRMED)
        self.assertEqual(release.tracks.count(), 2)
        self.assertTrue(
            release.identifiers.filter(
                scheme=ExternalIdentifier.Scheme.EXTERNAL,
                namespace="p7-flac-release-signature-v1",
            ).exists()
        )

    def test_same_album_title_with_different_artist_does_not_merge_releases(self):
        common = {"ALBUM": "Samme albumtittel", "DATE": "1984", "TRACKNUMBER": "1"}
        self.make_flac(
            "Artist A/Album/artist-a.flac",
            TITLE="Spor A",
            ALBUMARTIST="Artist A",
            **common,
        )
        self.make_flac(
            "Artist B/Album/artist-b.flac",
            TITLE="Spor B",
            ALBUMARTIST="Artist B",
            **common,
        )

        batch = self.scan()
        self.assertEqual(self.apply(batch), 2)

        self.assertEqual(Release.objects.count(), 2)
        self.assertEqual(ReleaseTrack.objects.count(), 2)

    def test_folder_supplies_album_and_groups_multi_disc_with_cover(self):
        album_folder = self.root / "Artist" / "Album fra mappe"
        album_folder.mkdir(parents=True)
        (album_folder / "cover.jpg").write_bytes(b"syntetisk testbilde")
        self.make_flac(
            "Artist/Album fra mappe/CD 1/01.flac",
            TITLE="Første disk",
            TRACKNUMBER="1",
        )
        self.make_flac(
            "Artist/Album fra mappe/CD 2/01.flac",
            TITLE="Andre disk",
            TRACKNUMBER="1",
        )

        batch = self.scan()
        self.assertEqual(self.apply(batch), 2)

        release = Release.objects.get()
        self.assertEqual(release.title, "Album fra mappe")
        self.assertEqual(
            list(
                release.tracks.order_by("disc_number").values_list(
                    "disc_number", flat=True
                )
            ),
            [1, 2],
        )
        cover = FileAsset.objects.get(release=release, role=FileAsset.Role.COVER_IMAGE)
        self.assertEqual(cover.filename, "cover.jpg")
        self.assertEqual(
            cover.locations.get().relative_path, "Artist/Album fra mappe/cover.jpg"
        )

    def test_path_must_stay_inside_configured_root(self):
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            with self.assertRaises(ValidationError):
                resolve_music_path("../utenfor")

    def test_invalid_controlled_radio_value_is_sent_to_control(self):
        self.make_flac(TITLE="Ugyldig Energy", RATING="9")
        item = self.scan().items.get()
        self.assertEqual(item.action, FlacIngestItem.Action.CONFLICT)
        self.assertIn("RATING må være et Energy-nivå fra 1 til 5.", item.messages)

    def test_invalid_isrc_is_an_actionable_file_conflict(self):
        self.make_flac(TITLE="Ugyldig kode", ISRC="not-an-isrc")

        item = self.scan().items.get()

        self.assertEqual(item.action, FlacIngestItem.Action.CONFLICT)
        self.assertIn("ugyldig verdi «not-an-isrc»", item.messages[0])

    def test_conflict_can_be_corrected_and_approved_without_changing_raw_tags(self):
        self.make_flac(
            TITLE="Kontroller meg",
            ARTIST="Uavklart artist",
            RATING="9",
            GENRE="Salme",
        )
        batch = self.scan()
        item = batch.items.get()
        self.assertEqual(item.action, FlacIngestItem.Action.CONFLICT)

        self.client.force_login(self.user)
        url = reverse("workbench:flac_ingest_review", args=[batch.pk, item.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Godkjenn og gjør klar")
        response = self.client.post(
            url,
            {
                "resolution": "new",
                "title": "Kontroller meg",
                "artists": "Uavklart artist",
                "isrc": "",
                "p7uuid": "",
                "genre": "Salme",
                "language": "nb",
                "energy": "4",
                "channels": "P7 Riks; P7 Kristen Riksradio",
                "target_audiences": "Voksen",
                "gender": "",
                "review_note": "Kontrollert mot P7s Energy-skala.",
            },
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.action, FlacIngestItem.Action.NEW)
        self.assertEqual(item.parsed_metadata["energy"], 4)
        self.assertNotIn("energy_invalid", item.parsed_metadata)
        self.assertEqual(item.raw_tags["rating"], ["9"])
        self.assertEqual(item.reviewed_by, self.user)
        self.assertIsNotNone(item.reviewed_at)
        self.assertEqual(item.review_note, "Kontrollert mot P7s Energy-skala.")

        self.assertEqual(self.apply(batch), 1)
        self.assertEqual(MusicLibraryEntry.objects.get().energy, 4)

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
        with override_settings(P7_MUSIC_ROOT=str(self.root), P7_ALLOW_FILE_WRITES=True):
            result = sync_file_asset(asset.pk)
        recording.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(recording.title, "Ny databaseverdi")
        self.assertEqual(result.result, FlacSyncLog.Result.MISSING)
        self.assertEqual(asset.sync_status, FileAsset.SyncStatus.MISSING)
        self.assertTrue(asset.sync_error)


class FlacMaintenanceTests(FlacTestMixin, TestCase):
    def import_files(self):
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            batch = scan_directory(relative_root=".", recursive=True, user=self.user)
            apply_batch(batch, user=self.user)
        return batch

    def test_missing_file_is_marked_without_deleting_catalogue(self):
        path = self.make_flac("album/spor.flac", TITLE="Flyttes kanskje")
        self.import_files()
        recording = Recording.objects.get()
        asset = FileAsset.objects.get()

        path.unlink()
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            scan_directory(relative_root=".", recursive=True, user=self.user)

        asset.refresh_from_db()
        location = asset.locations.get(is_current=True)
        self.assertEqual(location.status, FileLocation.Status.MISSING)
        self.assertEqual(asset.sync_status, FileAsset.SyncStatus.MISSING)
        self.assertTrue(Recording.objects.filter(pk=recording.pk).exists())
        self.assertTrue(MusicLibraryEntry.objects.filter(recording=recording).exists())

    def test_pcm_md5_reconciles_a_moved_file_without_new_recording(self):
        old_path = self.make_flac("fra/spor.flac", TITLE="Samme lyd")
        self.import_files()
        recording = Recording.objects.get()
        asset = FileAsset.objects.get()
        new_path = self.root / "til" / "spor.flac"
        new_path.parent.mkdir()
        old_path.rename(new_path)

        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            batch = scan_directory(
                relative_root=".", recursive=True, user=self.user, force_read=True
            )
            item = batch.items.get(relative_path="til/spor.flac")
            self.assertEqual(item.match_method, "audio_md5_move")
            apply_batch(batch, user=self.user)

        self.assertEqual(Recording.objects.count(), 1)
        asset.refresh_from_db()
        self.assertEqual(asset.recording_id, recording.pk)
        self.assertTrue(
            asset.locations.filter(
                relative_path="fra/spor.flac",
                status=FileLocation.Status.MOVED,
                is_current=False,
            ).exists()
        )
        self.assertTrue(
            asset.locations.filter(
                relative_path="til/spor.flac",
                status=FileLocation.Status.ACTIVE,
                is_current=True,
            ).exists()
        )

    def test_cleanup_requires_preview_and_preserves_recording_and_file_history(self):
        path = self.make_flac("borte.flac", TITLE="Kan regenereres")
        self.import_files()
        recording = Recording.objects.get()
        path.unlink()
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            scan_directory(relative_root=".", recursive=True, user=self.user)
            job = create_cleanup_preview(user=self.user)
            self.assertEqual(job.plan["stats"]["safe_to_remove"], 1)
            execute_cleanup(job, user=self.user)

        job.refresh_from_db()
        self.assertEqual(job.status, FlacMaintenanceJob.Status.COMPLETED)
        self.assertFalse(MusicLibraryEntry.objects.filter(recording=recording).exists())
        self.assertTrue(Recording.objects.filter(pk=recording.pk).exists())
        self.assertTrue(FileAsset.objects.filter(recording=recording).exists())
        self.assertEqual(FileLocation.objects.get().status, FileLocation.Status.MISSING)

    def test_explicit_remove_from_library_keeps_existing_file_and_recording(self):
        path = self.make_flac("behold-filen.flac", TITLE="Fjern medlemskap")
        before = path.read_bytes()
        self.import_files()
        recording = Recording.objects.get()

        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            job = create_cleanup_preview(user=self.user, recording_id=recording.pk)
            self.assertEqual(job.plan["stats"]["safe_to_remove"], 1)
            execute_cleanup(job, user=self.user)

        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(Recording.objects.filter(pk=recording.pk).exists())
        self.assertFalse(MusicLibraryEntry.objects.filter(recording=recording).exists())
        location = FileLocation.objects.get()
        self.assertEqual(location.status, FileLocation.Status.HISTORICAL)
        self.assertFalse(location.is_current)

    def test_managed_recording_is_protected_from_cleanup_and_rebuild(self):
        path = self.make_flac("forvaltet.flac", TITLE="Forvaltet")
        self.import_files()
        recording = Recording.objects.get()
        managed = ManagedRecording.objects.create(
            library_entry=recording.music_library_entry
        )
        owner = Party.objects.create(
            name="Dokumentert eier", kind=Party.Kind.ORGANIZATION
        )
        agreement = Agreement.objects.create(
            title="Bevart avtale",
            agreement_type=Agreement.Type.TRANSFER,
        )
        claim = RightsClaim.objects.create(
            recording=recording,
            right_type=RightsClaim.RightType.OWNERSHIP,
            rights_holder=owner,
            territory_mode=RightsClaim.TerritoryMode.WORLD,
            agreement=agreement,
        )
        path.unlink()
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            scan_directory(relative_root=".", recursive=True, user=self.user)
            cleanup = create_cleanup_preview(user=self.user)
            self.assertEqual(cleanup.plan["stats"]["safe_to_remove"], 0)
            self.assertEqual(cleanup.plan["stats"]["protected"], 1)
            rebuild = create_rebuild_preview(user=self.user)
            execute_rebuild(rebuild, user=self.user)

        self.assertTrue(ManagedRecording.objects.filter(pk=managed.pk).exists())
        self.assertTrue(MusicLibraryEntry.objects.filter(recording=recording).exists())
        self.assertTrue(RightsClaim.objects.filter(pk=claim.pk).exists())
        self.assertTrue(Agreement.objects.filter(pk=agreement.pk).exists())

    def test_manual_catalogue_change_blocks_regeneration(self):
        path = self.make_flac("vurdert.flac", TITLE="Opprinnelig")
        self.import_files()
        recording = Recording.objects.get()
        recording.version_designation = "Kontrollert versjon"
        recording.save(update_fields=("version_designation",))
        path.unlink()

        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            scan_directory(relative_root=".", recursive=True, user=self.user)
            job = create_cleanup_preview(user=self.user)

        self.assertEqual(job.plan["stats"]["safe_to_remove"], 0)
        self.assertIn(
            "Innspillingen har manuelt kataloginnhold eller er kontrollert.",
            job.plan["items"][0]["protected_reasons"],
        )

    def test_human_assertion_decision_blocks_regeneration(self):
        path = self.make_flac("avgjort.flac", TITLE="Vurdert kilde", GENRE="Pop")
        self.import_files()
        recording = Recording.objects.get()
        assertion = MetadataAssertion.objects.filter(
            entity_type=MetadataAssertion.EntityType.RECORDING,
            entity_uuid=recording.pk,
        ).first()
        AssertionDecision.objects.create(
            assertion=assertion,
            decision=AssertionDecision.Decision.CONFIRMED,
            decided_by=self.user,
            note="Kontrollert manuelt.",
        )
        path.unlink()

        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            scan_directory(relative_root=".", recursive=True, user=self.user)
            job = create_cleanup_preview(user=self.user)

        self.assertEqual(job.plan["stats"]["safe_to_remove"], 0)
        self.assertIn(
            "En kildepåstand om innspillingen er vurdert av en bruker.",
            job.plan["items"][0]["protected_reasons"],
        )

    def test_rebuild_without_p7uuid_is_semantically_stable_and_read_only(self):
        first = self.make_flac(
            "Album/01.flac",
            TITLE="Første",
            ALBUM="Prøvealbum",
            ALBUMARTIST="Prøveartist",
            TRACKNUMBER="1",
            GENRE="Pop",
            KANAL=["P7 Riks", "P7 Ung"],
        )
        second = self.make_flac(
            "Album/02.flac",
            TITLE="Andre",
            ALBUM="Prøvealbum",
            ALBUMARTIST="Prøveartist",
            TRACKNUMBER="2",
            GENRE="Vise",
        )
        before = {path.name: path.read_bytes() for path in (first, second)}
        self.import_files()
        recording_ids = set(Recording.objects.values_list("pk", flat=True))
        release_ids = set(Release.objects.values_list("pk", flat=True))

        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            job = create_rebuild_preview(user=self.user)
            self.assertEqual(job.plan["stats"]["regenerable_entries"], 2)
            execute_rebuild(job, user=self.user)

        self.assertEqual(
            set(Recording.objects.values_list("pk", flat=True)), recording_ids
        )
        self.assertEqual(set(Release.objects.values_list("pk", flat=True)), release_ids)
        self.assertEqual(MusicLibraryEntry.objects.count(), 2)
        self.assertEqual(ReleaseTrack.objects.count(), 2)
        self.assertEqual(
            set(
                MusicLibraryEntry.objects.get(
                    recording__title="Første"
                ).channels.values_list("name", flat=True)
            ),
            {"P7 Riks", "P7 Ung"},
        )
        self.assertEqual(
            {path.name: path.read_bytes() for path in (first, second)}, before
        )

    def test_all_maintenance_operations_leave_existing_p7uuid_file_unchanged(self):
        recording = Recording.objects.create(title="Eksisterende identitet")
        path = self.make_flac(
            "uuid.flac",
            TITLE="Eksisterende identitet",
            P7UUID=str(recording.pk),
            GENRE="Vise",
        )
        before = path.read_bytes()
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            first = scan_directory(relative_root=".", recursive=True, user=self.user)
            apply_batch(first, user=self.user)
            second = scan_directory(
                relative_root=".", recursive=True, user=self.user, force_read=True
            )
            apply_batch(second, user=self.user)
            cleanup = create_cleanup_preview(user=self.user)
            execute_cleanup(cleanup, user=self.user)
            rebuild = create_rebuild_preview(user=self.user)
            execute_rebuild(rebuild, user=self.user)

        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(Recording.objects.count(), 1)

    def test_sync_command_is_blocked_until_file_writes_are_explicitly_enabled(self):
        with self.assertRaisesMessage(CommandError, "Filskriving er deaktivert"):
            call_command("sync_flac_tags", stdout=StringIO())


class FlacWorkbenchPermissionTests(FlacTestMixin, TestCase):
    def test_maintenance_preview_and_execution_have_distinct_permissions(self):
        staff = get_user_model().objects.create_user(
            username="maintenance-staff", password="test", is_staff=True
        )
        preview_permission = Permission.objects.get(
            content_type__app_label="flac_ingest",
            codename="preview_library_cleanup",
        )
        staff.user_permissions.add(preview_permission)
        self.client.force_login(staff)

        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            self.assertEqual(
                self.client.get(reverse("workbench:library_maintenance")).status_code,
                200,
            )
            response = self.client.post(
                reverse("workbench:library_maintenance_preview"),
                {"kind": "cleanup", "relative_root": "."},
            )
        self.assertEqual(response.status_code, 302)
        job = FlacMaintenanceJob.objects.get()
        denied = self.client.post(
            reverse("workbench:library_maintenance_execute", args=[job.pk]),
            {"confirmed": "yes"},
        )
        self.assertEqual(denied.status_code, 403)
        job.refresh_from_db()
        self.assertEqual(job.status, FlacMaintenanceJob.Status.PREVIEW)

        staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="flac_ingest",
                codename="run_library_cleanup",
            )
        )
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            response = self.client.post(
                reverse("workbench:library_maintenance_execute", args=[job.pk]),
                {"confirmed": "yes"},
            )
        self.assertEqual(response.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.status, FlacMaintenanceJob.Status.COMPLETED)
        self.assertEqual(job.executed_by, staff)

    def test_rebuild_requires_explicit_backup_confirmation(self):
        self.client.force_login(self.user)
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            response = self.client.post(
                reverse("workbench:library_maintenance_preview"),
                {"kind": "rebuild", "relative_root": "."},
            )
        job = FlacMaintenanceJob.objects.get()
        self.assertEqual(response.status_code, 302)

        response = self.client.post(
            reverse("workbench:library_maintenance_execute", args=[job.pk]),
            {"confirmed": "yes"},
        )

        self.assertEqual(response.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.status, FlacMaintenanceJob.Status.PREVIEW)

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

    def test_review_endpoint_enforces_apply_permission_on_direct_post(self):
        self.make_flac(TITLE="Kontroll", RATING="9")
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            batch = scan_directory(relative_root=".", recursive=True, user=self.user)
        item = batch.items.get()
        url = reverse("workbench:flac_ingest_review", args=[batch.pk, item.pk])
        staff = get_user_model().objects.create_user(
            username="review-staff", password="test", is_staff=True
        )
        self.client.force_login(staff)
        response = self.client.post(
            url,
            {
                "resolution": "new",
                "title": "Kontroll",
                "energy": "3",
            },
        )
        self.assertEqual(response.status_code, 403)
        item.refresh_from_db()
        self.assertEqual(item.action, FlacIngestItem.Action.CONFLICT)
        self.assertIsNone(item.reviewed_at)

    def test_non_superuser_cannot_enable_uuid_recovery_in_service(self):
        staff = get_user_model().objects.create_user(
            username="uuid-staff", password="test", is_staff=True
        )
        self.make_flac(TITLE="Beskyttet UUID", P7UUID=str(uuid4()))

        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            with self.assertRaisesMessage(
                ValidationError, "Bare en administrator kan gjenopprette"
            ):
                scan_directory(
                    relative_root=".",
                    recursive=True,
                    user=staff,
                    allow_uuid_recovery=True,
                )
        self.assertEqual(FlacIngestBatch.objects.count(), 0)

    def test_selected_rescan_creates_a_new_batch_with_only_selected_files(self):
        self.make_flac("first.flac", TITLE="Første")
        self.make_flac("second.flac", TITLE="Andre")
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            batch = scan_directory(relative_root=".", recursive=True, user=self.user)
        selected = batch.items.get(relative_path="second.flac")
        self.client.force_login(self.user)

        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            response = self.client.post(
                reverse("workbench:flac_ingest_rescan", args=[batch.pk]),
                {"mode": "selected", "items": [str(selected.pk)]},
            )

        self.assertEqual(response.status_code, 302)
        new_batch = FlacIngestBatch.objects.exclude(pk=batch.pk).get()
        self.assertEqual(
            list(new_batch.items.values_list("relative_path", flat=True)),
            ["second.flac"],
        )

    def test_rescan_endpoint_enforces_permission_on_direct_post(self):
        self.make_flac(TITLE="Beskyttet")
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            batch = scan_directory(relative_root=".", recursive=True, user=self.user)
        staff = get_user_model().objects.create_user(
            username="rescan-staff", password="test", is_staff=True
        )
        self.client.force_login(staff)

        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            response = self.client.post(
                reverse("workbench:flac_ingest_rescan", args=[batch.pk]),
                {"mode": "all"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(FlacIngestBatch.objects.count(), 1)

    def test_legacy_flac_assertions_can_be_confirmed_without_other_sources(self):
        self.make_flac(TITLE="Tidligere innlest", GENRE="Gospel")
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            batch = scan_directory(relative_root=".", recursive=True, user=self.user)
            with self.captureOnCommitCallbacks(execute=True):
                apply_batch(batch, user=self.user)
        flac_assertion = MetadataAssertion.objects.filter(
            source_record__source_system__name="P7 radio-FLAC"
        ).first()
        flac_assertion.status = VerificationStatus.UNVERIFIED
        flac_assertion.save(update_fields=("status",))
        library_entry = Recording.objects.get().music_library_entry
        library_entry.verification_status = VerificationStatus.UNVERIFIED
        library_entry.save(update_fields=("verification_status",))

        manual_source = SourceSystem.objects.create(
            name="Manuell prøve", kind=SourceSystem.Kind.MANUAL
        )
        manual_record = SourceRecord.objects.create(
            source_system=manual_source, external_record_id="manual-1"
        )
        unrelated = MetadataAssertion.objects.create(
            source_record=manual_record,
            entity_type=MetadataAssertion.EntityType.RECORDING,
            entity_uuid=Recording.objects.get().pk,
            field_name="title",
            raw_value="Annen opplysning",
        )
        staff = get_user_model().objects.create_user(
            username="metadata-staff", password="test", is_staff=True
        )
        self.client.force_login(staff)
        denied = self.client.post(reverse("workbench:confirm_flac_metadata"))
        self.assertEqual(denied.status_code, 403)
        flac_assertion.refresh_from_db()
        self.assertEqual(flac_assertion.status, VerificationStatus.UNVERIFIED)
        self.client.force_login(self.user)

        response = self.client.post(reverse("workbench:confirm_flac_metadata"))

        self.assertRedirects(
            response, reverse("workbench:control") + "?type=uverifisert"
        )
        flac_assertion.refresh_from_db()
        unrelated.refresh_from_db()
        library_entry.refresh_from_db()
        self.assertEqual(flac_assertion.status, VerificationStatus.CONFIRMED)
        self.assertEqual(
            library_entry.verification_status, VerificationStatus.CONFIRMED
        )
        self.assertEqual(unrelated.status, VerificationStatus.UNVERIFIED)
        self.assertTrue(
            flac_assertion.decisions.filter(
                note__contains="tidligere anvendt FLAC-kildedata"
            ).exists()
        )

        library_entry.verification_status = VerificationStatus.UNVERIFIED
        library_entry.save(update_fields=("verification_status",))
        self.client.post(reverse("workbench:confirm_flac_metadata"))
        library_entry.refresh_from_db()
        self.assertEqual(
            library_entry.verification_status, VerificationStatus.CONFIRMED
        )

    def test_preview_can_filter_nonblocking_warnings(self):
        self.make_flac("warning.flac", TITLE="Med advarsel")
        self.make_flac("clean.flac", TITLE="Uten advarsel")
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            batch = scan_directory(relative_root=".", recursive=True, user=self.user)
        item = batch.items.get(relative_path="warning.flac")
        item.action = FlacIngestItem.Action.MATCHED
        item.messages = ["TITLE avviker fra databaseverdien"]
        item.save(update_fields=("action", "messages"))
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("workbench:flac_ingest_preview", args=[batch.pk]),
            {"status": "warning"},
        )

        self.assertContains(response, "Med advarsel")
        self.assertNotContains(response, "Uten advarsel")
        self.assertContains(response, "Advarsler")

    def test_selected_rescan_rejects_invalid_item_identifier(self):
        self.make_flac(TITLE="Gyldig fil")
        with override_settings(P7_MUSIC_ROOT=str(self.root)):
            batch = scan_directory(relative_root=".", recursive=True, user=self.user)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("workbench:flac_ingest_rescan", args=[batch.pk]),
            {"mode": "selected", "items": ["ikke-en-uuid"]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(FlacIngestBatch.objects.count(), 1)
