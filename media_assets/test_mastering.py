import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from mutagen.flac import FLAC

from catalogue.models import (
    ExternalIdentifier,
    Recording,
    RecordingContribution,
    Release,
    ReleaseTrack,
)
from flac_ingest.maintenance import create_rebuild_preview, execute_rebuild
from flac_ingest.services import apply_batch, scan_directory
from managed_music.models import ManagedRecording
from music_library.models import MusicLibraryEntry

from media_assets.mastering import (
    activate_candidate,
    build_generation_preview,
    create_generation_plan,
    generate_candidate,
    inspect_master,
    register_master,
    select_master,
)
from media_assets.models import (
    FileAsset,
    FileDerivation,
    FileLocation,
    MediaAssetEvent,
    RadioFlacGeneration,
    RecordingMediaSelection,
)
from media_assets.playback import (
    RadioPlaybackStatus,
    resolve_current_radio_asset,
)


class MasteringWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="master-admin", password="test-password"
        )
        self.recording = Recording.objects.create(
            title="Autoritativ DB-tittel", duration_ms=1000
        )
        RecordingContribution.objects.create(
            recording=self.recording,
            role=RecordingContribution.Role.PRIMARY,
            credited_as="DB-artisten",
        )
        ExternalIdentifier.objects.create(
            recording=self.recording,
            scheme=ExternalIdentifier.Scheme.ISRC,
            value="NO-P7A-26-00001",
        )
        self.entry = MusicLibraryEntry.objects.create(recording=self.recording)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def _settings(self, *, writes=False):
        return override_settings(
            P7_MUSIC_ROOT=str(self.root),
            P7_NAS_ROOT=str(self.root),
            P7_STORAGE_ROOTS={
                "generated_media": {
                    "server_root": str(self.root),
                    "client_root": r"\\P7-GENERATED\Radio",
                    "backend": "filesystem",
                    "read_only": False,
                }
            },
            P7_GENERATED_MEDIA_ROOT_KEY="generated_media",
            P7_GENERATED_MEDIA_RELATIVE_ROOT="P7-generert",
            P7_ALLOW_FILE_WRITES=writes,
            GUI_V2_WRITES_ENABLED=True,
        )

    def _samples(self, channels=2):
        base = (np.arange(4800, dtype=np.int32) % 4000 - 2000) << 8
        return np.column_stack([base] * channels) if channels > 1 else base

    def _wav(
        self,
        name="master.wav",
        *,
        sample_rate=48000,
        subtype="PCM_24",
        channels=2,
    ):
        path = self.root / "master" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(path, self._samples(channels), sample_rate, subtype=subtype)
        return path

    def _radio(self, name="external.flac"):
        path = self.root / "radio" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(
            path, self._samples(2), 48000, format="FLAC", subtype="PCM_24"
        )
        audio = FLAC(path)
        audio["TITLE"] = ["Gammel filtittel"]
        audio["ARTIST"] = ["Gammel filartist"]
        audio["ISRC"] = ["NO-P7A-26-00001"]
        audio["GENRE"] = ["Pop"]
        audio["RATING"] = ["4"]
        audio["KANAL"] = ["P7 Riks", "P7 Ung"]
        audio["TARGET"] = ["Voksen"]
        audio["ROTATION"] = ["Ikke vurdert"]
        audio["UNKNOWN_PRIVATE"] = ["skal ikke kopieres"]
        audio.save()
        asset = FileAsset.objects.create(
            recording=self.recording,
            filename=name,
            role=FileAsset.Role.RADIO_FLAC,
            mime_type="audio/flac",
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            sync_status=FileAsset.SyncStatus.SYNCED,
        )
        FileLocation.objects.create(
            asset=asset,
            storage_type=FileLocation.StorageType.NAS,
            relative_path=f"radio/{name}",
            status=FileLocation.Status.ACTIVE,
            is_current=True,
        )
        return asset, path

    def _registered_selected_master(self, **wav_options):
        path = self._wav(**wav_options)
        asset = register_master(
            recording=self.recording,
            root_key="music_library",
            relative_path=str(path.relative_to(self.root)).replace("\\", "/"),
            user=self.user,
        )
        select_master(recording=self.recording, asset=asset, user=self.user)
        return asset, path

    def test_master_inspection_supports_integer_pcm_and_rejects_float(self):
        pcm = self._wav()
        float_path = self._wav("float.wav", subtype="FLOAT")
        with self._settings():
            supported = inspect_master(
                root_key="music_library", relative_path="master/master.wav"
            )
            unsupported = inspect_master(
                root_key="music_library", relative_path="master/float.wav"
            )
        self.assertTrue(supported.supported)
        self.assertEqual(supported.bit_depth, 24)
        self.assertFalse(unsupported.supported)
        self.assertEqual(pcm.read_bytes(), pcm.read_bytes())

    def test_multiple_masters_require_explicit_selection(self):
        with self._settings():
            first, _ = self._registered_selected_master()
            second_path = self._wav("second.wav")
            second = register_master(
                recording=self.recording,
                root_key="music_library",
                relative_path="master/second.wav",
                user=self.user,
            )
        selection = RecordingMediaSelection.objects.get(
            recording=self.recording
        )
        self.assertEqual(selection.selected_master, first)
        self.assertEqual(
            FileAsset.objects.filter(
                recording=self.recording, role=FileAsset.Role.EDITED_WAV_MASTER
            ).count(),
            2,
        )
        self.assertNotEqual(selection.selected_master, second)

    def test_current_radio_constraint_and_derivation_validation(self):
        first = FileAsset.objects.create(
            recording=self.recording,
            filename="a.flac",
            role=FileAsset.Role.RADIO_FLAC,
            lifecycle_status=FileAsset.LifecycleStatus.CURRENT,
        )
        with self.assertRaises(ValidationError):
            FileAsset.objects.create(
                recording=self.recording,
                filename="b.flac",
                role=FileAsset.Role.RADIO_FLAC,
                lifecycle_status=FileAsset.LifecycleStatus.CURRENT,
            )
        other = Recording.objects.create(title="Annen")
        master = FileAsset.objects.create(
            recording=other,
            filename="master.wav",
            role=FileAsset.Role.EDITED_WAV_MASTER,
        )
        relation = FileDerivation(
            source_asset=master,
            derived_asset=first,
            tool_name="test",
        )
        with self.assertRaises(ValidationError):
            relation.save()
        with self.assertRaises(ValidationError):
            FileDerivation.objects.create(
                source_asset=first,
                derived_asset=first,
                tool_name="test",
            )

    def test_selection_rejects_master_and_current_from_other_recording(self):
        other = Recording.objects.create(title="Annen")
        local_radio = FileAsset.objects.create(
            recording=self.recording,
            filename="local-radio.flac",
            role=FileAsset.Role.RADIO_FLAC,
        )
        other_master = FileAsset.objects.create(
            recording=other,
            filename="other-master.wav",
            role=FileAsset.Role.EDITED_WAV_MASTER,
        )
        other_radio = FileAsset.objects.create(
            recording=other,
            filename="other-radio.flac",
            role=FileAsset.Role.RADIO_FLAC,
            lifecycle_status=FileAsset.LifecycleStatus.CURRENT,
        )
        with self.assertRaises(ValidationError):
            RecordingMediaSelection.objects.create(
                recording=self.recording,
                selected_master=other_master,
            )
        with self.assertRaises(ValidationError):
            RecordingMediaSelection.objects.create(
                recording=self.recording,
                selected_master=local_radio,
            )
        with self.assertRaises(ValidationError):
            RecordingMediaSelection.objects.create(
                recording=self.recording,
                current_radio=other_radio,
            )
        with self.assertRaises(ValidationError):
            RadioFlacGeneration.objects.create(
                recording=self.recording,
                master_asset=other_master,
                target_root_key="generated_media",
                target_relative_path="P7-generert/wrong.flac",
                status=RadioFlacGeneration.Status.PLANNED,
                created_by=self.user,
            )

    def test_explicit_current_selection_must_be_activated_and_wins_over_legacy(
        self,
    ):
        first, _ = self._radio("first.flac")
        second, _ = self._radio("second.flac")
        invalid = RecordingMediaSelection(
            recording=self.recording,
            current_radio=second,
        )
        with self.assertRaises(ValidationError):
            invalid.full_clean()

        second.lifecycle_status = FileAsset.LifecycleStatus.CURRENT
        second.save(update_fields=("lifecycle_status",))
        selection = RecordingMediaSelection.objects.create(
            recording=self.recording,
            current_radio=second,
            current_radio_by=self.user,
        )
        self.assertEqual(
            resolve_current_radio_asset(self.recording).asset, second
        )
        self.assertNotEqual(selection.current_radio, first)

    def test_write_gate_blocks_generation_without_new_file(self):
        with self._settings():
            self._radio()
            self._registered_selected_master()
            generation = create_generation_plan(
                recording=self.recording, user=self.user
            )
            target = self.root / Path(generation.target_relative_path)
            with self.assertRaises(PermissionDenied):
                generate_candidate(generation=generation, user=self.user)
        self.assertFalse(target.exists())
        self.assertIsNone(
            FileAsset.objects.filter(generation__isnull=False).first()
        )

    def test_identical_plan_and_generation_are_idempotent(self):
        with self._settings(writes=True):
            self._radio()
            self._registered_selected_master()
            first = create_generation_plan(
                recording=self.recording, user=self.user
            )
            repeated_plan = create_generation_plan(
                recording=self.recording, user=self.user
            )
            self.assertEqual(repeated_plan.pk, first.pk)

            generate_candidate(generation=first, user=self.user)
            first.refresh_from_db()
            candidate_id = first.candidate_asset_id
            repeated_generation = generate_candidate(
                generation=first, user=self.user
            )
            repeated_plan_after_file = create_generation_plan(
                recording=self.recording, user=self.user
            )

        self.assertEqual(repeated_generation.pk, first.pk)
        self.assertEqual(repeated_plan_after_file.pk, first.pk)
        self.assertEqual(repeated_generation.candidate_asset_id, candidate_id)
        self.assertEqual(
            FileAsset.objects.filter(
                recording=self.recording,
                lifecycle_status=FileAsset.LifecycleStatus.CANDIDATE,
            ).count(),
            1,
        )

    def test_managed_metadata_audio_roundtrip_and_activation(self):
        ManagedRecording.objects.create(library_entry=self.entry)
        with self._settings(writes=True):
            old_asset, old_path = self._radio()
            master, master_path = self._registered_selected_master()
            old_before = (old_path.read_bytes(), old_path.stat().st_mtime_ns)
            master_before = (
                master_path.read_bytes(),
                master_path.stat().st_mtime_ns,
            )
            preview = build_generation_preview(recording=self.recording)
            self.assertEqual(
                preview["expected_tags"]["TITLE"], ["Autoritativ DB-tittel"]
            )
            self.assertEqual(preview["expected_tags"]["RATING"], ["4"])
            self.assertNotIn("UNKNOWN_PRIVATE", preview["expected_tags"])
            generation = create_generation_plan(
                recording=self.recording, user=self.user
            )
            generate_candidate(generation=generation, user=self.user)
            generation.refresh_from_db()
            candidate = generation.candidate_asset
            self.assertEqual(
                generation.status, RadioFlacGeneration.Status.VERIFIED
            )
            self.assertEqual(
                candidate.lifecycle_status, FileAsset.LifecycleStatus.CANDIDATE
            )
            self.assertEqual(
                resolve_current_radio_asset(self.recording).asset, old_asset
            )
            generated_path = self.root / generation.target_relative_path
            tags = {
                key.upper(): list(values)
                for key, values in FLAC(generated_path).tags.items()
            }
            self.assertEqual(tags["TITLE"], ["Autoritativ DB-tittel"])
            self.assertEqual(tags["P7UUID"], [str(self.recording.pk)])
            self.assertNotIn("UNKNOWN_PRIVATE", tags)
            self.assertEqual(FileDerivation.objects.get().source_asset, master)
            activate_candidate(generation=generation, user=self.user)
            self.recording.refresh_from_db()
            old_asset.refresh_from_db()
            candidate.refresh_from_db()
            self.assertEqual(
                self.recording.media_selection.current_radio_id, candidate.pk
            )
            self.assertEqual(
                candidate.lifecycle_status, FileAsset.LifecycleStatus.CURRENT
            )
            self.assertEqual(
                FileAsset.objects.filter(
                    recording=self.recording,
                    role=FileAsset.Role.RADIO_FLAC,
                    lifecycle_status=FileAsset.LifecycleStatus.CURRENT,
                ).count(),
                1,
            )
            self.assertEqual(
                old_asset.lifecycle_status,
                FileAsset.LifecycleStatus.HISTORICAL,
            )
            self.assertEqual(
                resolve_current_radio_asset(self.recording).asset, candidate
            )
            self.assertEqual(
                (old_path.read_bytes(), old_path.stat().st_mtime_ns),
                old_before,
            )
            self.assertEqual(
                (master_path.read_bytes(), master_path.stat().st_mtime_ns),
                master_before,
            )
        self.assertTrue(
            MediaAssetEvent.objects.filter(
                event_type=MediaAssetEvent.EventType.RADIO_ACTIVATED
            ).exists()
        )

    def test_unmanaged_catalogue_and_radio_metadata_come_from_current_flac(
        self,
    ):
        with self._settings():
            self._radio()
            self._registered_selected_master()
            preview = build_generation_preview(recording=self.recording)

        self.assertFalse(preview["is_managed"])
        self.assertEqual(
            preview["expected_tags"]["TITLE"], ["Gammel filtittel"]
        )
        self.assertEqual(
            preview["expected_tags"]["ARTIST"], ["Gammel filartist"]
        )
        self.assertEqual(
            preview["expected_tags"]["KANAL"], ["P7 Riks", "P7 Ung"]
        )
        self.assertEqual(
            preview["expected_tags"]["ROTATION"], ["Ikke vurdert"]
        )

    def test_ambiguous_radio_source_blocks_generation_preview(self):
        with self._settings():
            self._radio("one.flac")
            self._radio("two.flac")
            self._registered_selected_master()
            with self.assertRaisesMessage(ValidationError, "Ingen entydig"):
                build_generation_preview(recording=self.recording)

    def test_output_collision_is_not_overwritten(self):
        with self._settings(writes=True):
            old, _ = self._radio()
            self._registered_selected_master()
            generation = create_generation_plan(
                recording=self.recording, user=self.user
            )
            occupied = self.root / generation.target_relative_path
            occupied.parent.mkdir(parents=True, exist_ok=True)
            occupied.write_bytes(b"occupied")
            with self.assertRaisesMessage(ValidationError, "finnes allerede"):
                generate_candidate(generation=generation, user=self.user)

        self.assertEqual(occupied.read_bytes(), b"occupied")
        self.assertEqual(
            resolve_current_radio_asset(self.recording).asset, old
        )

    def test_activation_failure_rolls_back_and_keeps_release_track(self):
        release = Release.objects.create(title="Testutgivelse")
        track = ReleaseTrack.objects.create(
            release=release,
            recording=self.recording,
            sequence_number=1,
            track_number=1,
        )
        with self._settings(writes=True):
            old, _ = self._radio()
            master, _ = self._registered_selected_master()
            old.lifecycle_status = FileAsset.LifecycleStatus.CURRENT
            old.save(update_fields=("lifecycle_status",))
            selection = RecordingMediaSelection.objects.get(
                recording=self.recording
            )
            selection.current_radio = old
            selection.current_radio_by = self.user
            selection.save()
            generation = create_generation_plan(
                recording=self.recording, user=self.user
            )
            generate_candidate(generation=generation, user=self.user)
            generation.refresh_from_db()
            candidate = generation.candidate_asset
            with patch(
                "media_assets.mastering.MediaAssetEvent.objects.create",
                side_effect=RuntimeError("audit failed"),
            ), self.assertRaises(RuntimeError):
                activate_candidate(generation=generation, user=self.user)

        old.refresh_from_db()
        candidate.refresh_from_db()
        generation.refresh_from_db()
        selection.refresh_from_db()
        track.refresh_from_db()
        self.assertEqual(
            old.lifecycle_status, FileAsset.LifecycleStatus.CURRENT
        )
        self.assertEqual(
            candidate.lifecycle_status, FileAsset.LifecycleStatus.CANDIDATE
        )
        self.assertEqual(
            generation.status, RadioFlacGeneration.Status.VERIFIED
        )
        self.assertEqual(selection.current_radio_id, old.pk)
        self.assertEqual(selection.selected_master_id, master.pk)
        self.assertEqual(track.recording_id, self.recording.pk)

    def test_database_registration_failure_removes_final_candidate_file(self):
        with self._settings(writes=True):
            old, _ = self._radio()
            master, _ = self._registered_selected_master()
            generation = create_generation_plan(
                recording=self.recording, user=self.user
            )
            target = self.root / generation.target_relative_path
            with patch.object(
                FileDerivation,
                "save",
                side_effect=RuntimeError("database registration failed"),
            ), self.assertRaises(RuntimeError):
                generate_candidate(generation=generation, user=self.user)

        generation.refresh_from_db()
        selection = RecordingMediaSelection.objects.get(
            recording=self.recording
        )
        self.assertFalse(target.exists())
        self.assertFalse(FileDerivation.objects.exists())
        self.assertFalse(
            FileAsset.objects.filter(
                recording=self.recording,
                lifecycle_status=FileAsset.LifecycleStatus.CANDIDATE,
            ).exists()
        )
        self.assertEqual(generation.status, RadioFlacGeneration.Status.FAILED)
        self.assertEqual(selection.selected_master_id, master.pk)
        self.assertIsNone(selection.current_radio_id)
        self.assertEqual(
            resolve_current_radio_asset(self.recording).asset,
            old,
        )

    def test_rebuild_preserves_selection_lineage_and_generated_p7uuid_rescans(
        self,
    ):
        with self._settings(writes=True):
            old, _ = self._radio()
            master, _ = self._registered_selected_master()
            generation = create_generation_plan(
                recording=self.recording, user=self.user
            )
            generate_candidate(generation=generation, user=self.user)
            generation.refresh_from_db()
            candidate = generation.candidate_asset
            activate_candidate(generation=generation, user=self.user)

            batch = scan_directory(
                relative_root="P7-generert",
                recursive=True,
                user=self.user,
                force_read=True,
            )
            self.assertEqual(batch.items.count(), 1)
            item = batch.items.get()
            self.assertEqual(item.recording_id, self.recording.pk)
            apply_batch(batch, user=self.user)

            rebuild = create_rebuild_preview(user=self.user)
            self.assertEqual(rebuild.plan["stats"]["protected_recordings"], 1)
            execute_rebuild(rebuild, user=self.user)

        self.assertEqual(
            Recording.objects.filter(pk=self.recording.pk).count(), 1
        )
        selection = RecordingMediaSelection.objects.get(
            recording=self.recording
        )
        self.assertEqual(selection.selected_master_id, master.pk)
        self.assertEqual(selection.current_radio_id, candidate.pk)
        self.assertTrue(
            FileDerivation.objects.filter(
                source_asset=master,
                derived_asset=candidate,
            ).exists()
        )
        old.refresh_from_db()
        candidate.refresh_from_db()
        self.assertEqual(
            old.lifecycle_status, FileAsset.LifecycleStatus.HISTORICAL
        )
        self.assertEqual(
            candidate.lifecycle_status, FileAsset.LifecycleStatus.CURRENT
        )
        self.assertEqual(
            resolve_current_radio_asset(self.recording).asset, candidate
        )

    def test_supported_quality_and_channels_are_preserved(self):
        for subtype, rate, channels in (
            ("PCM_16", 44100, 1),
            ("PCM_24", 48000, 2),
            ("PCM_24", 96000, 2),
        ):
            with self.subTest(subtype=subtype, rate=rate, channels=channels):
                recording = Recording.objects.create(
                    title=f"Test {subtype} {rate}"
                )
                MusicLibraryEntry.objects.create(recording=recording)
                self.recording = recording
                with self._settings(writes=True):
                    self._radio(f"source-{rate}.flac")
                    self._registered_selected_master(
                        name=f"master-{rate}.wav",
                        sample_rate=rate,
                        subtype=subtype,
                        channels=channels,
                    )
                    generation = create_generation_plan(
                        recording=recording, user=self.user
                    )
                    generate_candidate(generation=generation, user=self.user)
                    generation.refresh_from_db()
                technical = generation.candidate_asset.technical_metadata
                self.assertEqual(technical["sample_rate"], rate)
                self.assertEqual(
                    technical["bits_per_sample"], int(subtype[-2:])
                )
                self.assertEqual(technical["channels"], channels)
                self.assertEqual(
                    generation.verification["source_pcm_sha256"],
                    generation.verification["candidate_pcm_sha256"],
                )

    def test_encoder_failure_preserves_current_and_cleans_temp_output(self):
        with self._settings(writes=True):
            old, old_path = self._radio()
            self._registered_selected_master()
            generation = create_generation_plan(
                recording=self.recording, user=self.user
            )
            with patch(
                "media_assets.mastering._encode_lossless",
                side_effect=RuntimeError("encoder failed"),
            ), self.assertRaises(RuntimeError):
                generate_candidate(generation=generation, user=self.user)
        generation.refresh_from_db()
        self.assertEqual(generation.status, RadioFlacGeneration.Status.FAILED)
        self.assertTrue(old_path.exists())
        self.assertEqual(
            resolve_current_radio_asset(self.recording).asset, old
        )
        self.assertFalse(
            (self.root / generation.target_relative_path).exists()
        )

    def test_missing_master_and_metadata_failure_leave_current_untouched(self):
        with self._settings(writes=True):
            old, old_path = self._radio()
            _, master_path = self._registered_selected_master()
            missing_plan = create_generation_plan(
                recording=self.recording, user=self.user
            )
            master_path.unlink()
            with self.assertRaises(OSError):
                generate_candidate(generation=missing_plan, user=self.user)
            self.assertEqual(
                resolve_current_radio_asset(self.recording).asset, old
            )

            # Restore the controlled source and create a fresh plan. A tag
            # failure must clean the candidate just like an encoder failure.
            self._wav()
            metadata_plan = create_generation_plan(
                recording=self.recording, user=self.user
            )
            with patch(
                "media_assets.mastering._write_and_verify_tags",
                side_effect=ValidationError("metadata failed"),
            ), self.assertRaisesMessage(ValidationError, "metadata failed"):
                generate_candidate(generation=metadata_plan, user=self.user)

        self.assertTrue(old_path.exists())
        self.assertEqual(
            resolve_current_radio_asset(self.recording).asset, old
        )
        self.assertFalse(
            (self.root / metadata_plan.target_relative_path).exists()
        )
