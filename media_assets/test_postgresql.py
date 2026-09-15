import hashlib
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import SkipTest
from unittest.mock import patch

import numpy as np
import soundfile as sf
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase
from mutagen.flac import FLAC

from catalogue.models import Recording
from media_assets.mastering import (
    activate_candidate,
    create_generation_plan,
    generate_candidate,
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
from music_library.models import MusicLibraryEntry


class PostgreSQLMasteringConcurrencyTests(TransactionTestCase):
    """Integration checks that rely on real PostgreSQL locks and constraints."""

    reset_sequences = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != "postgresql":
            raise SkipTest("Krever PostgreSQL for ekte locking-semantikk.")

    def setUp(self):
        self.root_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.root_directory.name)
        self.settings_override = self.settings(
            P7_STORAGE_ROOTS={
                "music_library": {
                    "server_root": str(self.root),
                    "backend": "filesystem",
                    "read_only": True,
                },
                "generated_media": {
                    "server_root": str(self.root),
                    "backend": "filesystem",
                    "read_only": False,
                },
            },
            P7_GENERATED_MEDIA_ROOT_KEY="generated_media",
            P7_GENERATED_MEDIA_RELATIVE_ROOT="P7-generert",
            P7_ALLOW_FILE_WRITES=True,
        )
        self.settings_override.enable()
        self.user = get_user_model().objects.create_user(
            username="postgres-worker", password="test"
        )
        self.recording = Recording.objects.create(title="PostgreSQL-test")
        MusicLibraryEntry.objects.create(recording=self.recording)

    def tearDown(self):
        self.settings_override.disable()
        self.root_directory.cleanup()
        super().tearDown()

    def _asset(self, filename, role, lifecycle="unclassified"):
        return FileAsset.objects.create(
            recording=self.recording,
            filename=filename,
            role=role,
            lifecycle_status=lifecycle,
        )

    def _activation_fixture(self):
        master = self._asset("master.wav", FileAsset.Role.EDITED_WAV_MASTER)
        old = self._asset(
            "old.flac",
            FileAsset.Role.RADIO_FLAC,
            FileAsset.LifecycleStatus.CURRENT,
        )
        candidates = [
            self._asset(
                f"candidate-{number}.flac",
                FileAsset.Role.RADIO_FLAC,
                FileAsset.LifecycleStatus.CANDIDATE,
            )
            for number in (1, 2)
        ]
        selection = RecordingMediaSelection.objects.create(
            recording=self.recording,
            selected_master=master,
            current_radio=old,
        )
        generations = [
            RadioFlacGeneration.objects.create(
                recording=self.recording,
                master_asset=master,
                candidate_asset=candidate,
                target_root_key="generated_media",
                target_relative_path=f"P7-generert/{candidate.filename}",
                technical_plan={"codec": "FLAC"},
                expected_tags={"TITLE": "PostgreSQL-test"},
                metadata_diff={"changed": []},
                status=RadioFlacGeneration.Status.VERIFIED,
                created_by=self.user,
            )
            for candidate in candidates
        ]
        return master, old, candidates, selection, generations

    @staticmethod
    def _thread_call(barrier, callback):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            callback()
            return "ok"
        except Exception as error:  # returned so the parent can assert the result
            return error
        finally:
            close_old_connections()

    def test_partial_unique_index_exists_and_rejects_second_current(self):
        first = self._asset(
            "first.flac",
            FileAsset.Role.RADIO_FLAC,
            FileAsset.LifecycleStatus.CURRENT,
        )
        second = self._asset(
            "second.flac",
            FileAsset.Role.RADIO_FLAC,
            FileAsset.LifecycleStatus.CANDIDATE,
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = %s AND indexname = %s",
                ["media_assets_fileasset", "file_asset_one_current_radio"],
            )
            definition = cursor.fetchone()
        self.assertIsNotNone(definition)
        self.assertIn("UNIQUE", definition[0])
        self.assertIn("WHERE", definition[0])

        with self.assertRaises(IntegrityError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE media_assets_fileasset "
                    "SET lifecycle_status = %s WHERE id = %s",
                    [FileAsset.LifecycleStatus.CURRENT, second.pk],
                )
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.lifecycle_status, FileAsset.LifecycleStatus.CURRENT)
        self.assertEqual(second.lifecycle_status, FileAsset.LifecycleStatus.CANDIDATE)

    def test_concurrent_activation_serializes_to_one_consistent_current(self):
        _, old, candidates, _, generations = self._activation_fixture()
        barrier = threading.Barrier(2)

        def activate(generation_id):
            generation = RadioFlacGeneration.objects.get(pk=generation_id)
            user = get_user_model().objects.get(pk=self.user.pk)
            activate_candidate(generation=generation, user=user)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    self._thread_call,
                    barrier,
                    lambda generation_id=generation.pk: activate(generation_id),
                )
                for generation in generations
            ]
            outcomes = [future.result(timeout=20) for future in futures]

        self.assertTrue(all(result == "ok" for result in outcomes), outcomes)
        current = list(
            FileAsset.objects.filter(
                recording=self.recording,
                role=FileAsset.Role.RADIO_FLAC,
                lifecycle_status=FileAsset.LifecycleStatus.CURRENT,
            )
        )
        self.assertEqual(len(current), 1)
        selection = RecordingMediaSelection.objects.get(recording=self.recording)
        self.assertEqual(selection.current_radio_id, current[0].pk)
        old.refresh_from_db()
        self.assertEqual(old.lifecycle_status, FileAsset.LifecycleStatus.HISTORICAL)
        losing_candidate = next(item for item in candidates if item.pk != current[0].pk)
        losing_candidate.refresh_from_db()
        self.assertEqual(
            losing_candidate.lifecycle_status, FileAsset.LifecycleStatus.HISTORICAL
        )

    def test_derivation_database_constraint_and_cross_recording_validation(self):
        master = self._asset("lineage-master.wav", FileAsset.Role.EDITED_WAV_MASTER)
        derived = self._asset("lineage-radio.flac", FileAsset.Role.RADIO_FLAC)
        relation = FileDerivation.objects.create(
            source_asset=master,
            derived_asset=derived,
            tool_name="postgres-test",
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = %s",
                ["file_derivation_not_self"],
            )
            definition = cursor.fetchone()
        self.assertIsNotNone(definition)
        self.assertIn("CHECK", definition[0])
        with self.assertRaises(IntegrityError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE media_assets_filederivation "
                    "SET derived_asset_id = source_asset_id WHERE id = %s",
                    [relation.pk],
                )

        other_recording = Recording.objects.create(title="Annen innspilling")
        other_asset = FileAsset.objects.create(
            recording=other_recording,
            filename="other.flac",
            role=FileAsset.Role.RADIO_FLAC,
        )
        with self.assertRaises(ValidationError):
            FileDerivation.objects.create(
                source_asset=master,
                derived_asset=other_asset,
                tool_name="postgres-test",
            )

    def test_concurrent_master_selection_leaves_one_valid_selection(self):
        masters = [
            self._asset(f"master-{number}.wav", FileAsset.Role.EDITED_WAV_MASTER)
            for number in (1, 2)
        ]
        barrier = threading.Barrier(2)

        def choose(master_id):
            select_master(
                recording=Recording.objects.get(pk=self.recording.pk),
                asset=FileAsset.objects.get(pk=master_id),
                user=get_user_model().objects.get(pk=self.user.pk),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = [
                future.result(timeout=20)
                for future in [
                    executor.submit(
                        self._thread_call,
                        barrier,
                        lambda master_id=master.pk: choose(master_id),
                    )
                    for master in masters
                ]
            ]
        self.assertTrue(all(result == "ok" for result in outcomes), outcomes)
        selection = RecordingMediaSelection.objects.get(recording=self.recording)
        self.assertIn(selection.selected_master_id, {item.pk for item in masters})
        self.assertEqual(selection.selected_master.recording_id, self.recording.pk)

    def test_activation_exception_rolls_back_selection_lifecycle_and_lineage(self):
        master, old, candidates, selection, generations = self._activation_fixture()
        candidate = candidates[0]
        generation = generations[0]
        derivation = FileDerivation.objects.create(
            source_asset=master,
            derived_asset=candidate,
            tool_name="postgres-test",
        )
        with patch.object(
            MediaAssetEvent.objects,
            "create",
            side_effect=RuntimeError("forced activation failure"),
        ), self.assertRaises(RuntimeError):
            activate_candidate(generation=generation, user=self.user)

        old.refresh_from_db()
        candidate.refresh_from_db()
        selection.refresh_from_db()
        generation.refresh_from_db()
        self.assertEqual(old.lifecycle_status, FileAsset.LifecycleStatus.CURRENT)
        self.assertEqual(
            candidate.lifecycle_status, FileAsset.LifecycleStatus.CANDIDATE
        )
        self.assertEqual(selection.current_radio_id, old.pk)
        self.assertEqual(selection.selected_master_id, master.pk)
        self.assertEqual(generation.status, RadioFlacGeneration.Status.VERIFIED)
        self.assertTrue(FileDerivation.objects.filter(pk=derivation.pk).exists())
        self.assertEqual(
            FileAsset.objects.filter(
                recording=self.recording,
                lifecycle_status=FileAsset.LifecycleStatus.CURRENT,
            ).count(),
            1,
        )

    def _prepare_real_generation(self):
        samples = (np.arange(4800, dtype=np.int32) % 4000 - 2000) << 8
        stereo = np.column_stack([samples, samples])
        radio_path = self.root / "radio" / "source.flac"
        radio_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(radio_path, stereo, 48000, format="FLAC", subtype="PCM_24")
        radio_tags = FLAC(radio_path)
        radio_tags["TITLE"] = ["PostgreSQL-test"]
        radio_tags["ARTIST"] = ["Testartist"]
        radio_tags.save()
        radio = FileAsset.objects.create(
            recording=self.recording,
            filename=radio_path.name,
            role=FileAsset.Role.RADIO_FLAC,
            sha256=hashlib.sha256(radio_path.read_bytes()).hexdigest(),
        )
        FileLocation.objects.create(
            asset=radio,
            storage_type=FileLocation.StorageType.NAS,
            storage_root_key="music_library",
            relative_path="radio/source.flac",
            status=FileLocation.Status.ACTIVE,
            is_current=True,
        )
        master_path = self.root / "master" / "master.wav"
        master_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(master_path, stereo, 48000, subtype="PCM_24")
        master = register_master(
            recording=self.recording,
            root_key="music_library",
            relative_path="master/master.wav",
            user=self.user,
        )
        select_master(recording=self.recording, asset=master, user=self.user)

    def test_concurrent_identical_plan_and_generation_create_one_candidate(self):
        self._prepare_real_generation()
        plan_barrier = threading.Barrier(2)

        def make_plan():
            create_generation_plan(
                recording=Recording.objects.get(pk=self.recording.pk),
                user=get_user_model().objects.get(pk=self.user.pk),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            plan_outcomes = [
                future.result(timeout=20)
                for future in [
                    executor.submit(self._thread_call, plan_barrier, make_plan)
                    for _ in range(2)
                ]
            ]
        self.assertTrue(all(result == "ok" for result in plan_outcomes), plan_outcomes)
        self.assertEqual(RadioFlacGeneration.objects.count(), 1)
        generation = RadioFlacGeneration.objects.get()

        encode_started = threading.Event()
        losing_call_finished = threading.Event()
        original_encode = __import__(
            "media_assets.mastering", fromlist=["_encode_lossless"]
        )._encode_lossless

        def controlled_encode(*args, **kwargs):
            encode_started.set()
            self.assertTrue(losing_call_finished.wait(timeout=10))
            return original_encode(*args, **kwargs)

        def run_generation(wait_for_encoder=False):
            close_old_connections()
            if wait_for_encoder:
                self.assertTrue(encode_started.wait(timeout=10))
            try:
                generate_candidate(
                    generation=RadioFlacGeneration.objects.get(pk=generation.pk),
                    user=get_user_model().objects.get(pk=self.user.pk),
                )
                return "ok"
            except Exception as error:
                return error
            finally:
                if wait_for_encoder:
                    losing_call_finished.set()
                close_old_connections()

        close_old_connections()
        with patch("media_assets.mastering._encode_lossless", controlled_encode):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(run_generation)
                second = executor.submit(run_generation, True)
                outcomes = [first.result(timeout=20), second.result(timeout=20)]
        close_old_connections()

        self.assertEqual(outcomes.count("ok"), 1, outcomes)
        self.assertEqual(
            sum(isinstance(item, ValidationError) for item in outcomes), 1, outcomes
        )
        generation.refresh_from_db()
        self.assertEqual(generation.status, RadioFlacGeneration.Status.VERIFIED)
        self.assertIsNotNone(generation.candidate_asset_id)
        self.assertEqual(
            FileAsset.objects.filter(
                recording=self.recording,
                lifecycle_status=FileAsset.LifecycleStatus.CANDIDATE,
            ).count(),
            1,
        )
        self.assertEqual(len(list((self.root / "P7-generert").rglob("*.flac"))), 1)
