import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import soundfile as sf
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from mutagen.flac import FLAC

from catalogue.models import Recording, RecordingContribution
from media_assets.mastering import _pcm_digest
from media_assets.models import FileAsset, FileLocation, RecordingMediaSelection

from .models import (
    Delivery,
    DeliveryArtifact,
    DeliveryItem,
    DeliveryProfile,
    DownloadEvent,
)
from .services import (
    DeliveryPreviewStale,
    artifact_path,
    build_preview,
    confirm_preview,
    resolve_delivery_source,
)


class DeliveryTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "music"
        self.artifacts = Path(self.temp.name) / "deliveries"
        self.root.mkdir()
        self.settings = override_settings(
            P7_STORAGE_ROOTS={
                "music_library": {"server_root": self.root, "read_only": True}
            },
            P7_DELIVERY_ARTIFACT_ROOT=self.artifacts,
            P7_DELIVERY_ARTIFACT_TTL_HOURS=24,
        )
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.user = get_user_model().objects.create_user(
            "delivery", password="secret", first_name="Håkon"
        )
        self.user.user_permissions.add(
            *Permission.objects.filter(
                codename__in={
                    "create_delivery",
                    "download_delivery",
                    "create_external_delivery",
                    "view_delivery",
                    "view_musiclibraryentry",
                    "view_recording",
                    "view_release",
                    "view_fileasset",
                    "view_filelocation",
                }
            )
        )
        self.client.force_login(self.user)

    def _recording(
        self,
        title="Sang",
        filename="radio.flac",
        *,
        current=True,
        present=True,
        tags=None,
    ):
        recording = Recording.objects.create(title=title)
        RecordingContribution.objects.create(
            recording=recording,
            role=RecordingContribution.Role.PRIMARY,
            credited_as="Artist",
        )
        asset = FileAsset.objects.create(
            recording=recording,
            filename=filename,
            mime_type="audio/flac",
            role=FileAsset.Role.RADIO_FLAC,
            lifecycle_status=(
                FileAsset.LifecycleStatus.CURRENT
                if current
                else FileAsset.LifecycleStatus.HISTORICAL
            ),
        )
        location = FileLocation.objects.create(
            asset=asset,
            storage_type=FileLocation.StorageType.NAS,
            storage_root_key="music_library",
            relative_path=filename,
            status=FileLocation.Status.ACTIVE,
            is_current=True,
        )
        if present:
            audio = np.column_stack(
                (np.linspace(-0.2, 0.2, 600), np.linspace(0.2, -0.2, 600))
            )
            sf.write(
                self.root / filename, audio, 48000, format="FLAC", subtype="PCM_24"
            )
            flac = FLAC(self.root / filename)
            for key, value in (
                tags
                or {
                    "TITLE": title,
                    "ARTIST": "Artist",
                    "P7UUID": str(recording.pk),
                    "RATING": "4",
                    "X-UNKNOWN": "secret",
                }
            ).items():
                flac[key] = value
            flac.save()
            asset.sha256 = hashlib.sha256(
                (self.root / filename).read_bytes()
            ).hexdigest()
            asset.size_bytes = (self.root / filename).stat().st_size
            asset.save()
        selection = RecordingMediaSelection.objects.create(
            recording=recording, current_radio=asset if current else None
        )
        return recording, asset, location, selection

    def _data(self, profile=DeliveryProfile.INTERNAL_COMPLETE):
        return {
            "profile": profile,
            "purpose": Delivery.Purpose.INTERNAL,
            "purpose_description": "",
            "recipient_name": "Håkon",
            "recipient_organization": "P7",
            "retain_for_future_use": False,
            "other_use": False,
            "other_use_description": "",
        }

    def _preview(self, recordings, profile=DeliveryProfile.INTERNAL_COMPLETE):
        return build_preview(
            [item.pk for item in recordings],
            user=self.user,
            cleaned_data=self._data(profile),
        )

    def test_explicit_current_is_required_and_master_never_falls_back(self):
        recording, asset, _, selection = self._recording(current=False)
        master = FileAsset.objects.create(
            recording=recording,
            filename="master.wav",
            role=FileAsset.Role.EDITED_WAV_MASTER,
        )
        selection.selected_master = master
        selection.save()
        result = resolve_delivery_source(recording)
        self.assertEqual(result.status, "missing_current")
        self.assertIsNone(result.asset)

    def test_single_delivery_freezes_source_and_old_history_survives_current_change(
        self,
    ):
        recording, source, _, selection = self._recording()
        delivery = confirm_preview(self._preview([recording])["token"], user=self.user)
        item = delivery.items.get()
        self.assertEqual(item.source_file_asset, source)
        replacement = FileAsset.objects.create(
            recording=recording,
            filename="new.flac",
            role=FileAsset.Role.RADIO_FLAC,
            lifecycle_status=FileAsset.LifecycleStatus.CANDIDATE,
        )
        source.lifecycle_status = FileAsset.LifecycleStatus.HISTORICAL
        source.save()
        replacement.lifecycle_status = FileAsset.LifecycleStatus.CURRENT
        replacement.save()
        selection.current_radio = replacement
        selection.save()
        item.refresh_from_db()
        self.assertEqual(item.source_file_asset_id, source.pk)

    def test_stale_preview_rejects_changed_current(self):
        recording, source, _, selection = self._recording()
        preview = self._preview([recording])
        source.lifecycle_status = FileAsset.LifecycleStatus.HISTORICAL
        source.save()
        replacement = FileAsset.objects.create(
            recording=recording,
            filename="new.flac",
            role=FileAsset.Role.RADIO_FLAC,
            lifecycle_status=FileAsset.LifecycleStatus.CURRENT,
        )
        selection.current_radio = replacement
        selection.save()
        with self.assertRaises(DeliveryPreviewStale):
            confirm_preview(preview["token"], user=self.user)

    def test_missing_file_and_partial_bulk_are_documented(self):
        ready, *_ = self._recording("Klar", "ready.flac")
        missing, *_ = self._recording("Mangler", "missing.flac", present=False)
        preview = self._preview([ready, missing])
        self.assertEqual(
            {row["status"] for row in preview["items"]}, {"ready", "unavailable"}
        )
        with self.assertRaises(ValidationError):
            confirm_preview(preview["token"], user=self.user)
        delivery = confirm_preview(preview["token"], user=self.user, allow_partial=True)
        self.assertEqual(delivery.status, Delivery.Status.PARTIAL)
        self.assertEqual(
            delivery.items.filter(status=DeliveryItem.Status.SKIPPED).count(), 1
        )

    def test_internal_single_is_exact_source_and_read_only(self):
        recording, asset, location, _ = self._recording()
        revisions = (recording.revision, asset.revision, location.revision)
        before = (self.root / "radio.flac").read_bytes()
        delivery = confirm_preview(self._preview([recording])["token"], user=self.user)
        response = self.client.get(reverse("delivery:download", args=[delivery.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), before)
        self.assertEqual((self.root / "radio.flac").read_bytes(), before)
        self.assertEqual(delivery.download_events.count(), 2)
        recording.refresh_from_db()
        asset.refresh_from_db()
        location.refresh_from_db()
        self.assertEqual(
            (recording.revision, asset.revision, location.revision), revisions
        )

    def test_bulk_zip_has_files_and_internal_manifest(self):
        first, *_ = self._recording("Samme", "one.flac")
        second, *_ = self._recording("Samme", "two.flac")
        delivery = confirm_preview(
            self._preview([first, second])["token"], user=self.user
        )
        artifact = delivery.artifacts.get()
        self.assertEqual(artifact.kind, DeliveryArtifact.Kind.ZIP)
        with zipfile.ZipFile(artifact_path(artifact.relative_path)) as archive:
            names = archive.namelist()
            self.assertIn("manifest.json", names)
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(len(manifest), 2)
            self.assertIn("file_asset_uuid", manifest[0])
            self.assertIn("Artist - Samme (2).flac", names)

    def test_external_profile_allowlists_tags_and_preserves_pcm(self):
        recording, _, _, _ = self._recording(
            tags={
                "TITLE": "Sang",
                "ARTIST": "Artist",
                "ISRC": "NOP7T2600001",
                "P7UUID": "secret",
                "RATING": "5",
                "X-CUSTOM": "secret",
                "GENRE": "Pop",
            }
        )
        source_bytes = (self.root / "radio.flac").read_bytes()
        source_pcm = _pcm_digest(self.root / "radio.flac")
        delivery = confirm_preview(
            self._preview([recording], DeliveryProfile.EXTERNAL_RADIO)["token"],
            user=self.user,
        )
        artifact = delivery.artifacts.get()
        output = artifact_path(artifact.relative_path)
        tags = {key.upper() for key in FLAC(output).keys()}
        self.assertTrue({"TITLE", "ARTIST", "ISRC", "GENRE"}.issubset(tags))
        self.assertTrue({"P7UUID", "RATING", "X-CUSTOM"}.isdisjoint(tags))
        self.assertEqual(_pcm_digest(output), source_pcm)
        self.assertEqual((self.root / "radio.flac").read_bytes(), source_bytes)

    def test_external_manifest_omits_internal_identifiers(self):
        first, *_ = self._recording("Første", "first.flac")
        second, *_ = self._recording("Andre", "second.flac")
        delivery = confirm_preview(
            self._preview([first, second], DeliveryProfile.EXTERNAL_RADIO)["token"],
            user=self.user,
        )
        artifact = delivery.artifacts.get()
        with zipfile.ZipFile(artifact_path(artifact.relative_path)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        self.assertNotIn("recording_uuid", manifest[0])
        self.assertNotIn("file_asset_uuid", manifest[0])
        self.assertEqual(
            set(manifest[0]), {"title", "artist", "isrc", "output_filename"}
        )

    def test_artifact_path_cannot_escape_delivery_root(self):
        with self.assertRaises(ValidationError):
            artifact_path("../outside.zip")

    def test_external_permission_is_enforced_in_service(self):
        recording, *_ = self._recording()
        preview = self._preview([recording], DeliveryProfile.EXTERNAL_RADIO)
        self.user.user_permissions.remove(
            Permission.objects.get(codename="create_external_delivery")
        )
        self.user = get_user_model().objects.get(pk=self.user.pk)
        with self.assertRaises(PermissionDenied):
            confirm_preview(preview["token"], user=self.user)

    def test_filename_is_windows_safe(self):
        recording, *_ = self._recording("Sang: <test>?", "safe.flac")
        preview = self._preview([recording])
        self.assertNotRegex(preview["items"][0]["output_filename"], r'[<>:"/\\|?*]')

    def test_unauthenticated_and_unpermitted_download_are_denied(self):
        recording, *_ = self._recording()
        delivery = confirm_preview(self._preview([recording])["token"], user=self.user)
        self.client.logout()
        response = self.client.get(reverse("delivery:download", args=[delivery.pk]))
        self.assertEqual(response.status_code, 302)
        other = get_user_model().objects.create_user("other", password="secret")
        self.client.force_login(other)
        self.assertEqual(
            self.client.get(
                reverse("delivery:download", args=[delivery.pk])
            ).status_code,
            403,
        )

    def test_cleanup_removes_artifact_but_keeps_delivery_audit(self):
        recording, *_ = self._recording()
        delivery = confirm_preview(
            self._preview([recording], DeliveryProfile.EXTERNAL_RADIO)["token"],
            user=self.user,
        )
        artifact = delivery.artifacts.get()
        artifact.expires_at = timezone.now() - timezone.timedelta(seconds=1)
        artifact.save()
        call_command("cleanup_delivery_artifacts")
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, Delivery.Status.EXPIRED)
        self.assertIsNotNone(delivery.artifacts.get().removed_at)
        self.assertTrue(delivery.manifest_snapshot)
        self.assertTrue(Delivery.objects.filter(pk=delivery.pk).exists())

    def test_music_library_and_recording_expose_same_delivery_entry(self):
        recording, *_ = self._recording()
        from music_library.models import MusicLibraryEntry

        MusicLibraryEntry.objects.create(recording=recording)
        library = self.client.get(reverse("gui_v2:music_library"))
        detail = self.client.get(
            reverse("gui_v2:recording_detail", args=[recording.pk])
        )
        self.assertContains(library, reverse("delivery:create"))
        self.assertContains(
            detail, f"{reverse('delivery:create')}?recording={recording.pk}"
        )

    def test_gui_preview_and_confirm_use_the_shared_workflow(self):
        recording, *_ = self._recording()
        preview_response = self.client.post(
            reverse("delivery:create"),
            {
                "recording": str(recording.pk),
                **self._data(),
                "preview": "1",
            },
        )
        self.assertEqual(preview_response.status_code, 200)
        self.assertContains(preview_response, "Forhåndsvisning")
        token = preview_response.context["preview"]["token"]
        response = self.client.post(
            reverse("delivery:create"),
            {
                "recording": str(recording.pk),
                "preview_token": token,
                "confirm": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        delivery = Delivery.objects.get()
        self.assertEqual(response.url, reverse("delivery:detail", args=[delivery.pk]))
