from unittest.mock import patch
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalogue.models import Recording, Release, ReleaseTrack
from gui_v2.radio_workbench import radio_work_rows
from media_assets.models import FileAsset, RecordingMediaSelection
from media_assets.pipeline_status import RadioWorkState


class RadioWorkbenchTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "radio-workbench", "", "test"
        )
        self.client.force_login(self.user)
        self.recording = Recording.objects.create(title="Samme innspilling")
        self.master = FileAsset.objects.create(
            recording=self.recording,
            filename="master.wav",
            role=FileAsset.Role.EDITED_WAV_MASTER,
        )
        RecordingMediaSelection.objects.create(
            recording=self.recording, selected_master=self.master
        )
        for number in (1, 2):
            release = Release.objects.create(title=f"Utgivelse {number}")
            ReleaseTrack.objects.create(
                release=release,
                recording=self.recording,
                sequence_number=1,
                side="A",
                track_number=1,
            )

    def test_one_recording_is_one_row_across_releases(self):
        rows = radio_work_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["releases"]), 2)
        self.assertEqual(rows[0]["state"], RadioWorkState.MISSING_RADIO_FLAC)
        response = self.client.get(reverse("gui_v2:radio_workbench"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Samme innspilling")
        self.assertContains(response, "Mangler Radio-FLAC")

    def test_projection_queries_do_not_grow_per_recording(self):
        with CaptureQueriesContext(connection) as small:
            self.assertEqual(len(radio_work_rows()), 1)
        for number in range(12):
            recording = Recording.objects.create(title=f"Ekstra {number}")
            master = FileAsset.objects.create(
                recording=recording,
                filename=f"master-{number}.wav",
                role=FileAsset.Role.EDITED_WAV_MASTER,
            )
            RecordingMediaSelection.objects.create(
                recording=recording, selected_master=master
            )
        with CaptureQueriesContext(connection) as larger:
            self.assertEqual(len(radio_work_rows()), 13)
        self.assertLessEqual(len(larger), len(small) + 1)

    @override_settings(GUI_V2_WRITES_ENABLED=True, P7_ALLOW_FILE_WRITES=True)
    def test_bulk_preview_is_read_only_and_stale_master_blocks_apply(self):
        def preview(*, recording):
            master = RecordingMediaSelection.objects.get(
                recording=recording
            ).selected_master
            return {
                "master": master,
                "first_radio": True,
                "target_relative_path": "radio/candidate.flac",
                "metadata_digest": "digest",
            }

        with patch(
            "gui_v2.radio_work_views.build_generation_preview",
            side_effect=preview,
        ):
            response = self.client.post(
                reverse("gui_v2:radio_workbench_bulk"),
                {"mode": "preview", "recordings": [str(self.recording.pk)]},
            )
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Forhåndsvisning")
            self.assertContains(response, "fysisk medium")
            self.assertEqual(Recording.objects.count(), 1)
            token = response.context["plan"]
            newer = FileAsset.objects.create(
                recording=self.recording,
                filename="master-v2.wav",
                role=FileAsset.Role.EDITED_WAV_MASTER,
            )
            selection = RecordingMediaSelection.objects.get(
                recording=self.recording
            )
            selection.selected_master = newer
            selection.save()
            with patch(
                "gui_v2.radio_work_views.create_generation_plan"
            ) as create:
                applied = self.client.post(
                    reverse("gui_v2:radio_workbench_bulk"),
                    {
                        "mode": "apply",
                        "plan": token,
                        f"metadata_{self.recording.pk}": "yes",
                    },
                )
                self.assertContains(applied, "endret")
                create.assert_not_called()

    @override_settings(GUI_V2_WRITES_ENABLED=True, P7_ALLOW_FILE_WRITES=True)
    def test_bulk_apply_uses_existing_generator_without_activation(self):
        preview_data = {
            "master": self.master,
            "first_radio": True,
            "target_relative_path": "radio/candidate.flac",
            "metadata_digest": "digest",
        }
        with patch(
            "gui_v2.radio_work_views.build_generation_preview",
            return_value=preview_data,
        ):
            preview = self.client.post(
                reverse("gui_v2:radio_workbench_bulk"),
                {
                    "mode": "preview",
                    "recordings": [str(self.recording.pk)],
                },
            )
            token = preview.context["plan"]
            generation = SimpleNamespace(status="planned")
            with (
                patch(
                    "gui_v2.radio_work_views.create_generation_plan",
                    return_value=generation,
                ) as create,
                patch(
                    "gui_v2.radio_work_views.generate_candidate"
                ) as generate,
            ):
                result = self.client.post(
                    reverse("gui_v2:radio_workbench_bulk"),
                    {
                        "mode": "apply",
                        "plan": token,
                        f"metadata_{self.recording.pk}": "yes",
                    },
                )
                self.assertContains(result, "Kandidat generert")
                create.assert_called_once()
                generate.assert_called_once()

    @override_settings(GUI_V2_WRITES_ENABLED=True, P7_ALLOW_FILE_WRITES=True)
    def test_existing_failed_plan_is_not_reported_as_generated(self):
        with patch(
            "gui_v2.radio_work_views.build_generation_preview",
            return_value={
                "master": self.master,
                "first_radio": True,
                "target_relative_path": "radio/candidate.flac",
                "metadata_digest": "digest",
            },
        ):
            preview = self.client.post(
                reverse("gui_v2:radio_workbench_bulk"),
                {"mode": "preview", "recordings": [str(self.recording.pk)]},
            )
            with (
                patch(
                    "gui_v2.radio_work_views.create_generation_plan",
                    return_value=SimpleNamespace(status="failed"),
                ),
                patch(
                    "gui_v2.radio_work_views.generate_candidate",
                    side_effect=ValidationError(
                        "Bare en planlagt generering kan kjøres."
                    ),
                ) as generate,
            ):
                result = self.client.post(
                    reverse("gui_v2:radio_workbench_bulk"),
                    {
                        "mode": "apply",
                        "plan": preview.context["plan"],
                        f"metadata_{self.recording.pk}": "yes",
                    },
                )
            generate.assert_called_once()
            self.assertFalse(result.context["results"][0]["success"])
            self.assertContains(result, "Bare en planlagt generering")
            self.assertNotContains(result, "Kandidat generert og verifisert")
