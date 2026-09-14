import tempfile
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from mutagen.flac import FLAC

from catalogue.models import ExternalIdentifier, Recording, RecordingContribution, Release, ReleaseTrack
from managed_music.models import ManagedRecording
from media_assets.models import FileAsset, FileLocation
from music_library.models import Channel, MusicLibraryChannel, MusicLibraryEntry
from parties.models import Party
from rights.models import RightsClaim, RightsConfiguration


class GuiV2WorkspaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="prototype-user", password="test-password")
        self.release = Release.objects.create(title="Testutgivelse")
        self.recording = Recording.objects.create(title="Eksisterende innspilling")
        self.entry = MusicLibraryEntry.objects.create(recording=self.recording, genre="Pop", energy=3)
        self.channel = Channel.objects.create(code="p7-test", name="P7 Test")
        MusicLibraryChannel.objects.create(library_entry=self.entry, channel=self.channel)

    def _superuser(self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)

    def test_login_redirect_keeps_requested_v2_url(self):
        requested = reverse("gui_v2:music_library")
        response = self.client.get(requested)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlparse(response.url).query)["next"], [requested])

    def test_sections_enforce_view_permissions(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("gui_v2:home")).status_code, 200)
        self.assertEqual(self.client.get(reverse("gui_v2:music_library")).status_code, 403)
        self.assertEqual(self.client.get(reverse("gui_v2:release_list")).status_code, 403)
        self.user.user_permissions.add(
            Permission.objects.get(content_type__app_label="music_library", codename="view_musiclibraryentry"),
            Permission.objects.get(content_type__app_label="catalogue", codename="view_release"),
        )
        self.client.force_login(get_user_model().objects.get(pk=self.user.pk))
        self.assertContains(self.client.get(reverse("gui_v2:music_library")), "Eksisterende innspilling")
        self.assertContains(self.client.get(reverse("gui_v2:release_list")), "Testutgivelse")

    def test_library_filter_any_all_and_selection(self):
        other = Channel.objects.create(code="annen", name="Annen")
        self._superuser()
        response = self.client.get(reverse("gui_v2:music_library"), {"channels": [self.channel.pk, other.pk], "channel_mode": "any", "selected": self.entry.pk})
        self.assertContains(response, "Eksisterende innspilling")
        response = self.client.get(reverse("gui_v2:music_library"), {"channels": [self.channel.pk, other.pk], "channel_mode": "all"})
        self.assertContains(response, "Ingen innspillinger passer")

    def test_library_and_grid_render_keyboard_workbench(self):
        self._superuser()
        self.entry.rotation_suitability = MusicLibraryEntry.RotationSuitability.NOT_SUITABLE
        self.entry.save(update_fields=("rotation_suitability",))
        library = self.client.get(reverse("gui_v2:music_library"), {"genre": "Pop"})
        self.assertContains(library, "Sjanger: Pop")
        self.assertContains(library, "data-row-href")
        self.assertContains(library, "data-library-row")
        self.assertContains(library, "Krever oppfølging")
        self.assertContains(library, "Ikke rotasjonsverdig")
        self.assertNotContains(library, 'id="v2-inspector"')
        grid = self.client.get(reverse("gui_v2:release_detail", args=[self.release.pk]))
        self.assertContains(grid, 'role="grid"')
        self.assertContains(grid, 'data-field="recording_title"')

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_release_can_be_created_before_tracks_are_registered(self):
        self._superuser()
        response = self.client.post(
            reverse("gui_v2:release_list"),
            {
                "release-title": "Ny testutgivelse",
                "release-release_type": "",
                "release-release_date": "",
                "release-release_year": "",
                "release-label": "",
                "release-catalogue_number": "",
                "release-verification_status": "unverified",
                "release-notes": "",
                "release-barcode": "",
            },
        )
        created = Release.objects.get(title="Ny testutgivelse")
        self.assertRedirects(
            response,
            reverse("gui_v2:release_detail", args=[created.pk]),
        )
        self.assertFalse(created.tracks.exists())

    @override_settings(GUI_V2_WRITES_ENABLED=False)
    def test_release_creation_is_blocked_in_read_only_mode(self):
        self._superuser()
        response = self.client.post(
            reverse("gui_v2:release_list"),
            {"release-title": "Skal ikke opprettes"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Release.objects.filter(title="Skal ikke opprettes").exists())

    def test_release_tabs_integrate_cover_details_files_and_rights(self):
        self._superuser()
        cover = FileAsset.objects.create(
            release=self.release,
            filename="cover.png",
            role=FileAsset.Role.COVER_IMAGE,
        )
        FileLocation.objects.create(
            asset=cover,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="covers/cover.png",
            status=FileLocation.Status.ACTIVE,
        )
        detail_url = reverse("gui_v2:release_detail", args=[self.release.pk])
        response = self.client.get(detail_url)
        self.assertContains(response, reverse("workbench:cover_image", args=[cover.pk]))
        for label in ("Sporliste", "Utgivelsesdetaljer", "Filer og kilder", "Rettigheter"):
            self.assertContains(response, label)
        self.assertNotContains(response, reverse("workbench:release", args=[self.release.pk]))
        details = self.client.get(detail_url, {"tab": "details"})
        self.assertContains(details, 'name="release-title"')
        files = self.client.get(detail_url, {"tab": "files"})
        self.assertContains(files, "cover.png")
        rights = self.client.get(detail_url, {"tab": "rights"})
        self.assertContains(rights, "Registrer rettigheter for valgte innspillinger")
        self.assertContains(rights, 'id="id_rights-recordings"')

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_integrated_rights_tab_creates_claim_on_selected_recording(self):
        self._superuser()
        local = Party.objects.create(name="Lokal testorganisasjon", kind=Party.Kind.ORGANIZATION)
        RightsConfiguration.objects.create(local_organization=local)
        ReleaseTrack.objects.create(
            release=self.release, recording=self.recording, sequence_number=1
        )
        response = self.client.post(
            reverse("gui_v2:release_detail", args=[self.release.pk]),
            {
                "action": "rights", "tab": "rights",
                "rights-recordings": str(self.recording.pk),
                "rights-right_type": RightsClaim.RightType.DISTRIBUTION,
                "rights-rights_holder": str(local.pk), "rights-grantor": "",
                "rights-share": "", "rights-territory_mode": RightsClaim.TerritoryMode.WORLD,
                "rights-valid_from": "", "rights-valid_until": "",
                "rights-evidence_strength": RightsClaim.EvidenceStrength.NOT_ASSESSED,
                "rights-source_record": "", "rights-agreement": "", "rights-notes": "Testgrunnlag",
            },
        )
        self.assertRedirects(
            response,
            f"{reverse('gui_v2:release_detail', args=[self.release.pk])}?tab=rights",
        )
        claim = RightsClaim.objects.get()
        self.assertEqual(claim.recording, self.recording)
        self.assertEqual(claim.right_type, RightsClaim.RightType.DISTRIBUTION)

    def test_recording_search_matches_credited_artist(self):
        RecordingContribution.objects.create(
            recording=self.recording,
            role=RecordingContribution.Role.PRIMARY,
            credited_as="Signalverket",
        )
        self._superuser()
        response = self.client.get(reverse("gui_v2:recording_search"), {"q": "Signal"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["id"], str(self.recording.pk))

    @override_settings(GUI_V2_WRITES_ENABLED=False)
    def test_writes_are_blocked_outside_isolated_mode(self):
        self._superuser()
        response = self.client.post(reverse("gui_v2:release_detail", args=[self.release.pk]), {"tracks-TOTAL_FORMS": "0", "tracks-INITIAL_FORMS": "0", "tracks-MIN_NUM_FORMS": "0", "tracks-MAX_NUM_FORMS": "1000"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ReleaseTrack.objects.exists())

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_grid_creates_track_atomically_without_scheduling_writeback(self):
        self._superuser()
        asset = FileAsset.objects.create(recording=self.recording, filename="sentinel.flac", role=FileAsset.Role.RADIO_FLAC, sync_status=FileAsset.SyncStatus.SYNCED)
        payload = {
            "tracks-TOTAL_FORMS": "1", "tracks-INITIAL_FORMS": "0", "tracks-MIN_NUM_FORMS": "0", "tracks-MAX_NUM_FORMS": "1000",
            "tracks-0-sequence_number": "1", "tracks-0-disc_number": "1", "tracks-0-side": "A", "tracks-0-track_number": "1",
            "tracks-0-title_override": "Utgivelsestittel", "tracks-0-recording_id": str(self.recording.pk),
            "tracks-0-recording_title": self.recording.title, "tracks-0-artists": "", "tracks-0-composers": "", "tracks-0-lyricists": "", "tracks-0-duration": "03:12", "tracks-0-isrc": "",
            "tracks-0-arrangers": "",
        }
        response = self.client.post(reverse("gui_v2:release_detail", args=[self.release.pk]), payload)
        self.assertEqual(response.status_code, 302)
        track = ReleaseTrack.objects.get()
        self.assertEqual(track.recording, self.recording)
        self.assertEqual(track.duration_ms, 192000)
        asset.refresh_from_db()
        self.assertEqual(asset.sync_status, FileAsset.SyncStatus.SYNCED)

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_grid_saves_arranger_as_recording_credit(self):
        self._superuser()
        payload = {
            "tracks-TOTAL_FORMS": "1", "tracks-INITIAL_FORMS": "0",
            "tracks-MIN_NUM_FORMS": "0", "tracks-MAX_NUM_FORMS": "1000",
            "tracks-0-sequence_number": "1", "tracks-0-recording_id": str(self.recording.pk),
            "tracks-0-recording_title": self.recording.title, "tracks-0-arrangers": "Ada Arrange",
            "tracks-0-update_shared_recording": "on",
        }
        response = self.client.post(reverse("gui_v2:release_detail", args=[self.release.pk]), payload)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.recording.contributions.filter(role=RecordingContribution.Role.ARRANGER, credited_as="Ada Arrange").exists())

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_release_metadata_and_barcode_are_edited_in_same_workspace(self):
        self._superuser()
        response = self.client.post(
            reverse("gui_v2:release_detail", args=[self.release.pk]),
            {
                "action": "release",
                "release-title": "Korrigert utgivelse",
                "release-release_type": Release.Type.CD,
                "release-release_year": "1998",
                "release-catalogue_number": "P7-101",
                "release-verification_status": "confirmed",
                "release-notes": "Kontrollert mot cover.",
                "release-barcode": "036000291452",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.release.refresh_from_db()
        self.assertEqual((self.release.title, self.release.catalogue_number), ("Korrigert utgivelse", "P7-101"))
        identifier = self.release.identifiers.get()
        self.assertEqual((identifier.scheme, identifier.normalized_value), (ExternalIdentifier.Scheme.UPC, "036000291452"))
        identifier_id = identifier.pk
        response = self.client.post(
            reverse("gui_v2:release_detail", args=[self.release.pk]),
            {
                "action": "release", "release-title": self.release.title,
                "release-release_type": Release.Type.CD,
                "release-release_year": "1998", "release-catalogue_number": "P7-101",
                "release-verification_status": "confirmed", "release-notes": "",
                "release-barcode": "0 36000 29145 2",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.release.identifiers.get().pk, identifier_id)

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_release_metadata_write_requires_change_permission(self):
        self.user.user_permissions.add(
            Permission.objects.get(content_type__app_label="catalogue", codename="view_release")
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("gui_v2:release_detail", args=[self.release.pk]),
            {"action": "release", "release-title": "Ikke tillatt"},
        )
        self.assertEqual(response.status_code, 403)
        self.release.refresh_from_db()
        self.assertEqual(self.release.title, "Testutgivelse")

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_track_with_linked_file_has_human_deletion_error(self):
        self._superuser()
        track = ReleaseTrack.objects.create(
            release=self.release, recording=self.recording, sequence_number=1
        )
        FileAsset.objects.create(
            recording=self.recording,
            release_track=track,
            filename="03 Amazing Grace.flac",
            role=FileAsset.Role.RADIO_FLAC,
        )
        response = self.client.post(
            reverse("gui_v2:release_detail", args=[self.release.pk]),
            {
                "action": "tracks",
                "tracks-TOTAL_FORMS": "1",
                "tracks-INITIAL_FORMS": "0",
                "tracks-MIN_NUM_FORMS": "0",
                "tracks-MAX_NUM_FORMS": "1000",
                "tracks-0-track_id": str(track.pk),
                "tracks-0-sequence_number": "1",
                "tracks-0-remove": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "03 Amazing Grace.flac")
        self.assertContains(response, "Koble filen til riktig spor")
        self.assertTrue(ReleaseTrack.objects.filter(pk=track.pk).exists())

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_invalid_grid_preserves_entered_data_and_creates_nothing(self):
        self._superuser()
        payload = {"tracks-TOTAL_FORMS": "1", "tracks-INITIAL_FORMS": "0", "tracks-MIN_NUM_FORMS": "0", "tracks-MAX_NUM_FORMS": "1000", "tracks-0-sequence_number": "1", "tracks-0-recording_title": "Ny testinnspilling", "tracks-0-duration": "3:99"}
        response = self.client.post(reverse("gui_v2:release_detail", args=[self.release.pk]), payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ny testinnspilling")
        self.assertContains(response, "Bruk mm:ss")
        self.assertFalse(ReleaseTrack.objects.exists())
        self.assertEqual(Recording.objects.count(), 1)

    def test_csrf_is_required_for_direct_write(self):
        self._superuser()
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(strict.post(reverse("gui_v2:release_detail", args=[self.release.pk]), {}).status_code, 403)

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_catalogue_viewer_cannot_rescan_by_direct_post(self):
        self.user.user_permissions.add(
            Permission.objects.get(content_type__app_label="music_library", codename="view_musiclibraryentry")
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("gui_v2:rescan_library_file", args=[self.entry.pk, uuid4()])
        )
        self.assertEqual(response.status_code, 403)

    def test_existing_workbench_and_prototype_do_not_touch_files(self):
        self._superuser()
        with tempfile.TemporaryDirectory() as folder:
            sentinel = Path(folder) / "skal-ikke-skrives.flac"
            sentinel.write_bytes(b"read-only prototype sentinel")
            before = (sentinel.read_bytes(), sentinel.stat().st_mtime_ns)
            with override_settings(P7_MUSIC_ROOT=folder, P7_NAS_ROOT=folder, GUI_V2_WRITES_ENABLED=False):
                for name in ("home", "workbench:library", "workbench:releases", "gui_v2:home", "gui_v2:music_library", "gui_v2:release_list"):
                    self.assertEqual(self.client.get(reverse(name)).status_code, 200)
            self.assertEqual((sentinel.read_bytes(), sentinel.stat().st_mtime_ns), before)

    def _radio_file(self, root, recording, *, title, genre, energy):
        relative_path = "radio/test.flac"
        path = Path(root) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__).parents[1] / "flac_ingest" / "test_fixtures" / "silence.flac", path)
        audio = FLAC(path)
        audio["TITLE"] = title
        audio["GENRE"] = genre
        audio["RATING"] = str(energy)
        audio["P7UUID"] = str(recording.pk)
        audio.save()
        asset = FileAsset.objects.create(
            recording=recording, filename=path.name, role=FileAsset.Role.RADIO_FLAC,
            sha256="0" * 64, sync_status=FileAsset.SyncStatus.SYNCED,
        )
        FileLocation.objects.create(
            asset=asset, storage_type=FileLocation.StorageType.NAS,
            relative_path=relative_path, status=FileLocation.Status.ACTIVE, is_current=True,
        )
        return asset

    def test_onetagger_copy_value_is_plain_containing_folder_path(self):
        self._superuser()
        with tempfile.TemporaryDirectory() as root, override_settings(P7_MUSIC_ROOT=root):
            self._radio_file(
                root, self.recording, title=self.recording.title, genre="Pop", energy=3
            )
            response = self.client.get(
                reverse("gui_v2:music_library"), {"selected": self.entry.pk}
            )
            selected = response.context["selected"]
            copied_path = selected.radio_files[0].current_locations[0].onetagger_path
            self.assertEqual(copied_path, str((Path(root) / "radio").resolve()))
            self.assertNotIn("WindowsPath(", copied_path)
            self.assertNotIn("(", copied_path)
            self.assertContains(response, "Kopier mappe til OneTagger")

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_single_file_rescan_updates_unmanaged_catalogue_and_radio(self):
        self._superuser()
        with tempfile.TemporaryDirectory() as root, override_settings(P7_MUSIC_ROOT=root):
            asset = self._radio_file(root, self.recording, title="Tittel fra FLAC", genre="Rock", energy=5)
            path = Path(root) / "radio/test.flac"
            audio = FLAC(path)
            audio["KANAL"] = "P7 Test"
            audio["COMMENT"] = "TXXX:Rotasjon - Ikke Rotasjonsverdig"
            audio.save()
            response = self.client.post(
                reverse("gui_v2:rescan_library_file", args=[self.entry.pk, asset.pk]),
                {"return": reverse("gui_v2:music_library")},
            )
            self.assertEqual(response.status_code, 302)
            self.recording.refresh_from_db(); self.entry.refresh_from_db()
            self.assertEqual(self.recording.title, "Tittel fra FLAC")
            self.assertEqual((self.entry.genre, self.entry.energy), ("Rock", 5))
            self.assertEqual(
                self.entry.rotation_suitability,
                MusicLibraryEntry.RotationSuitability.NOT_SUITABLE,
            )
            self.assertEqual(list(self.entry.channels.all()), [self.channel])

            # Removing authoritative radio tags in OneTagger/FLAC removes the
            # corresponding values from the music library on the next re-read.
            audio = FLAC(path)
            del audio["KANAL"]
            del audio["GENRE"]
            del audio["COMMENT"]
            audio.save()
            response = self.client.post(
                reverse("gui_v2:rescan_library_file", args=[self.entry.pk, asset.pk]),
                {"return": reverse("gui_v2:music_library")},
            )
            self.assertEqual(response.status_code, 302)
            self.entry.refresh_from_db()
            self.assertEqual(self.entry.genre, "")
            self.assertEqual(self.entry.rotation_suitability, "")
            self.assertFalse(self.entry.channels.exists())

            # A later explicit re-read must also repair stale relations left by
            # an older importer, even when the FLAC itself is now unchanged.
            MusicLibraryChannel.objects.create(
                library_entry=self.entry, channel=self.channel
            )
            response = self.client.post(
                reverse("gui_v2:rescan_library_file", args=[self.entry.pk, asset.pk]),
                {"return": reverse("gui_v2:music_library")},
            )
            self.assertEqual(response.status_code, 302)
            self.assertFalse(self.entry.channels.exists())

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_single_file_rescan_preserves_managed_catalogue_but_updates_radio(self):
        self._superuser()
        ManagedRecording.objects.create(library_entry=self.entry)
        with tempfile.TemporaryDirectory() as root, override_settings(P7_MUSIC_ROOT=root):
            asset = self._radio_file(root, self.recording, title="Skal ikke brukes", genre="Ny radiosjanger", energy=4)
            rights_before = ManagedRecording.objects.count()
            response = self.client.post(
                reverse("gui_v2:rescan_library_file", args=[self.entry.pk, asset.pk]),
                {"return": reverse("gui_v2:music_library")},
            )
            self.assertEqual(response.status_code, 302)
            self.recording.refresh_from_db(); self.entry.refresh_from_db()
            self.assertEqual(self.recording.title, "Eksisterende innspilling")
            self.assertEqual((self.entry.genre, self.entry.energy), ("Ny radiosjanger", 4))
            self.assertFalse(self.entry.channels.exists())
            self.assertEqual(ManagedRecording.objects.count(), rights_before)
