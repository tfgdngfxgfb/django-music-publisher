from django.contrib.auth import get_user_model
from django.test import TestCase

from catalogue.models import Recording
from media_assets.models import (
    FileAsset,
    FileDerivation,
    RadioFlacGeneration,
    RecordingMediaSelection,
)
from media_assets.pipeline_status import (
    CurrentRadioState,
    GenerationState,
    MasterState,
    get_recording_media_pipeline_status,
)


class RecordingMediaPipelineStatusTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "pipeline-status", "", "test"
        )
        self.recording = Recording.objects.create(title="Innspilling")
        self.old_master = FileAsset.objects.create(
            recording=self.recording,
            filename="master-v1.wav",
            role=FileAsset.Role.EDITED_WAV_MASTER,
        )
        self.selected_master = FileAsset.objects.create(
            recording=self.recording,
            filename="master-v2.wav",
            role=FileAsset.Role.EDITED_WAV_MASTER,
        )
        self.current = FileAsset.objects.create(
            recording=self.recording,
            filename="radio-v1.flac",
            role=FileAsset.Role.RADIO_FLAC,
            lifecycle_status=FileAsset.LifecycleStatus.CURRENT,
        )
        self.selection = RecordingMediaSelection.objects.create(
            recording=self.recording,
            selected_master=self.selected_master,
            current_radio=self.current,
        )

    def derive(self, source, derived):
        return FileDerivation.objects.create(
            source_asset=source,
            derived_asset=derived,
            created_by=self.user,
            tool_name="test",
        )

    def generation(self, master, candidate, status):
        target_name = candidate.filename if candidate else str(status)
        return RadioFlacGeneration.objects.create(
            recording=self.recording,
            master_asset=master,
            candidate_asset=candidate,
            target_root_key="generated_media",
            target_relative_path=f"radio/{target_name}",
            technical_plan={"format": "FLAC"},
            expected_tags={"TITLE": [self.recording.title]},
            metadata_diff=["test"],
            status=status,
            created_by=self.user,
        )

    def test_selected_v2_candidate_v2_and_current_v1_are_separate_facts(self):
        self.derive(self.old_master, self.current)
        candidate = FileAsset.objects.create(
            recording=self.recording,
            filename="radio-v2.flac",
            role=FileAsset.Role.RADIO_FLAC,
            lifecycle_status=FileAsset.LifecycleStatus.CANDIDATE,
        )
        self.derive(self.selected_master, candidate)
        self.generation(
            self.selected_master,
            candidate,
            RadioFlacGeneration.Status.VERIFIED,
        )

        status = get_recording_media_pipeline_status(self.recording)

        self.assertEqual(
            status.current_state, CurrentRadioState.FROM_PREVIOUS_MASTER
        )
        self.assertEqual(
            status.generation_state,
            GenerationState.CANDIDATE_FROM_SELECTED_MASTER,
        )
        self.assertEqual(status.current_radio, self.current)
        self.assertEqual(status.candidate_asset, candidate)

    def test_unknown_current_lineage_is_neutral_and_not_regeneration_claim(
        self,
    ):
        status = get_recording_media_pipeline_status(self.recording)

        self.assertEqual(
            status.current_state, CurrentRadioState.LINEAGE_UNKNOWN
        )
        self.assertEqual(status.generation_state, GenerationState.NONE)
        self.assertIsNone(status.current_source_master)

    def test_current_matches_selected_master(self):
        self.derive(self.selected_master, self.current)

        status = get_recording_media_pipeline_status(self.recording)

        self.assertEqual(
            status.current_state,
            CurrentRadioState.MATCHES_SELECTED_MASTER,
        )

    def test_generation_progress_failure_and_other_master_are_distinct(self):
        generation = self.generation(
            self.selected_master,
            None,
            RadioFlacGeneration.Status.GENERATING,
        )
        self.assertEqual(
            get_recording_media_pipeline_status(
                self.recording
            ).generation_state,
            GenerationState.IN_PROGRESS,
        )
        generation.status = RadioFlacGeneration.Status.FAILED
        generation.save()
        self.assertEqual(
            get_recording_media_pipeline_status(
                self.recording
            ).generation_state,
            GenerationState.FAILED,
        )
        generation.delete()

        candidate = FileAsset.objects.create(
            recording=self.recording,
            filename="radio-from-old-master.flac",
            role=FileAsset.Role.RADIO_FLAC,
            lifecycle_status=FileAsset.LifecycleStatus.CANDIDATE,
        )
        self.derive(self.old_master, candidate)
        self.generation(
            self.old_master,
            candidate,
            RadioFlacGeneration.Status.VERIFIED,
        )
        self.assertEqual(
            get_recording_media_pipeline_status(
                self.recording
            ).generation_state,
            GenerationState.CANDIDATE_FROM_OTHER_MASTER,
        )

    def test_recording_without_selection_or_radio_has_neutral_empty_state(
        self,
    ):
        recording = Recording.objects.create(title="Uten medier")

        status = get_recording_media_pipeline_status(recording)

        self.assertEqual(status.master_state, MasterState.NO_MASTER)
        self.assertEqual(status.current_state, CurrentRadioState.NO_RADIO)
        self.assertEqual(status.generation_state, GenerationState.NONE)
