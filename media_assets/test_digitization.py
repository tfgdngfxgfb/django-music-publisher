import hashlib
import tempfile
from pathlib import Path
from urllib.parse import quote
from unittest.mock import patch

import numpy as np
import soundfile as sf
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalogue.models import Recording, Release, ReleaseTrack
from flac_ingest.maintenance import _protection_map
from managed_music.models import ManagedRelease
from media_assets.digitization import (
    apply_plan,
    delete_empty_batch,
    preview_operation,
    preview_registration,
    suggest_tracks,
)
from media_assets.mastering import select_master
from media_assets.models import (
    DigitizationBatch,
    DigitizationDerivation,
    DigitizationFile,
    DigitizationPlan,
    FileAsset,
    FileDerivation,
    MediaAssetEvent,
    RadioFlacGeneration,
    RecordingMediaSelection,
)


class DigitizationWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "digitizer", "", "test"
        )
        self.release = Release.objects.create(
            title="Arkivutgivelsen", release_year=1978
        )
        self.batch = DigitizationBatch.objects.create(
            release=self.release,
            title="Digitalisering 2026",
            created_by=self.user,
        )
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.override = self.settings(
            GUI_V2_WRITES_ENABLED=True,
            P7_ALLOW_FILE_WRITES=False,
            P7_STORAGE_ROOTS={
                "capture": {
                    "server_root": str(self.root),
                    "client_root": r"\\TEST-CLIENT\Capture",
                }
            },
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        for folder, names in (
            ("raw", ["Side A.wav", "Side B.wav"]),
            (
                "edited",
                ["01 Sang en.wav", "02 Sang to.wav", "03 Sang tre.wav"],
            ),
        ):
            (self.root / folder).mkdir()
            for name in names:
                sf.write(
                    self.root / folder / name,
                    np.zeros((100, 2), dtype=np.int32),
                    48000,
                    subtype="PCM_24",
                )

    def register(self, folder, role, batch=None):
        batch = batch or self.batch
        plan = preview_registration(
            batch=batch,
            root_key="capture",
            relative_path=folder,
            role=role,
            user=self.user,
        )
        apply_plan(plan=plan, user=self.user)
        return list(
            FileAsset.objects.filter(
                digitization_file__batch=batch, role=role
            ).order_by("filename")
        )

    def test_picker_rejects_conflicting_role_and_foreign_file_membership(self):
        from django.http import QueryDict
        from gui_v2.digitization import _browse_storage

        assets = self.register("raw", FileAsset.Role.RAW_DIGITIZATION)
        for asset in assets:
            location = asset.locations.get(is_current=True)
            location.storage_root_key = "music_library"
            location.save()
        params = QueryDict(mutable=True)
        params.update(
            {
                "browse": "1",
                "root_key": "music_library",
                "relative_path": "raw",
                "role": FileAsset.Role.RAW_DIGITIZATION,
            }
        )
        with self.settings(P7_MUSIC_ROOT=str(self.root)):
            form, entries, error, _ = _browse_storage(self.batch, params)
            self.assertTrue(form.is_valid(), form.errors)
            self.assertFalse(error)
            self.assertEqual(len(entries), 2)
            self.assertTrue(all(item["registered_here"] for item in entries))
            self.assertFalse(any(item["unavailable"] for item in entries))
            params["role"] = FileAsset.Role.EDITED_WAV_MASTER
            _, entries, error, _ = _browse_storage(self.batch, params)
            self.assertFalse(error)
            self.assertEqual(len(entries), 2)
            self.assertTrue(all(item["unavailable"] for item in entries))
            self.assertFalse(any(item["registered_here"] for item in entries))
            # A registered asset outside digitization needs explicit review.
            DigitizationFile.objects.filter(asset=assets[0]).delete()
            params["role"] = FileAsset.Role.RAW_DIGITIZATION
            _, entries, _, _ = _browse_storage(self.batch, params)
            affected = next(
                item
                for item in entries
                if item["path"]
                == assets[0].locations.get(is_current=True).relative_path
            )
            self.assertTrue(affected["unavailable"])

    def prepare(self):
        return (
            self.register("raw", FileAsset.Role.RAW_DIGITIZATION),
            self.register("edited", FileAsset.Role.EDITED_WAV_MASTER),
        )

    def preview(self, operation, payload, batch=None):
        return preview_operation(
            batch=batch or self.batch,
            operation=operation,
            payload=payload,
            user=self.user,
        )

    def apply(self, operation, payload, batch=None):
        plan = self.preview(operation, payload, batch)
        return apply_plan(plan=plan, user=self.user)

    def link(self, masters):
        tracks = []
        for number, asset in enumerate(masters, 1):
            recording = Recording.objects.create(title=f"Sang {number}")
            tracks.append(
                ReleaseTrack.objects.create(
                    release=self.release,
                    recording=recording,
                    sequence_number=number,
                    side="A",
                    track_number=number,
                )
            )
        self.apply(
            "recording_link",
            {
                "rows": [
                    {"asset": str(asset.pk), "track": str(track.pk)}
                    for asset, track in zip(masters, tracks)
                ]
            },
        )
        return tracks

    def test_raw_and_edited_master_are_separate_browsers(self):
        self.client.force_login(self.user)
        url = reverse("gui_v2:digitization_detail", args=[self.batch.pk])
        with self.settings(P7_MUSIC_ROOT=str(self.root)):
            before = FileAsset.objects.count()
            page = self.client.get(url)
            self.assertContains(page, "Finn RAW-filer")
            self.assertContains(page, "Finn redigerte mastere")
            self.assertContains(page, 'id="raw-picker"')
            self.assertContains(page, 'id="master-picker"')
            self.assertContains(page, "digitization-picker-layout", count=2)
            browsed = self.client.get(
                url,
                {
                    "browse": "1",
                    "role": FileAsset.Role.EDITED_WAV_MASTER,
                    "root_key": "music_library",
                    "relative_path": "edited",
                },
            )
            self.assertContains(browsed, "01 Sang en.wav")
            self.assertContains(browsed, "Størrelse")
            self.assertEqual(FileAsset.objects.count(), before)
            fragment = self.client.get(
                reverse(
                    "gui_v2:digitization_browse_files",
                    args=[self.batch.pk],
                ),
                {
                    "browse": "1",
                    "role": FileAsset.Role.EDITED_WAV_MASTER,
                    "root_key": "music_library",
                    "relative_path": "edited",
                    "file_q": "01 Sang",
                },
            )
            self.assertContains(fragment, 'id="master-picker"')
            self.assertContains(fragment, "digitization-picker-layout")
            self.assertContains(fragment, "digitization-folder-tree")
            self.assertContains(fragment, "Filer i valgt mappe")
            self.assertContains(fragment, "WAV-filer: 1")
            self.assertContains(fragment, "01 Sang en.wav")
            self.assertNotContains(fragment, "02 Sang to.wav")
            self.assertNotContains(fragment, "v2-player")
            self.assertEqual(FileAsset.objects.count(), before)
            preview = self.client.post(
                url,
                {
                    "operation": "register",
                    "selection_mode": "selected",
                    "root_key": "music_library",
                    "role": FileAsset.Role.EDITED_WAV_MASTER,
                    "relative_path": "edited",
                    "filenames": ["01 Sang en.wav"],
                },
            )
            self.assertContains(preview, "Forhåndsvisning")
            self.assertNotContains(preview, "02 Sang to.wav")
            self.assertEqual(FileAsset.objects.count(), before)

    def test_digitization_can_finish_without_radio_flac(self):
        raw, masters = self.prepare()
        self.link(masters)
        self.apply(
            "raw_link",
            {
                "assets": [str(master.pk) for master in masters],
                "source": str(raw[0].pk),
            },
        )
        self.apply(
            "select_master", {"assets": [str(master.pk) for master in masters]}
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("gui_v2:digitization_detail", args=[self.batch.pk]),
            {"operation": "finish"},
        )
        self.assertEqual(response.status_code, 302)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, DigitizationBatch.Status.COMPLETE)
        self.assertEqual(
            FileAsset.objects.filter(role=FileAsset.Role.RADIO_FLAC).count(), 0
        )

    def test_start_uses_existing_or_new_release_and_returns_to_guide(self):
        self.client.force_login(self.user)
        url = reverse("gui_v2:digitization_start")
        existing = self.client.post(
            url, {"mode": "existing", "release_id": str(self.release.pk)}
        )
        self.assertEqual(existing.status_code, 302)
        self.assertIn("step=metadata", existing.url)
        self.assertEqual(
            DigitizationBatch.objects.filter(release=self.release).count(), 2
        )
        new = self.client.post(
            url,
            {
                "mode": "new",
                "release-title": "Ny kassett",
                "release-release_type": Release.Type.CASSETTE,
                "release-release_year": 1986,
            },
        )
        self.assertEqual(new.status_code, 302)
        created = Release.objects.get(title="Ny kassett")
        self.assertIn("step=metadata", new.url)
        self.assertTrue(
            DigitizationBatch.objects.filter(release=created).exists()
        )
        destination = reverse(
            "gui_v2:digitization_detail",
            args=[DigitizationBatch.objects.get(release=created).pk],
        )
        saved = self.client.post(
            f"{reverse('gui_v2:release_detail', args=[created.pk])}"
            f"?tab=details&return={quote(destination, safe='')}",
            {
                "action": "release",
                "release-title": "Ny kassett",
                "release-release_type": Release.Type.CASSETTE,
                "release-release_year": "1986",
                "release-verification_status": "unverified",
            },
        )
        self.assertEqual(saved.status_code, 302)
        self.assertIn("return=", saved.url)

    def test_registration_preview_and_apply_read_only_and_idempotent(self):
        paths = list(self.root.rglob("*.wav"))
        before = {
            p: (
                hashlib.sha256(p.read_bytes()).hexdigest(),
                p.stat().st_mtime_ns,
            )
            for p in paths
        }
        raw, masters = self.prepare()
        self.assertEqual(len(raw), 2)
        self.assertEqual(len(masters), 3)
        self.assertTrue(
            all(asset.recording_id is None for asset in raw + masters)
        )
        self.assertTrue(
            all(asset.release_id == self.release.pk for asset in raw)
        )
        self.register("raw", FileAsset.Role.RAW_DIGITIZATION)
        self.assertEqual(DigitizationFile.objects.count(), 5)
        self.assertEqual(
            before,
            {
                p: (
                    hashlib.sha256(p.read_bytes()).hexdigest(),
                    p.stat().st_mtime_ns,
                )
                for p in paths
            },
        )

    def test_raw_links_multiple_sources_and_correction_preserves_history(self):
        raw, masters = self.prepare()
        self.apply(
            "raw_link",
            {"source": str(raw[0].pk), "assets": [str(a.pk) for a in masters]},
        )
        with self.assertRaises(ValidationError):
            self.preview(
                "raw_link",
                {"source": str(raw[1].pk), "assets": [str(masters[2].pk)]},
            )
        self.apply(
            "raw_link",
            {
                "source": str(raw[1].pk),
                "assets": [str(masters[2].pk)],
                "note": "Spor tre kom fra side B",
            },
        )
        self.assertEqual(DigitizationDerivation.objects.count(), 4)
        self.assertEqual(
            DigitizationDerivation.objects.filter(is_active=True).count(), 3
        )
        self.assertTrue(
            MediaAssetEvent.objects.filter(
                details__note="Spor tre kom fra side B"
            ).exists()
        )

    def test_raw_link_bulk_rollback_on_second_event_failure(self):
        raw, masters = self.prepare()
        plan = self.preview(
            "raw_link",
            {"source": str(raw[0].pk), "assets": [str(a.pk) for a in masters]},
        )
        from media_assets.digitization import _event

        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise IntegrityError("simulated")
            return _event(*args, **kwargs)

        with patch(
            "media_assets.digitization._event", side_effect=fail_second
        ), self.assertRaises(IntegrityError):
            apply_plan(plan=plan, user=self.user)
        self.assertEqual(DigitizationDerivation.objects.count(), 0)
        plan.refresh_from_db()
        self.assertIsNone(plan.applied_at)

    def test_recording_link_bulk_and_selected_master_use_4_5a(self):
        raw, masters = self.prepare()
        tracks = self.link(masters)
        self.apply("select_master", {"assets": [str(a.pk) for a in masters]})
        for asset, track in zip(masters, tracks):
            asset.refresh_from_db()
            self.assertEqual(asset.recording_id, track.recording_id)
            self.assertEqual(asset.release_track_id, track.pk)
            self.assertIsNone(asset.release_id)
            self.assertEqual(
                RecordingMediaSelection.objects.get(
                    recording=track.recording
                ).selected_master_id,
                asset.pk,
            )
        protected = _protection_map([t.recording_id for t in tracks])
        self.assertTrue(all(protected[t.recording_id] for t in tracks))

    def test_only_linked_master_is_selected_and_shown_in_final_step(self):
        _, masters = self.prepare()
        track = ReleaseTrack.objects.create(
            release=self.release,
            recording=Recording.objects.create(title="Sang en"),
            sequence_number=1,
        )

        plan = self.preview(
            "recording_link",
            {"rows": [{"asset": str(masters[0].pk), "track": str(track.pk)}]},
        )
        self.assertIn("valgt automatisk", " ".join(plan.consequences))
        apply_plan(plan=plan, user=self.user)
        self.assertEqual(
            RecordingMediaSelection.objects.get(
                recording=track.recording
            ).selected_master_id,
            masters[0].pk,
        )
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("gui_v2:digitization_detail", args=[self.batch.pk]),
            {"step": "radio"},
        )
        row = response.context["workspace"]["pipeline"][0]
        self.assertEqual(row["master_label"], "Valgt")
        self.assertNotContains(response, "Koble og velg master</a>")
        selection = RecordingMediaSelection.objects.get(
            recording=track.recording
        )
        selection.selected_master = None
        selection.save()
        legacy_response = self.client.get(
            reverse("gui_v2:digitization_detail", args=[self.batch.pk]),
            {"step": "radio"},
        )
        self.assertEqual(
            legacy_response.context["workspace"]["pipeline"][0][
                "master_label"
            ],
            "Tilknyttet – ikke valgt",
        )

    def test_two_masters_for_recording_require_explicit_choice(self):
        _, masters = self.prepare()
        track = ReleaseTrack.objects.create(
            release=self.release,
            recording=Recording.objects.create(title="Sang en"),
            sequence_number=1,
        )
        self.apply(
            "recording_link",
            {
                "rows": [
                    {"asset": str(asset.pk), "track": str(track.pk)}
                    for asset in masters[:2]
                ]
            },
        )
        self.assertFalse(
            RecordingMediaSelection.objects.filter(
                recording=track.recording,
                selected_master__isnull=False,
            ).exists()
        )
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("gui_v2:digitization_detail", args=[self.batch.pk]),
            {"step": "radio"},
        )
        self.assertEqual(
            response.context["workspace"]["pipeline"][0]["master_label"],
            "Tilknyttet – ikke valgt",
        )

    def test_corrections_unlink_without_deleting_history_and_reopen_batch(
        self,
    ):
        raws, masters = self.prepare()
        track = ReleaseTrack.objects.create(
            release=self.release,
            recording=Recording.objects.create(title="Feilkoblet sang"),
            sequence_number=1,
        )
        self.apply(
            "recording_link",
            {"rows": [{"asset": str(masters[0].pk), "track": str(track.pk)}]},
        )
        self.apply(
            "raw_link",
            {"source": str(raws[0].pk), "assets": [str(masters[0].pk)]},
        )
        self.batch.status = DigitizationBatch.Status.COMPLETE
        self.batch.save()

        self.apply(
            "unlink_raw",
            {
                "assets": [str(masters[0].pk)],
                "note": "Feil side ble valgt",
            },
        )
        relation = DigitizationDerivation.objects.get(derived_asset=masters[0])
        self.assertFalse(relation.is_active)
        self.assertEqual(relation.source_asset, raws[0])
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, DigitizationBatch.Status.OPEN)

        self.apply(
            "clear_master_selection",
            {
                "assets": [str(masters[0].pk)],
                "note": "Feil masterversjon",
            },
        )
        self.assertIsNone(
            RecordingMediaSelection.objects.get(
                recording=track.recording
            ).selected_master_id
        )
        self.apply(
            "unlink_recording",
            {
                "assets": [str(masters[0].pk)],
                "note": "Masteren tilhører et annet spor",
            },
        )
        masters[0].refresh_from_db()
        self.assertIsNone(masters[0].recording_id)
        self.assertIsNone(masters[0].release_track_id)
        self.assertEqual(masters[0].release_id, self.release.pk)
        self.assertTrue(
            Recording.objects.filter(pk=track.recording_id).exists()
        )
        self.assertTrue(ReleaseTrack.objects.filter(pk=track.pk).exists())
        self.assertTrue(
            {
                MediaAssetEvent.EventType.RECORDING_LINKED,
                MediaAssetEvent.EventType.MASTER_SELECTED,
                MediaAssetEvent.EventType.RAW_LINKED,
                MediaAssetEvent.EventType.RAW_UNLINKED,
                MediaAssetEvent.EventType.MASTER_SELECTION_CLEARED,
                MediaAssetEvent.EventType.RECORDING_UNLINKED,
            }.issubset(
                set(
                    MediaAssetEvent.objects.filter(
                        asset=masters[0]
                    ).values_list("event_type", flat=True)
                )
            )
        )

    def test_corrections_require_note_and_protect_generated_lineage(self):
        _, masters = self.prepare()
        track = ReleaseTrack.objects.create(
            release=self.release,
            recording=Recording.objects.create(title="Beskyttet sang"),
            sequence_number=1,
        )
        self.apply(
            "recording_link",
            {"rows": [{"asset": str(masters[0].pk), "track": str(track.pk)}]},
        )
        with self.assertRaisesMessage(ValidationError, "Oppgi hvorfor"):
            self.preview(
                "clear_master_selection",
                {"assets": [str(masters[0].pk)], "note": ""},
            )
        self.apply(
            "clear_master_selection",
            {"assets": [str(masters[0].pk)], "note": "Skal korrigeres"},
        )
        masters[0].refresh_from_db()
        radio = FileAsset.objects.create(
            recording=track.recording,
            filename="historisk.flac",
            role=FileAsset.Role.RADIO_FLAC,
        )
        FileDerivation.objects.create(
            source_asset=masters[0],
            derived_asset=radio,
            created_by=self.user,
            tool_name="test",
        )
        with self.assertRaisesMessage(ValidationError, "avledningshistorikk"):
            self.preview(
                "unlink_recording",
                {
                    "assets": [str(masters[0].pk)],
                    "note": "Forsøk på farlig frakobling",
                },
            )

    def test_correction_actions_are_visible_for_linked_master(self):
        _, masters = self.prepare()
        track = ReleaseTrack.objects.create(
            release=self.release,
            recording=Recording.objects.create(title="Sang en"),
            sequence_number=1,
        )
        self.apply(
            "recording_link",
            {"rows": [{"asset": str(masters[0].pk), "track": str(track.pk)}]},
        )
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("gui_v2:digitization_detail", args=[self.batch.pk])
        )
        self.assertContains(response, "Korriger koblinger")
        self.assertContains(response, "Fjern mastervalg")
        self.assertContains(response, "Fjern innspillingskobling")

    def test_recording_link_rolls_back_new_recordings_and_assignments(self):
        _, masters = self.prepare()
        plan = self.preview(
            "recording_link",
            {
                "rows": [
                    {"asset": str(a.pk), "new_title": f"Ny {i}"}
                    for i, a in enumerate(masters)
                ]
            },
        )
        with patch(
            "media_assets.digitization._event",
            side_effect=IntegrityError("simulated"),
        ), self.assertRaises(IntegrityError):
            apply_plan(plan=plan, user=self.user)
        self.assertFalse(Recording.objects.exists())
        self.assertFalse(FileAsset.objects.exclude(recording=None).exists())

    def test_stale_preview_is_rejected_after_master_choice(self):
        _, masters = self.prepare()
        tracks = self.link(masters)
        plan = self.preview(
            "select_master", {"assets": [str(a.pk) for a in masters]}
        )
        select_master(
            recording=tracks[0].recording,
            asset=FileAsset.objects.get(pk=masters[0].pk),
            user=self.user,
        )
        with self.assertRaisesMessage(ValidationError, "Arbeidsgrunnlaget"):
            apply_plan(plan=plan, user=self.user)
        self.assertEqual(RecordingMediaSelection.objects.count(), 3)

    def test_relevant_recording_change_rejects_recording_link_preview(self):
        _, masters = self.prepare()
        recording = Recording.objects.create(title="Opprinnelig tittel")
        track = ReleaseTrack.objects.create(
            release=self.release,
            recording=recording,
            sequence_number=1,
        )
        plan = self.preview(
            "recording_link",
            {"rows": [{"asset": str(masters[0].pk), "track": str(track.pk)}]},
        )
        recording.title = "Endret etter forhåndsvisning"
        recording.save()

        with self.assertRaisesMessage(ValidationError, "Arbeidsgrunnlaget"):
            apply_plan(plan=plan, user=self.user)
        masters[0].refresh_from_db()
        self.assertIsNone(masters[0].recording_id)

    def test_irrelevant_recording_change_does_not_stale_raw_link_preview(self):
        raw, masters = self.prepare()
        recording = Recording.objects.create(title="Ikke del av råkoblingen")
        ReleaseTrack.objects.create(
            release=self.release,
            recording=recording,
            sequence_number=1,
        )
        plan = self.preview(
            "raw_link",
            {"source": str(raw[0].pk), "assets": [str(masters[0].pk)]},
        )
        recording.title = "Relevant for katalogen, ikke denne handlingen"
        recording.save()

        apply_plan(plan=plan, user=self.user)

        self.assertTrue(
            DigitizationDerivation.objects.filter(
                source_asset=raw[0], derived_asset=masters[0], is_active=True
            ).exists()
        )

    def test_multiple_masters_may_link_to_one_recording_but_not_bulk_select(
        self,
    ):
        _, masters = self.prepare()
        recording = Recording.objects.create(title="Samme innspilling")
        track = ReleaseTrack.objects.create(
            release=self.release,
            recording=recording,
            sequence_number=1,
        )
        self.apply(
            "recording_link",
            {
                "rows": [
                    {"asset": str(asset.pk), "track": str(track.pk)}
                    for asset in masters[:2]
                ]
            },
        )
        self.assertEqual(
            FileAsset.objects.filter(
                recording=recording,
                role=FileAsset.Role.EDITED_WAV_MASTER,
            ).count(),
            2,
        )
        with self.assertRaisesMessage(
            ValidationError, "bare én master per innspilling"
        ):
            self.preview(
                "select_master",
                {"assets": [str(asset.pk) for asset in masters[:2]]},
            )

    def test_global_master_effect_and_release_title_button_are_visible(self):
        _, masters = self.prepare()
        tracks = self.link(masters)
        other_release = Release.objects.create(title="Samleutgivelse")
        ReleaseTrack.objects.create(
            release=other_release,
            recording=tracks[0].recording,
            sequence_number=1,
        )
        other_batch = DigitizationBatch.objects.create(
            release=other_release,
            title="Annet digitaliseringsforsøk",
            created_by=self.user,
        )
        other_master = FileAsset.objects.create(
            recording=tracks[0].recording,
            filename="Alternativ master.wav",
            role=FileAsset.Role.EDITED_WAV_MASTER,
        )
        DigitizationFile.objects.create(batch=other_batch, asset=other_master)
        select_master(
            recording=tracks[0].recording,
            asset=FileAsset.objects.get(pk=masters[0].pk),
            user=self.user,
        )
        current = FileAsset.objects.create(
            recording=tracks[0].recording,
            filename="legacy.flac",
            role=FileAsset.Role.RADIO_FLAC,
            lifecycle_status=FileAsset.LifecycleStatus.CURRENT,
        )
        selection = RecordingMediaSelection.objects.get(
            recording=tracks[0].recording
        )
        selection.current_radio = current
        selection.save()
        from gui_v2.digitization import batch_workspace

        other_workspace = batch_workspace(other_batch)
        self.assertEqual(
            other_workspace["masters"][0]["selection"].selected_master_id,
            masters[0].pk,
        )
        plan = self.preview("select_master", {"assets": [str(masters[0].pk)]})
        self.assertIn("forekommer på 2 utgivelser", plan.consequences[0])
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("gui_v2:digitization_detail", args=[self.batch.pk])
        )
        self.assertContains(response, "Gjelder 2 utgivelser")
        self.assertContains(response, "Masteropprinnelse ukjent")
        index = self.client.get(reverse("gui_v2:digitization_index"))
        self.assertContains(index, "Start ny digitalisering")
        start = self.client.get(reverse("gui_v2:digitization_start"))
        self.assertContains(start, "Opprett ny utgivelse")

    def test_redigitization_and_partial_replacement_preserve_previous_choice(
        self,
    ):
        _, masters = self.prepare()
        tracks = self.link(masters)
        self.apply("select_master", {"assets": [str(a.pk) for a in masters]})
        batch2 = DigitizationBatch.objects.create(
            release=self.release, title="Nytt forsøk", created_by=self.user
        )
        (self.root / "new").mkdir()
        sf.write(
            self.root / "new" / "01.wav",
            np.zeros((100, 2)),
            48000,
            subtype="PCM_16",
        )
        new = self.register("new", FileAsset.Role.EDITED_WAV_MASTER, batch2)[0]
        self.apply(
            "recording_link",
            {"rows": [{"asset": str(new.pk), "track": str(tracks[0].pk)}]},
            batch2,
        )
        with self.assertRaisesMessage(
            ValidationError, "allerede valgt master"
        ):
            self.preview("select_master", {"assets": [str(new.pk)]}, batch2)
        new.refresh_from_db()
        select_master(recording=tracks[0].recording, asset=new, user=self.user)
        self.assertEqual(
            RecordingMediaSelection.objects.get(
                recording=tracks[1].recording
            ).selected_master_id,
            masters[1].pk,
        )
        self.assertEqual(
            DigitizationBatch.objects.filter(release=self.release).count(), 2
        )
        self.assertTrue(FileAsset.objects.filter(pk=masters[0].pk).exists())

    def test_wrong_batch_and_self_reference_rejected(self):
        raw, masters = self.prepare()
        other = DigitizationBatch.objects.create(
            release=self.release, title="Annet forsøk", created_by=self.user
        )
        with self.assertRaises(ValidationError):
            self.preview(
                "raw_link",
                {"source": str(raw[0].pk), "assets": [str(masters[0].pk)]},
                other,
            )
        with self.assertRaises(ValidationError):
            DigitizationDerivation.objects.create(
                source_asset=raw[0], derived_asset=raw[0], created_by=self.user
            )
        with self.assertRaises(ValidationError):
            raw[0].recording = Recording.objects.create(
                title="Ugyldig råknytning"
            )
            raw[0].release = None
            raw[0].save()

    def test_only_one_active_source_database_constraint(self):
        raw, masters = self.prepare()
        first = DigitizationDerivation.objects.create(
            source_asset=raw[0], derived_asset=masters[0], created_by=self.user
        )
        second = DigitizationDerivation.objects.create(
            source_asset=raw[1],
            derived_asset=masters[0],
            is_active=False,
            created_by=self.user,
        )
        from django.db import connection

        with self.assertRaises(
            IntegrityError
        ), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE media_assets_digitizationderivation SET is_active = %s WHERE id = %s",
                [
                    True,
                    (
                        second.pk.hex
                        if connection.vendor == "sqlite"
                        else second.pk
                    ),
                ],
            )
        self.assertTrue(
            DigitizationDerivation.objects.get(pk=first.pk).is_active
        )

    def test_same_file_cannot_join_two_batches(self):
        self.prepare()
        other = DigitizationBatch.objects.create(
            release=self.release, title="Annet forsøk", created_by=self.user
        )
        with self.assertRaises(ValidationError):
            self.register("raw", FileAsset.Role.RAW_DIGITIZATION, other)

    def test_changed_source_file_invalidates_registration_preview(self):
        plan = preview_registration(
            batch=self.batch,
            root_key="capture",
            relative_path="raw",
            role=FileAsset.Role.RAW_DIGITIZATION,
            user=self.user,
        )
        sf.write(
            self.root / "raw" / "Side A.wav",
            np.ones((200, 2)),
            44100,
            subtype="PCM_16",
        )
        with self.assertRaisesMessage(ValidationError, "endret siden"):
            apply_plan(plan=plan, user=self.user)
        self.assertEqual(DigitizationFile.objects.count(), 0)

    def test_preview_is_read_only_and_repeated_apply_is_noop(self):
        raw, masters = self.prepare()
        plan = self.preview(
            "raw_link",
            {"source": str(raw[0].pk), "assets": [str(masters[0].pk)]},
        )
        self.assertEqual(DigitizationDerivation.objects.count(), 0)
        apply_plan(plan=plan, user=self.user)
        apply_plan(plan=plan, user=self.user)
        self.assertEqual(DigitizationDerivation.objects.count(), 1)

    def test_suggestions_report_ambiguity_without_assignment(self):
        _, masters = self.prepare()
        one = Recording.objects.create(title="Sang en")
        other = Recording.objects.create(title="Sang en")
        tracks = [
            ReleaseTrack.objects.create(
                release=self.release, recording=r, sequence_number=n
            )
            for n, r in enumerate((one, other), 1)
        ]
        suggestion = suggest_tracks(masters[0], tracks)
        self.assertEqual(suggestion["status"], "Tvetydig")
        self.assertEqual(len(suggestion["choices"]), 2)
        self.assertIsNone(masters[0].recording_id)

    def test_combined_links_preview_apply_and_rollback(self):
        raw, masters = self.prepare()
        tracks = [
            ReleaseTrack.objects.create(
                release=self.release,
                recording=Recording.objects.create(title=f"Spor {n}"),
                sequence_number=n,
                side="A",
                track_number=n,
            )
            for n in range(1, 4)
        ]
        plan = self.preview(
            "link_masters",
            {
                "rows": [
                    {
                        "asset": str(master.pk),
                        "track": str(track.pk),
                        "source": str(raw[0].pk),
                    }
                    for master, track in zip(masters, tracks)
                ]
            },
        )
        self.assertFalse(DigitizationDerivation.objects.exists())
        from media_assets.digitization import _apply_raw_links

        calls = 0

        def fail_second(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValidationError("Simulert feil i andre råkobling")
            return _apply_raw_links(*args)

        events_before = MediaAssetEvent.objects.count()
        with patch(
            "media_assets.digitization._apply_raw_links",
            side_effect=fail_second,
        ):
            with self.assertRaises(ValidationError):
                apply_plan(plan=plan, user=self.user)
        self.assertFalse(DigitizationDerivation.objects.exists())
        self.assertEqual(MediaAssetEvent.objects.count(), events_before)
        self.assertFalse(RecordingMediaSelection.objects.exists())
        self.assertFalse(
            FileAsset.objects.filter(
                pk__in=[m.pk for m in masters], recording__isnull=False
            ).exists()
        )
        apply_plan(plan=plan, user=self.user)
        self.assertEqual(DigitizationDerivation.objects.count(), 3)
        self.assertEqual(
            RecordingMediaSelection.objects.filter(
                selected_master__in=masters
            ).count(),
            3,
        )
        self.assertEqual(
            FileAsset.objects.filter(
                pk__in=[m.pk for m in masters], recording__isnull=False
            ).count(),
            3,
        )

    def test_folder_conventions_use_release_and_allow_browsing_without_path_input(
        self,
    ):
        from catalogue.models import Label
        from media_assets.digitization_storage import suggested_folder

        self.release.label = Label.objects.create(name="Klango")
        self.release.catalogue_number = "FMC 102"
        self.release.title = "Veien hjem"
        self.release.save()
        for folder in ("Klango/FMC", "Klango/FMC 102 - Veien hjem"):
            (self.root / folder).mkdir(parents=True)
        raw = suggested_folder(
            self.release, FileAsset.Role.RAW_DIGITIZATION, "capture"
        )
        master = suggested_folder(
            self.release, FileAsset.Role.EDITED_WAV_MASTER, "capture"
        )
        self.assertEqual(raw["path"], "Klango/FMC")
        self.assertEqual(master["path"], "Klango/FMC 102 - Veien hjem")
        with self.settings(P7_RAW_FOLDER_TEMPLATE="{label}/annen/{series}"):
            fallback = suggested_folder(
                self.release, FileAsset.Role.RAW_DIGITIZATION, "capture"
            )
            self.assertEqual(fallback["path"], "Klango")
            self.assertFalse(fallback["matched"])
        self.client.force_login(self.user)
        with self.settings(
            P7_STORAGE_ROOTS={"raw_sources": {"server_root": str(self.root)}}
        ):
            page = self.client.get(
                reverse(
                    "gui_v2:digitization_browse_files", args=[self.batch.pk]
                ),
                {
                    "browse": "1",
                    "suggest": "1",
                    "root_key": "raw_sources",
                    "role": FileAsset.Role.RAW_DIGITIZATION,
                },
            )
        self.assertContains(page, "Klango/FMC")
        self.assertContains(page, 'type="hidden" name="relative_path"')
        self.assertNotContains(page, 'placeholder="."')

    def test_no_permission_and_other_preview_owner_rejected(self):
        raw, masters = self.prepare()
        plan = self.preview(
            "raw_link",
            {"source": str(raw[0].pk), "assets": [str(masters[0].pk)]},
        )
        viewer = get_user_model().objects.create_user("viewer")
        with self.assertRaises(PermissionDenied):
            apply_plan(plan=plan, user=viewer)
        admin = get_user_model().objects.create_superuser(
            "other-admin", "", "test"
        )
        with self.assertRaises(PermissionDenied):
            apply_plan(plan=plan, user=admin)

    def test_gui_preview_apply_selection_and_client_path(self):
        raw, masters = self.prepare()
        self.client.force_login(self.user)
        url = reverse("gui_v2:digitization_detail", args=[self.batch.pk])
        with patch(
            "media_assets.storage.location_exists",
            side_effect=AssertionError("live lookup"),
        ):
            response = self.client.get(url)
        self.assertContains(
            response, r"\\TEST-CLIENT\Capture\edited\01 Sang en.wav"
        )
        self.assertNotContains(response, str(self.root))
        self.assertContains(response, "Spor og mastere")
        self.assertContains(response, "data-digitization-row")
        response = self.client.post(
            url,
            {
                "operation": "raw_link",
                "source": raw[0].pk,
                "assets": [masters[0].pk],
            },
        )
        self.assertContains(response, "Bekreft de viste endringene")
        self.assertFalse(DigitizationDerivation.objects.exists())
        plan = response.context["plan"]
        response = self.client.post(
            url, {"operation": "apply", "plan": plan.pk, "confirmed": "yes"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DigitizationDerivation.objects.count(), 1)

    def test_gui_empty_and_permissions(self):
        self.client.force_login(self.user)
        self.assertContains(
            self.client.get(
                reverse("gui_v2:digitization_detail", args=[self.batch.pk])
            ),
            "Ingen redigerte mastere",
        )
        self.assertContains(
            self.client.get(reverse("gui_v2:digitization_index")),
            "Arkivutgivelsen",
        )
        self.client.force_login(get_user_model().objects.create_user("reader"))
        self.assertEqual(
            self.client.get(reverse("gui_v2:digitization_index")).status_code,
            403,
        )

    def test_release_matrix_keeps_selected_candidate_and_current_separate(
        self,
    ):
        raw, masters = self.prepare()
        tracks = self.link(masters)
        masters[0].refresh_from_db()
        self.apply(
            "raw_link",
            {"source": str(raw[0].pk), "assets": [str(masters[0].pk)]},
        )
        recording = tracks[0].recording
        second = FileAsset.objects.create(
            recording=recording,
            filename="master-v2.wav",
            role=FileAsset.Role.EDITED_WAV_MASTER,
        )
        current = FileAsset.objects.create(
            recording=recording,
            filename="radio-v1.flac",
            role=FileAsset.Role.RADIO_FLAC,
            lifecycle_status=FileAsset.LifecycleStatus.CURRENT,
        )
        candidate = FileAsset.objects.create(
            recording=recording,
            filename="radio-v2.flac",
            role=FileAsset.Role.RADIO_FLAC,
            lifecycle_status=FileAsset.LifecycleStatus.CANDIDATE,
        )
        selection = RecordingMediaSelection.objects.get(recording=recording)
        selection.selected_master = second
        selection.current_radio = current
        selection.save()
        for source, derived in ((masters[0], current), (second, candidate)):
            FileDerivation.objects.create(
                source_asset=source,
                derived_asset=derived,
                created_by=self.user,
                tool_name="test",
            )
        generation = RadioFlacGeneration.objects.create(
            recording=recording,
            master_asset=second,
            candidate_asset=candidate,
            target_root_key="capture",
            target_relative_path="radio/radio-v2.flac",
            technical_plan={"format": "FLAC"},
            expected_tags={"TITLE": [recording.title]},
            metadata_diff=["test"],
            status=RadioFlacGeneration.Status.VERIFIED,
            created_by=self.user,
        )
        self.client.force_login(self.user)
        url = reverse(
            "gui_v2:digitization_release_matrix", args=[self.release.pk]
        )
        before = (
            FileAsset.objects.count(),
            RecordingMediaSelection.objects.count(),
            RadioFlacGeneration.objects.count(),
        )
        with patch(
            "media_assets.storage.location_exists",
            side_effect=AssertionError("live lookup"),
        ):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "komplett utgivelsesmatrise")
        self.assertContains(response, "master-v2.wav")
        self.assertContains(response, "radio-v2.flac")
        self.assertContains(response, "radio-v1.flac")
        self.assertContains(response, "Fra tidligere master")
        self.assertContains(response, "Ny radiofil klar til aktivering")
        self.assertContains(response, f"generation={generation.pk}")
        self.assertContains(response, "data-matrix-row")
        self.assertContains(
            response, 'data-current-state="from_previous_master"'
        )
        self.assertContains(response, 'data-has-candidate="true"')
        self.assertEqual(
            response.context["workspace"]["rows"][0]["raw_sources"], [raw[0]]
        )
        self.assertEqual(
            (
                FileAsset.objects.count(),
                RecordingMediaSelection.objects.count(),
                RadioFlacGeneration.objects.count(),
            ),
            before,
        )
        dashboard = self.client.get(reverse("gui_v2:digitization_index"))
        progress = list(dashboard.context["page"])[0].progress
        self.assertEqual(
            (
                progress["total"],
                progress["linked"],
                progress["selected"],
                progress["radio"],
            ),
            (3, 3, 3, 1),
        )
        batch_page = self.client.get(
            reverse("gui_v2:digitization_detail", args=[self.batch.pk])
        )
        self.assertContains(batch_page, 'data-raw-detail="')
        files_page = self.client.get(
            reverse("gui_v2:recording_files", args=[recording.pk])
        )
        self.assertContains(files_page, "Master- og radiohistorikk")
        self.assertContains(files_page, "Fra tidligere master")
        preview = self.client.get(
            reverse(
                "gui_v2:recording_generation_preview", args=[recording.pk]
            ),
            {"generation": generation.pk},
        )
        self.assertContains(preview, "radio-v1.flac")
        self.assertContains(preview, "radio-v2.flac")
        read_only_user = get_user_model().objects.create_user("matrix-reader")
        for app_label, codename in (
            ("catalogue", "view_recording"),
            ("media_assets", "view_fileasset"),
            ("media_assets", "view_filelocation"),
        ):
            read_only_user.user_permissions.add(
                Permission.objects.get(
                    content_type__app_label=app_label, codename=codename
                )
            )
        self.client.force_login(read_only_user)
        read_only_page = self.client.get(
            reverse(
                "gui_v2:recording_generation_preview", args=[recording.pk]
            ),
            {"generation": generation.pk},
        )
        self.assertEqual(read_only_page.status_code, 200)
        self.assertNotContains(
            read_only_page, "Aktiver som gjeldende radiofil"
        )
        files_read_only = self.client.get(
            reverse("gui_v2:recording_files", args=[recording.pk])
        )
        self.assertNotContains(files_read_only, "Velg som master")
        viewer = get_user_model().objects.create_user("matrix-viewer")
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_release_matrix_unknown_lineage_is_not_a_generation_decision(self):
        _, masters = self.prepare()
        track = self.link(masters)[0]
        masters[0].refresh_from_db()
        current = FileAsset.objects.create(
            recording=track.recording,
            filename="legacy-current.flac",
            role=FileAsset.Role.RADIO_FLAC,
            lifecycle_status=FileAsset.LifecycleStatus.CURRENT,
        )
        selection = RecordingMediaSelection.objects.get(
            recording=track.recording
        )
        selection.current_radio = current
        selection.save()
        self.client.force_login(self.user)
        response = self.client.get(
            reverse(
                "gui_v2:digitization_release_matrix", args=[self.release.pk]
            )
        )
        self.assertEqual(
            response.context["workspace"]["rows"][0]["filter_key"],
            "unknown_lineage",
        )
        self.assertContains(response, "masteropprinnelse er ikke dokumentert")

    def test_release_matrix_does_not_query_per_track(self):
        self.client.force_login(self.user)
        url = reverse(
            "gui_v2:digitization_release_matrix", args=[self.release.pk]
        )
        ReleaseTrack.objects.create(
            release=self.release,
            recording=Recording.objects.create(title="Spor 1"),
            sequence_number=1,
        )
        with CaptureQueriesContext(connection) as baseline:
            self.assertEqual(self.client.get(url).status_code, 200)
        for sequence in range(2, 9):
            ReleaseTrack.objects.create(
                release=self.release,
                recording=Recording.objects.create(title=f"Spor {sequence}"),
                sequence_number=sequence,
            )
        with CaptureQueriesContext(connection) as expanded:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["workspace"]["rows"]), 8)
        self.assertLessEqual(len(expanded), len(baseline) + 2)

    def test_index_shows_and_filters_release_management(self):
        ManagedRelease.objects.create(
            release=self.release,
            status=ManagedRelease.Status.ACTIVE,
            relationship=ManagedRelease.Relationship.MANAGED_CATALOGUE,
        )
        other_release = Release.objects.create(title="Ikke forvaltet")
        DigitizationBatch.objects.create(
            release=other_release,
            title="Annen digitalisering",
            created_by=self.user,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("gui_v2:digitization_index"))
        self.assertContains(response, "Forvaltet på vegne av andre")
        response = self.client.get(
            reverse("gui_v2:digitization_index"), {"management": "managed"}
        )
        self.assertEqual(
            [batch.release for batch in response.context["page"]],
            [self.release],
        )
        response = self.client.get(
            reverse("gui_v2:digitization_index"), {"management": "none"}
        )
        self.assertEqual(
            [batch.release for batch in response.context["page"]],
            [other_release],
        )

    def test_empty_batch_can_be_removed_without_removing_release(self):
        batch_id = self.batch.pk
        release_id = self.release.pk

        delete_empty_batch(batch=self.batch, user=self.user)

        self.assertFalse(
            DigitizationBatch.objects.filter(pk=batch_id).exists()
        )
        self.assertTrue(Release.objects.filter(pk=release_id).exists())

    def test_batch_with_registered_files_cannot_be_removed(self):
        self.register("raw", FileAsset.Role.RAW_DIGITIZATION)

        with self.assertRaisesMessage(ValidationError, "registrerte filer"):
            delete_empty_batch(batch=self.batch, user=self.user)

        self.assertTrue(
            DigitizationBatch.objects.filter(pk=self.batch.pk).exists()
        )

    def test_gui_requires_confirmation_before_removing_empty_batch(self):
        self.client.force_login(self.user)
        url = reverse("gui_v2:digitization_detail", args=[self.batch.pk])
        response = self.client.post(url, {"operation": "delete_batch"})
        self.assertContains(response, "Bekreft at digitaliseringsbatchen")
        self.assertTrue(
            DigitizationBatch.objects.filter(pk=self.batch.pk).exists()
        )

        response = self.client.post(
            url, {"operation": "delete_batch", "confirmed": "yes"}
        )
        self.assertRedirects(response, reverse("gui_v2:digitization_index"))
        self.assertFalse(
            DigitizationBatch.objects.filter(pk=self.batch.pk).exists()
        )
        self.assertTrue(Release.objects.filter(pk=self.release.pk).exists())

    def test_batch_render_queries_do_not_scale_with_fifty_masters(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from gui_v2.digitization import batch_workspace

        self.link(self.prepare()[1])
        with CaptureQueriesContext(connection) as small:
            workspace = batch_workspace(self.batch)
            list(workspace["events"])
        for number in range(4, 51):
            recording = Recording.objects.create(title=f"Spor {number}")
            track = ReleaseTrack.objects.create(
                release=self.release,
                recording=recording,
                sequence_number=number,
            )
            asset = FileAsset.objects.create(
                role=FileAsset.Role.EDITED_WAV_MASTER,
                recording=recording,
                release_track=track,
                filename=f"{number:02d} Spor.wav",
            )
            DigitizationFile.objects.create(batch=self.batch, asset=asset)
        with CaptureQueriesContext(connection) as large:
            workspace = batch_workspace(self.batch)
            list(workspace["events"])
        self.assertEqual(len(workspace["masters"]), 50)
        self.assertLessEqual(len(large), len(small) + 1)
        self.assertEqual(
            workspace["masters"][0]["asset"].filename, "01 Sang en.wav"
        )

    def test_generation_page_names_each_server_blocker(self):
        recording = Recording.objects.create(title="Uten master")
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("gui_v2:recording_generation_preview", args=[recording.pk])
        )
        self.assertContains(response, "P7_ALLOW_FILE_WRITES")
        self.assertContains(response, "P7_GENERATED_MEDIA_ROOT")
        self.assertContains(response, "Velg en autoritativ master")

    def test_existing_radio_target_offers_new_name_without_overwrite(self):
        from media_assets.mastering import build_generation_preview

        _, masters = self.prepare()
        recording = self.link(masters)[0].recording
        self.apply("select_master", {"assets": [str(masters[0].pk)]})
        roots = {
            "capture": {"server_root": str(self.root)},
            "generated_media": {
                "server_root": str(self.root),
                "read_only": False,
            },
        }
        with self.settings(P7_STORAGE_ROOTS=roots, P7_ALLOW_FILE_WRITES=True):
            preview = build_generation_preview(recording=recording)
            target = self.root / preview["target_relative_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"existing")
            self.client.force_login(self.user)
            url = reverse(
                "gui_v2:recording_generation_preview", args=[recording.pk]
            )
            response = self.client.get(url)
            self.assertContains(response, "Målfilen finnes allerede")
            self.assertNotContains(response, "Lagre plan")
            alternate = response.context["alternate_target"]
            self.assertNotEqual(alternate, preview["target_relative_path"])
            response = self.client.get(
                url, {"target_relative_path": alternate}
            )
            self.assertContains(response, "Lagre plan")
            self.assertEqual(target.read_bytes(), b"existing")
            self.assertFalse(RadioFlacGeneration.objects.exists())

    def test_first_radio_full_pipeline_and_rebuild_protection(self):
        from media_assets.mastering import (
            build_generation_preview,
            create_generation_plan,
            generate_candidate,
            activate_candidate,
        )
        from media_assets.playback import resolve_current_radio_asset
        from flac_ingest.maintenance import (
            create_rebuild_preview,
            execute_rebuild,
        )
        from music_library.models import MusicLibraryEntry
        from mutagen.flac import FLAC

        raw, masters = self.prepare()
        before = {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.root.rglob("*.wav")
        }
        tracks = self.link(masters)
        self.apply(
            "raw_link",
            {"source": str(raw[0].pk), "assets": [str(masters[0].pk)]},
        )
        self.apply("select_master", {"assets": [str(masters[0].pk)]})
        recording = tracks[0].recording
        entry = MusicLibraryEntry.objects.get(recording=recording)
        entry.genre = "Gospel"
        entry.language = "nb"
        entry.energy = 3
        entry.save()
        roots = {
            "capture": {"server_root": str(self.root)},
            "generated_media": {
                "server_root": str(self.root),
                "read_only": False,
            },
        }
        with self.settings(
            P7_STORAGE_ROOTS=roots,
            P7_MUSIC_ROOT=str(self.root),
            P7_NAS_ROOT=str(self.root),
            P7_ALLOW_FILE_WRITES=True,
        ):
            preview = build_generation_preview(recording=recording)
            self.assertTrue(preview["first_radio"])
            self.assertEqual(
                preview["expected_tags"]["ALBUM"], [self.release.title]
            )
            self.assertEqual(preview["expected_tags"]["GENRE"], ["Gospel"])
            self.assertNotIn("ARTIST", preview["expected_tags"])
            with self.assertRaisesMessage(ValidationError, "Bekreft"):
                create_generation_plan(recording=recording, user=self.user)
            with self.assertRaisesMessage(
                ValidationError, "Metadata er endret"
            ):
                create_generation_plan(
                    recording=recording,
                    user=self.user,
                    database_metadata_confirmed=True,
                    expected_metadata_digest="outdated",
                )
            plan = create_generation_plan(
                recording=recording,
                user=self.user,
                database_metadata_confirmed=True,
                expected_metadata_digest=preview["metadata_digest"],
            )
            self.assertEqual(
                create_generation_plan(
                    recording=recording,
                    user=self.user,
                    database_metadata_confirmed=True,
                ).pk,
                plan.pk,
            )
            generation = generate_candidate(generation=plan, user=self.user)
            activate_candidate(generation=generation, user=self.user)
            recording.refresh_from_db()
            playback = resolve_current_radio_asset(recording, verify_file=True)
            self.assertEqual(str(playback.status), "available")
            self.assertEqual(FLAC(playback.path)["TITLE"], [recording.title])
            self.client.force_login(self.user)
            response = self.client.get(
                reverse("gui_v2:recording_audio", args=[recording.pk]),
                HTTP_RANGE="bytes=0-7",
            )
            self.assertEqual(response.status_code, 206)
            self.assertEqual(len(b"".join(response.streaming_content)), 8)
            files_page = self.client.get(
                reverse("gui_v2:recording_files", args=[recording.pk])
            )
            self.assertContains(files_page, "Digitaliseringsopprinnelse")
            self.assertContains(files_page, raw[0].filename)
            job = create_rebuild_preview(user=self.user, relative_root=".")
            execute_rebuild(job=job, user=self.user)
            self.assertTrue(
                DigitizationDerivation.objects.filter(
                    source_asset=raw[0],
                    derived_asset=masters[0],
                    is_active=True,
                ).exists()
            )
            self.assertEqual(
                RecordingMediaSelection.objects.get(
                    recording=recording
                ).current_radio_id,
                generation.candidate_asset_id,
            )
        self.assertEqual(
            before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}
        )
