import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from mutagen.flac import FLAC

from catalogue.models import Recording, RecordingContribution
from managed_music.models import ManagedRecording
from media_assets.mastering import register_master, select_master
from media_assets.models import FileAsset, FileLocation, RadioFlacGeneration
from music_library.models import MusicLibraryEntry
from rights.models import RightsClaim


class MasteringGuiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="gui-master", password="test-password"
        )
        self.client.force_login(self.user)
        self.recording = Recording.objects.create(title="GUI mastertest")
        RecordingContribution.objects.create(
            recording=self.recording,
            role=RecordingContribution.Role.PRIMARY,
            credited_as="Testartist",
        )
        MusicLibraryEntry.objects.create(recording=self.recording)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        values = (np.arange(4800, dtype=np.int32) % 2000 - 1000) << 8
        stereo = np.column_stack((values, values))
        self.master_path = self.root / "masters" / "master.wav"
        self.master_path.parent.mkdir(parents=True)
        sf.write(self.master_path, stereo, 48000, subtype="PCM_24")
        self.radio_path = self.root / "radio" / "old.flac"
        self.radio_path.parent.mkdir(parents=True)
        sf.write(
            self.radio_path, stereo, 48000, format="FLAC", subtype="PCM_24"
        )
        audio = FLAC(self.radio_path)
        audio["TITLE"] = [self.recording.title]
        audio["ARTIST"] = ["Testartist"]
        audio["GENRE"] = ["Pop"]
        audio["RATING"] = ["3"]
        audio.save()
        self.old_radio = FileAsset.objects.create(
            recording=self.recording,
            filename="old.flac",
            role=FileAsset.Role.RADIO_FLAC,
            sync_status=FileAsset.SyncStatus.SYNCED,
        )
        FileLocation.objects.create(
            asset=self.old_radio,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="radio/old.flac",
            is_current=True,
            status=FileLocation.Status.ACTIVE,
        )

    def _settings(self, writes=False):
        return override_settings(
            GUI_V2_WRITES_ENABLED=True,
            P7_ALLOW_FILE_WRITES=writes,
            P7_MUSIC_ROOT=str(self.root),
            P7_STORAGE_ROOTS={
                "generated_media": {
                    "server_root": str(self.root),
                    "client_root": r"\\P7-GENERATED\Radio",
                    "read_only": False,
                }
            },
            P7_GENERATED_MEDIA_ROOT_KEY="generated_media",
            P7_GENERATED_MEDIA_RELATIVE_ROOT="P7-generert",
        )

    def _register_and_select(self):
        master = register_master(
            recording=self.recording,
            root_key="music_library",
            relative_path="masters/master.wav",
            user=self.user,
        )
        select_master(recording=self.recording, asset=master, user=self.user)
        return master

    def test_master_inspection_registration_and_selection_are_explicit(self):
        url = reverse(
            "gui_v2:recording_master_register", args=[self.recording.pk]
        )
        data = {
            "root_key": "music_library",
            "relative_path": "masters/master.wav",
        }
        with self._settings():
            inspected = self.client.post(url, {**data, "action": "inspect"})
            self.assertEqual(inspected.status_code, 200)
            self.assertContains(inspected, "PCM_24")
            self.assertContains(inspected, "uten DSP")
            self.assertFalse(
                FileAsset.objects.filter(
                    role=FileAsset.Role.EDITED_WAV_MASTER
                ).exists()
            )
            confirmed = self.client.post(url, {**data, "action": "confirm"})
            self.assertEqual(confirmed.status_code, 302)
            master = FileAsset.objects.get(
                role=FileAsset.Role.EDITED_WAV_MASTER
            )
            files_page = self.client.get(
                reverse("gui_v2:recording_files", args=[self.recording.pk])
            )
            self.assertContains(files_page, "Velg som master")
            selected = self.client.post(
                reverse(
                    "gui_v2:recording_master_select",
                    args=[self.recording.pk, master.pk],
                )
            )
            self.assertEqual(selected.status_code, 302)
            files_page = self.client.get(
                reverse("gui_v2:recording_files", args=[self.recording.pk])
            )
            self.assertContains(files_page, "Valgt master: master.wav")
            self.assertContains(files_page, "Forbered radio-FLAC")
        self.assertFalse(ManagedRecording.objects.exists())
        self.assertFalse(RightsClaim.objects.exists())

    def test_preview_write_gate_candidate_and_explicit_activation(self):
        with self._settings():
            self._register_and_select()
            preview_url = reverse(
                "gui_v2:recording_generation_preview", args=[self.recording.pk]
            )
            preview = self.client.get(preview_url)
            self.assertContains(preview, "Gjeldende autoritativ radio-FLAC")
            self.assertContains(preview, "P7UUID")
            preview_target = preview.context["preview"]["target_relative_path"]
            self.assertContains(
                preview,
                f'name="target_relative_path" value="{preview_target}"',
                html=False,
            )
            repeated_preview = self.client.get(preview_url)
            self.assertEqual(
                repeated_preview.context["preview"]["target_relative_path"],
                preview_target,
            )
            # The server derives the same target if a user agent submits the
            # form without including the clicked button's value.
            planned = self.client.post(preview_url, {})
            self.assertEqual(planned.status_code, 302)
            generation = RadioFlacGeneration.objects.get()
            self.assertEqual(generation.target_relative_path, preview_target)
            blocked = self.client.post(
                reverse(
                    "gui_v2:recording_generate_candidate",
                    args=[self.recording.pk, generation.pk],
                )
            )
            self.assertEqual(blocked.status_code, 302)
            generation.refresh_from_db()
            self.assertEqual(
                generation.status, RadioFlacGeneration.Status.PLANNED
            )
            self.assertIsNone(generation.candidate_asset)

        with self._settings(writes=True):
            generated = self.client.post(
                reverse(
                    "gui_v2:recording_generate_candidate",
                    args=[self.recording.pk, generation.pk],
                )
            )
            self.assertEqual(generated.status_code, 302)
            generation.refresh_from_db()
            self.assertEqual(
                generation.status, RadioFlacGeneration.Status.VERIFIED
            )
            self.old_radio.refresh_from_db()
            self.assertNotEqual(
                self.old_radio.lifecycle_status,
                FileAsset.LifecycleStatus.HISTORICAL,
            )
            files_page = self.client.get(
                reverse("gui_v2:recording_files", args=[self.recording.pk])
            )
            self.assertContains(files_page, "Verifisert kandidat")
            self.assertContains(files_page, "Kontroller og aktiver kandidat")
            activated = self.client.post(
                reverse(
                    "gui_v2:recording_activate_candidate",
                    args=[self.recording.pk, generation.pk],
                )
            )
            self.assertEqual(activated.status_code, 302)
        generation.refresh_from_db()
        self.old_radio.refresh_from_db()
        self.assertEqual(
            generation.status, RadioFlacGeneration.Status.ACTIVATED
        )
        self.assertEqual(
            self.old_radio.lifecycle_status,
            FileAsset.LifecycleStatus.HISTORICAL,
        )
