"""6B contracts: canonical catalogue, radio observations and writeback differ."""

from django.test import TestCase, override_settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from mutagen.flac import FLAC

from catalogue.authority import (
    annotate_file_authority,
    annotate_recording_authority,
    protecting_releases,
    recording_authority,
)
from catalogue.models import (
    ExternalIdentifier,
    Recording,
    Release,
    ReleaseTrack,
)
from flac_ingest.adapter import file_sha256
from flac_ingest.maintenance import (
    create_cleanup_preview,
    create_rebuild_preview,
    execute_cleanup,
    execute_rebuild,
)
from flac_ingest.models import FlacSyncLog
from flac_ingest.services import (
    apply_batch,
    scan_directory,
    sync_file_asset,
    preview_radio_file_split,
)
from flac_ingest.tests import FlacTestMixin
from managed_music.models import ManagedRecording, ManagedRelease
from media_assets.models import FileAsset, FileLocation
from music_library.models import MusicLibraryEntry
from rights.models import RightsClaim


class ObjectAuthorityTests(FlacTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.settings_override = override_settings(
            P7_MUSIC_ROOT=str(self.root)
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.path = self.make_flac(
            TITLE="Opprinnelig tittel",
            ARTIST="Opprinnelig artist",
            ALBUM="Utgivelse",
            CATALOGNUMBER="P7-6B",
            TRACKNUMBER="1",
            DISCNUMBER="1",
            ISRC="NO-P7T-26-00001",
            GENRE="Pop",
            ALBUMARTIST="Various Artists",
            RATING="3",
            KANAL="P7 Riks",
        )
        self.reread()
        self.asset = FileAsset.objects.get(role=FileAsset.Role.RADIO_FLAC)
        self.recording = self.asset.recording
        self.entry = self.recording.music_library_entry
        self.track = self.asset.release_track
        self.release = self.track.release

    def reread(self):
        batch = scan_directory(
            relative_root=".", recursive=True, user=self.user, force_read=True
        )
        apply_batch(batch, user=self.user)
        item = batch.items.get(
            relative_path=self.path.relative_to(self.root).as_posix()
        )
        self.assertIsNotNone(item.applied_at, item.messages)
        return item

    def tags(self, **changes):
        audio = FLAC(self.path)
        for key, value in changes.items():
            if value is None:
                audio.pop(key, None)
            else:
                audio[key] = value if isinstance(value, list) else str(value)
        audio.save()

    def manage_release(self, release=None, status="pending"):
        return ManagedRelease.objects.create(
            release=release or self.release,
            status=status,
            relationship=ManagedRelease.Relationship.MANAGED_CATALOGUE,
        )

    def test_ordinary_catalogue_and_release_creation_still_follow_flac(self):
        self.tags(
            TITLE="Ny tittel",
            ALBUM="Annen utgivelse",
            CATALOGNUMBER="NEW",
            TRACKNUMBER=2,
        )
        self.reread()
        self.recording.refresh_from_db()
        self.assertEqual(self.recording.title, "Ny tittel")
        self.assertTrue(
            ReleaseTrack.objects.filter(
                release__title="Annen utgivelse", recording=self.recording
            ).exists()
        )

    def test_all_membership_statuses_protect_canonical_identity_not_radio(
        self,
    ):
        for recording_managed, release_managed in (
            (True, False),
            (False, True),
            (True, True),
        ):
            for status in ("pending", "active", "inactive"):
                with self.subTest(
                    recording_managed=recording_managed,
                    release_managed=release_managed,
                    status=status,
                ):
                    ManagedRecording.objects.all().delete()
                    ManagedRelease.objects.all().delete()
                    if recording_managed:
                        ManagedRecording.objects.create(
                            library_entry=self.entry, status=status
                        )
                    if release_managed:
                        self.manage_release(status=status)
                    title, duration = (
                        self.recording.title,
                        self.recording.duration_ms,
                    )
                    credits = list(
                        self.recording.contributions.values_list(
                            "pk", flat=True
                        )
                    )
                    self.tags(
                        TITLE="FLAC-avvik",
                        ARTIST="En annen artist",
                        ISRC="NO-P7T-26-00999",
                        GENRE="Gospel",
                        LANGUAGE="nn",
                        RATING=5,
                        GENDER="female",
                        KANAL="P7 Ung",
                        TARGET="Voksen",
                    )
                    item = self.reread()
                    self.recording.refresh_from_db()
                    self.entry.refresh_from_db()
                    self.asset.refresh_from_db()
                    self.assertEqual(
                        (self.recording.title, self.recording.duration_ms),
                        (title, duration),
                    )
                    self.assertEqual(
                        list(
                            self.recording.contributions.values_list(
                                "pk", flat=True
                            )
                        ),
                        credits,
                    )
                    self.assertEqual(
                        self.recording.identifiers.get(
                            scheme="ISRC"
                        ).normalized_value,
                        "NOP7T2600001",
                    )
                    self.assertEqual(
                        (
                            self.entry.genre,
                            self.entry.language,
                            self.entry.energy,
                        ),
                        ("Gospel", "nn", 5),
                    )
                    self.assertEqual(
                        list(
                            self.entry.channels.values_list("name", flat=True)
                        ),
                        ["P7 Ung"],
                    )
                    self.assertEqual(
                        list(
                            self.entry.target_audiences.values_list(
                                "name", flat=True
                            )
                        ),
                        ["Voksen"],
                    )
                    self.assertEqual(self.asset.sha256, file_sha256(self.path))
                    self.assertEqual(self.entry.gender, "female")
                    self.assertEqual(
                        self.entry.rotation_suitability, "suitable"
                    )
                    self.assertTrue(item.source_record.raw_payload)
                    self.tags(
                        GENRE=None,
                        LANGUAGE=None,
                        RATING=None,
                        GENDER=None,
                        KANAL=None,
                        TARGET=None,
                    )
                    self.reread()
                    self.entry.refresh_from_db()
                    self.assertEqual(
                        (
                            self.entry.genre,
                            self.entry.language,
                            self.entry.energy,
                            self.entry.gender,
                        ),
                        ("", "", None, ""),
                    )
                    self.assertFalse(self.entry.channels.exists())
                    self.assertFalse(self.entry.target_audiences.exists())

    def test_managed_recording_still_allows_new_ordinary_release_and_cover(
        self,
    ):
        ManagedRecording.objects.create(library_entry=self.entry)
        album = self.root / "album"
        album.mkdir()
        self.path = self.path.rename(album / "radio.flac")
        (album / "cover.jpg").write_bytes(b"test cover")
        self.tags(ALBUM="Ny vanlig utgivelse", CATALOGNUMBER="P7-NEW")
        item = self.reread()
        self.assertEqual(item.release.title, "Ny vanlig utgivelse")
        self.assertTrue(
            item.release.file_assets.filter(
                role=FileAsset.Role.COVER_IMAGE
            ).exists()
        )
        self.assertIsNotNone(item.release_track_id)

    def test_existing_managed_track_matching_uses_position_not_sequence(self):
        self.track.sequence_number = 7
        self.track.save()
        self.manage_release()
        self.asset.release_track = None
        self.asset.save()
        item = self.reread()
        self.assertEqual(item.release_track_id, self.track.pk)
        self.assertEqual(self.release.tracks.count(), 1)

    def test_managed_track_mismatch_preserves_structure_and_observations(self):
        self.manage_release()
        self.track.title_override = "Tittel på utgivelse"
        self.track.duration_ms = 8000
        self.track.side = "A"
        self.track.save()
        baseline = ReleaseTrack.objects.filter(pk=self.track.pk).values().get()
        for changes in (
            {"TRACKNUMBER": 5},
            {"DISCNUMBER": 2},
            {"ALBUM": "Feil album"},
            {"TITLE": "Avvikende tittel"},
        ):
            with self.subTest(changes=changes):
                self.tags(**changes, GENRE="Endret radio")
                item = self.reread()
                self.assertEqual(item.release_track_id, self.track.pk)
                self.assertTrue(item.messages)
                self.assertEqual(
                    ReleaseTrack.objects.filter(pk=self.track.pk)
                    .values()
                    .get(),
                    baseline,
                )
                self.entry.refresh_from_db()
                self.assertEqual(self.entry.genre, "Endret radio")

    def test_missing_managed_track_does_not_block_radio_or_create_track(self):
        self.manage_release()
        self.asset.release_track = None
        self.asset.save()
        self.tags(TRACKNUMBER=7, GENRE="Nytt")
        item = self.reread()
        self.assertIsNone(item.release_track_id)
        self.assertEqual(self.release.tracks.count(), 1)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.sync_status, FileAsset.SyncStatus.CONFLICT)
        self.assertIn("ingen entydig", " ".join(item.messages))
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.genre, "Nytt")

    def test_different_recording_cannot_take_managed_track(self):
        self.manage_release()
        other = Recording.objects.create(title="Annen innspilling")
        self.make_flac(
            "other.flac",
            TITLE="Annen innspilling",
            P7UUID=str(other.pk),
            ALBUM=self.release.title,
            CATALOGNUMBER="P7-6B",
            TRACKNUMBER=1,
            DISCNUMBER=1,
        )
        batch = scan_directory(
            relative_root=".", recursive=True, user=self.user, force_read=True
        )
        apply_batch(batch, user=self.user)
        item = batch.items.get(relative_path="other.flac")
        self.assertIsNotNone(item.applied_at, item.messages)
        self.assertIsNone(item.release_track_id)
        self.track.refresh_from_db()
        self.assertEqual(self.track.recording_id, self.recording.pk)

    def test_release_only_is_derived_and_never_creates_rights(self):
        managed = self.manage_release()
        other = Release.objects.create(title="Samleutgivelse")
        self.manage_release(other)
        ReleaseTrack.objects.create(
            release=other, recording=self.recording, sequence_number=1
        )
        self.assertTrue(
            recording_authority(self.recording).managed_release_only
        )
        self.assertFalse(recording_authority(self.recording).writeback)
        self.assertEqual(
            set(protecting_releases(self.recording)), {self.release, other}
        )
        self.assertFalse(ManagedRecording.objects.exists())
        self.assertFalse(RightsClaim.objects.exists())
        membership = ManagedRecording.objects.create(library_entry=self.entry)
        self.assertFalse(
            recording_authority(self.recording).managed_release_only
        )
        membership.delete()
        managed.delete()
        other.tracks.all().delete()
        self.assertFalse(recording_authority(self.recording).protected)

    def test_authority_queries_and_file_context_do_not_have_per_row_reads(
        self,
    ):
        self.manage_release()
        bare = FileAsset.objects.create(
            recording=self.recording,
            filename="other.flac",
            role=FileAsset.Role.RADIO_FLAC,
        )
        for index in range(12):
            Recording.objects.create(title=f"Ordinær {index}")
        with self.assertNumQueries(1):
            rows = list(annotate_recording_authority(Recording.objects.all()))
            self.assertEqual(sum(row.authority_via_release for row in rows), 1)
        with self.assertNumQueries(1):
            files = {
                row.pk: row.on_managed_release_track
                for row in annotate_file_authority(FileAsset.objects.all())
            }
        self.assertTrue(files[self.asset.pk])
        self.assertFalse(files[bare.pk])

    def test_managed_cover_discovery_blocked_existing_cover_observed(self):
        self.manage_release()
        cover_path = self.root / "cover.jpg"
        cover_path.write_bytes(b"new discovered cover")
        self.reread()
        self.assertFalse(
            self.release.file_assets.filter(
                role=FileAsset.Role.COVER_IMAGE
            ).exists()
        )
        cover = FileAsset.objects.create(
            release=self.release,
            filename="cover.jpg",
            role=FileAsset.Role.COVER_IMAGE,
        )
        location = FileLocation.objects.create(
            asset=cover,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="cover.jpg",
        )
        self.reread()
        cover.refresh_from_db()
        self.assertEqual(cover.sha256, file_sha256(cover_path))
        self.assertEqual(cover.size_bytes, cover_path.stat().st_size)
        location.storage_type = FileLocation.StorageType.LOCAL
        location.save()
        cover_path.write_bytes(b"updated registered cover")
        self.reread()
        cover.refresh_from_db()
        self.assertEqual(cover.sha256, file_sha256(cover_path))
        cover_path.unlink()
        self.reread()
        location.refresh_from_db()
        self.assertEqual(location.status, FileLocation.Status.MISSING)
        self.assertEqual(cover.release_id, self.release.pk)

    @override_settings(P7_ALLOW_FILE_WRITES=True)
    def test_recording_writeback_leaves_ordinary_release_and_radio_tags_untouched(
        self,
    ):
        ManagedRecording.objects.create(library_entry=self.entry)
        self.recording.title = "DB-tittel"
        self.recording.save()
        self.tags(ALBUM="Behold album", TRACKNUMBER=9)
        before = dict(FLAC(self.path).tags)
        log = sync_file_asset(self.asset.pk)
        self.assertEqual(log.result, FlacSyncLog.Result.SUCCESS)
        after = dict(FLAC(self.path).tags)
        self.assertEqual(after["title"], ["DB-tittel"])
        for tag in (
            "album",
            "tracknumber",
            "discnumber",
            "catalognumber",
            "albumartist",
            "genre",
            "rating",
            "kanal",
        ):
            self.assertEqual(after[tag], before[tag])
        self.assertNotIn("ALBUM", log.written_tags)

    @override_settings(P7_ALLOW_FILE_WRITES=True)
    def test_release_writeback_does_not_write_recording_or_album_artist(self):
        self.manage_release()
        self.tags(
            TITLE="Behold filtittel",
            ARTIST="Behold artist",
            ALBUM="Feil album",
            ALBUMARTIST="Various Artists",
        )
        log = sync_file_asset(self.asset.pk)
        self.assertEqual(log.result, FlacSyncLog.Result.SUCCESS)
        after = FLAC(self.path)
        self.assertEqual(after["album"], [self.release.title])
        self.assertEqual(after["title"], ["Behold filtittel"])
        self.assertEqual(after["artist"], ["Behold artist"])
        self.assertEqual(after["albumartist"], ["Various Artists"])
        self.assertEqual(after["p7uuid"], [str(self.recording.pk)])
        self.assertNotIn("TITLE", log.written_tags)
        self.asset.release_track = None
        self.asset.save()
        self.assertEqual(
            sync_file_asset(self.asset.pk).result, FlacSyncLog.Result.CONFLICT
        )
        self.assertEqual(
            sync_file_asset(self.asset.pk, p7uuid_only=True).result,
            FlacSyncLog.Result.SUCCESS,
        )

    @override_settings(P7_ALLOW_FILE_WRITES=False)
    def test_write_gate_still_blocks_release_writeback(self):
        self.manage_release()
        before = self.path.read_bytes()
        self.assertIsNone(sync_file_asset(self.asset.pk))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(FlacSyncLog.objects.exists())

    def test_dirty_marking_is_scoped_to_actual_release_file_context(self):
        bare = FileAsset.objects.create(
            recording=self.recording,
            filename="bare.flac",
            role=FileAsset.Role.RADIO_FLAC,
        )
        self.manage_release()
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.sync_status, FileAsset.SyncStatus.PENDING)
        for asset in FileAsset.objects.all():
            asset.sync_status = FileAsset.SyncStatus.SYNCED
            asset.save()
        self.recording.title = "Beskyttet via utgivelse"
        self.recording.save()
        self.assertFalse(
            FileAsset.objects.filter(sync_status="pending").exists()
        )
        self.release.title = "Ny utgivelsestittel"
        self.release.save()
        self.asset.refresh_from_db()
        bare.refresh_from_db()
        self.assertEqual(self.asset.sync_status, FileAsset.SyncStatus.PENDING)
        self.assertEqual(bare.sync_status, FileAsset.SyncStatus.SYNCED)
        for asset in FileAsset.objects.all():
            asset.sync_status = FileAsset.SyncStatus.SYNCED
            asset.save()
        ExternalIdentifier.objects.create(
            release=self.release, scheme="EAN", value="4006381333931"
        )
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.sync_status, FileAsset.SyncStatus.PENDING)

    def test_rebuild_refreshes_radio_without_resetting_protected_catalogue(
        self,
    ):
        self.manage_release()
        for managed in (False, True):
            with self.subTest(managed=managed):
                if managed:
                    ManagedRecording.objects.create(library_entry=self.entry)
                self.tags(TITLE="Ikke overskriv", GENRE="Ny radioverdi")
                job = create_rebuild_preview(user=self.user)
                self.assertEqual(
                    job.plan["stats"]["radio_metadata_refresh"], 1
                )
                execute_rebuild(job, user=self.user)
                self.recording.refresh_from_db()
                self.entry.refresh_from_db()
                self.assertEqual(self.recording.title, "Opprinnelig tittel")
                self.assertEqual(self.entry.genre, "Ny radioverdi")
                self.assertTrue(
                    ReleaseTrack.objects.filter(pk=self.track.pk).exists()
                )
                self.assertTrue(self.recording.contributions.exists())

    def test_refresh_preview_does_not_authorize_reset_after_membership_removed(
        self,
    ):
        membership = self.manage_release()
        job = create_rebuild_preview(user=self.user)
        membership.delete()
        self.tags(TITLE="Ikke godkjent endring")
        execute_rebuild(job, user=self.user)
        self.recording.refresh_from_db()
        self.assertEqual(self.recording.title, "Opprinnelig tittel")

    def test_cleanup_release_only_library_entry_keeps_catalogue(self):
        self.manage_release()
        original_file = self.path.read_bytes()
        self.path.unlink()
        job = create_cleanup_preview(user=self.user)
        execute_cleanup(job, user=self.user)
        self.assertFalse(
            MusicLibraryEntry.objects.filter(pk=self.entry.pk).exists()
        )
        self.assertTrue(
            ReleaseTrack.objects.filter(
                pk=self.track.pk, recording=self.recording
            ).exists()
        )
        self.assertTrue(
            ManagedRelease.objects.filter(release=self.release).exists()
        )
        self.path.write_bytes(original_file)
        self.tags(
            TITLE="Ikke regenerer katalogtittel", GENRE="Tilbake fra FLAC"
        )
        execute_rebuild(create_rebuild_preview(user=self.user), user=self.user)
        self.recording.refresh_from_db()
        self.assertEqual(self.recording.title, "Opprinnelig tittel")
        self.assertEqual(
            MusicLibraryEntry.objects.get(recording=self.recording).genre,
            "Tilbake fra FLAC",
        )

    def test_split_does_not_relink_a_managed_release_track(self):
        self.manage_release()
        FileAsset.objects.create(
            recording=self.recording,
            filename="other.flac",
            role=FileAsset.Role.RADIO_FLAC,
        )
        self.assertTrue(
            preview_radio_file_split(asset_id=self.asset.pk)["blocked_reason"]
        )

    def test_library_filter_inspector_and_file_badge_use_derived_category(
        self,
    ):
        self.manage_release()
        ordinary = Recording.objects.create(title="Ordinær")
        MusicLibraryEntry.objects.create(recording=ordinary)
        self.client.force_login(self.user)
        url = reverse("gui_v2:music_library")
        response = self.client.get(url, {"managed": "release_only"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row.pk for row in response.context["page"]], [self.entry.pk]
        )
        self.assertContains(response, "Katalogvern fra utgivelser")
        self.assertContains(response, "ikke selvstendig Recording-forvaltning")
        self.assertEqual(self.client.get(url).status_code, 302)
        response = self.client.get(
            reverse("gui_v2:recording_files", args=[self.recording.pk])
        )
        self.assertContains(response, "Spor på forvaltet utgivelse")
        ManagedRecording.objects.create(library_entry=self.entry)
        response = self.client.get(url, {"managed": "release_only"})
        self.assertEqual(len(response.context["page"]), 0)

    @override_settings(P7_ALLOW_FILE_WRITES=True)
    def test_both_authorities_write_only_their_subsets_and_are_rechecked(self):
        membership = ManagedRecording.objects.create(library_entry=self.entry)
        release_membership = self.manage_release()
        self.tags(TITLE="File", ALBUM="File release", GENRE="Radioverdi")
        log = sync_file_asset(self.asset.pk)
        self.assertEqual(log.result, FlacSyncLog.Result.SUCCESS)
        self.assertEqual(FLAC(self.path)["title"], [self.recording.title])
        self.assertEqual(FLAC(self.path)["album"], [self.release.title])
        self.assertEqual(FLAC(self.path)["genre"], ["Radioverdi"])
        membership.delete()
        release_membership.delete()
        before = self.path.read_bytes()
        self.assertEqual(
            sync_file_asset(self.asset.pk).result, FlacSyncLog.Result.CONFLICT
        )
        self.assertEqual(self.path.read_bytes(), before)

    def test_ordinary_release_changes_do_not_queue_recording_only_writeback(
        self,
    ):
        ManagedRecording.objects.create(library_entry=self.entry)
        self.asset.refresh_from_db()
        self.asset.sync_status = FileAsset.SyncStatus.SYNCED
        self.asset.save()
        self.release.title = "Ny vanlig utgivelsestittel"
        self.release.save()
        self.track.title_override = "Sporvariant"
        self.track.save()
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.sync_status, FileAsset.SyncStatus.SYNCED)

    def test_library_authority_filter_has_constant_query_count(self):
        self.manage_release()
        self.client.force_login(self.user)
        url = reverse("gui_v2:music_library")
        parameters = {
            "managed": "release_only",
            "selected": str(self.entry.pk),
        }
        # Warm session and permission caches before measuring identical requests.
        self.client.get(url, parameters)
        with CaptureQueriesContext(connection) as before:
            self.assertEqual(self.client.get(url, parameters).status_code, 200)
        for index in range(12):
            recording = Recording.objects.create(title=f"Ekstra {index}")
            MusicLibraryEntry.objects.create(recording=recording)
            ReleaseTrack.objects.create(
                release=self.release,
                recording=recording,
                sequence_number=index + 2000,
            )
        with CaptureQueriesContext(connection) as after:
            response = self.client.get(url, parameters)
            self.assertEqual(len(response.context["page"]), 13)
        self.assertLessEqual(len(after), len(before) + 1)

    def test_protecting_release_details_require_management_permission(self):
        from django.contrib.auth.models import Permission

        self.manage_release()
        self.user.is_superuser = False
        self.user.save()
        self.user.user_permissions.add(
            Permission.objects.get(codename="view_musiclibraryentry")
        )
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("gui_v2:music_library"), {"managed": "release_only"}
        )
        self.assertContains(response, "Via forvaltet utgivelse")
        self.assertNotContains(response, "Katalogvern fra utgivelser")
        self.assertEqual(
            self.client.get(
                reverse("gui_v2:recording_files", args=[self.recording.pk])
            ).status_code,
            403,
        )
