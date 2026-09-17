"""Focused coverage for the three Recording GUI v2 workspaces."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalogue.models import Label, Recording, Release, ReleaseTrack
from delivery.models import (
    Delivery,
    DeliveryArtifact,
    DeliveryItem,
    DeliveryProfile,
)
from media_assets.models import FileAsset, FileLocation
from music_library.models import (
    Channel,
    MusicLibraryChannel,
    MusicLibraryEntry,
    MusicLibraryTargetAudience,
    TargetAudience,
)


class RecordingWorkspaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "workspace", password="test"
        )
        self.user.is_staff = True
        self.user.save()
        for app_label, codename in (
            ("catalogue", "view_recording"),
            ("catalogue", "view_release"),
            ("music_library", "view_musiclibraryentry"),
            ("music_library", "change_musiclibraryentry"),
            ("media_assets", "view_fileasset"),
            ("media_assets", "view_filelocation"),
            ("delivery", "view_delivery"),
            ("delivery", "create_delivery"),
            ("delivery", "download_delivery"),
        ):
            self.user.user_permissions.add(
                Permission.objects.get(
                    content_type__app_label=app_label, codename=codename
                )
            )
        self.client.force_login(self.user)
        self.recording = Recording.objects.create(title="En innspilling")

    def url(self, tab):
        return reverse(f"gui_v2:recording_{tab}", args=[self.recording.pk])

    def test_shared_header_tabs_player_and_return_context(self):
        return_url = reverse("gui_v2:music_library") + "?genre=Pop"
        for tab in ("releases", "radio", "deliveries"):
            response = self.client.get(self.url(tab), {"return": return_url})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'class="player global-player"')
            self.assertContains(response, 'class="recording-object-header"')
            self.assertContains(response, "genre=Pop")
            for destination in ("releases", "radio", "deliveries"):
                self.assertContains(response, self.url(destination))
            self.assertContains(response, f"UUID {self.recording.pk}")

    def test_releases_show_empty_multiple_positions_and_override(self):
        self.assertContains(
            self.client.get(self.url("releases")),
            "Ingen utgivelser er koblet til denne innspillingen",
        )
        label = Label.objects.create(name="Testlabel")
        first = Release.objects.create(
            title="Original",
            label=label,
            release_year=2026,
            release_type=Release.Type.CD,
        )
        second = Release.objects.create(
            title="Samling", release_type=Release.Type.LP
        )
        track = ReleaseTrack.objects.create(
            release=first,
            recording=self.recording,
            sequence_number=4,
            disc_number=1,
            track_number=4,
            title_override="Alternativ tittel",
            duration_ms=183000,
        )
        ReleaseTrack.objects.create(
            release=second,
            recording=self.recording,
            sequence_number=2,
            side="B",
            track_number=2,
        )
        response = self.client.get(
            self.url("releases"), {"track": str(track.pk)}
        )
        for value in (
            "Original",
            "Samling",
            "Testlabel",
            "Disc 1 · Spor 4",
            "Side B · Spor 2",
            "Alternativ tittel",
            "03:03",
        ):
            self.assertContains(response, value)
        self.assertContains(
            response, reverse("gui_v2:release_detail", args=[first.pk])
        )

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_radio_read_edit_permissions_and_shared_form(self):
        self.assertContains(
            self.client.get(self.url("radio")),
            "ingen post i Musikkarkivet",
        )
        entry = MusicLibraryEntry.objects.create(recording=self.recording)
        channel = Channel.objects.create(code="radio-test", name="Radiokanal")
        target = TargetAudience.objects.create(
            code="adult-test", name="Voksne"
        )
        response = self.client.post(
            self.url("radio"),
            {
                "genre": "Pop",
                "language": "nb",
                "gender": "female",
                "energy": "4",
                "rotation_suitability": "not_suitable",
                "verification_status": entry.verification_status,
                "channels": [str(channel.pk)],
                "target_audiences": [str(target.pk)],
                "notes": "Kontrollert i radio",
            },
        )
        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.energy, 4)
        self.assertEqual(list(entry.channels.all()), [channel])
        self.assertEqual(list(entry.target_audiences.all()), [target])
        page = self.client.get(self.url("radio"))
        for value in (
            "4 av 5",
            "Ikke rotasjonsverdig",
            "Radiokanal",
            "Voksne",
        ):
            self.assertContains(page, value)
        self.assertContains(page, "Rediger radiodata")

        with override_settings(GUI_V2_WRITES_ENABLED=False):
            self.assertNotContains(
                self.client.get(self.url("radio")), "Lagre radiodata"
            )
            self.assertEqual(
                self.client.post(
                    self.url("radio"), {"genre": "Rock"}
                ).status_code,
                403,
            )
        entry.refresh_from_db()
        self.assertEqual(entry.genre, "Pop")

    def test_radio_current_and_ambiguous_playback_are_conservative(self):
        MusicLibraryEntry.objects.create(recording=self.recording)
        first = FileAsset.objects.create(
            recording=self.recording,
            role=FileAsset.Role.RADIO_FLAC,
            filename="first.flac",
            mime_type="audio/flac",
        )
        FileLocation.objects.create(
            asset=first,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="first.flac",
            status=FileLocation.Status.ACTIVE,
            is_current=True,
        )
        available = self.client.get(self.url("radio"))
        self.assertContains(available, "first.flac")
        self.assertContains(available, "data-play-recording")
        self.assertContains(
            available,
            reverse("gui_v2:recording_audio", args=[self.recording.pk]),
        )
        second = FileAsset.objects.create(
            recording=self.recording,
            role=FileAsset.Role.RADIO_FLAC,
            filename="second.flac",
        )
        FileLocation.objects.create(
            asset=second,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="second.flac",
            status=FileLocation.Status.ACTIVE,
            is_current=True,
        )
        ambiguous = self.client.get(self.url("radio"))
        self.assertContains(ambiguous, "Flere mulige radiofiler")
        self.assertNotContains(ambiguous, "data-play-recording")

    def _delivery(
        self, recording, *, recipient="Mottaker", source=None, location=None
    ):
        delivery = Delivery.objects.create(
            created_by=self.user,
            status=Delivery.Status.READY,
            profile=DeliveryProfile.INTERNAL_COMPLETE,
            purpose=Delivery.Purpose.INTERNAL,
            recipient_name=recipient,
        )
        item = DeliveryItem.objects.create(
            delivery=delivery,
            recording=recording,
            status=(
                DeliveryItem.Status.READY
                if location
                else DeliveryItem.Status.SKIPPED
            ),
            recording_uuid_snapshot=recording.pk,
            title_snapshot=recording.title,
            source_asset_uuid_snapshot=source.pk if source else None,
            source_sha256_snapshot="a" * 64 if source else "",
            source_file_asset=source,
            source_file_location=location,
            output_filename="delivered.flac" if source else "",
        )
        return delivery, item

    def test_delivery_history_uses_snapshots_and_filters_recording(self):
        self.assertContains(
            self.client.get(self.url("deliveries")),
            "ingen registrerte leveranser",
        )
        historical = FileAsset.objects.create(
            recording=self.recording,
            role=FileAsset.Role.RADIO_FLAC,
            filename="historical.flac",
        )
        delivery, _ = self._delivery(self.recording, source=historical)
        other = Recording.objects.create(title="Annen innspilling")
        self._delivery(other, recipient="Annen mottaker")
        response = self.client.get(self.url("deliveries"))
        self.assertContains(response, "Mottaker")
        self.assertNotContains(response, "Annen mottaker")
        self.assertContains(response, str(historical.pk))
        self.assertContains(response, "a" * 64)
        self.assertContains(response, "historical.flac")
        self.assertContains(response, f"?recording={self.recording.pk}")
        self.assertContains(
            response, reverse("delivery:detail", args=[delivery.pk])
        )
        current = FileAsset.objects.create(
            recording=self.recording,
            role=FileAsset.Role.RADIO_FLAC,
            filename="current.flac",
        )
        response = self.client.get(self.url("deliveries"))
        self.assertContains(response, str(historical.pk))
        self.assertNotContains(response, current.filename)

    def test_delivery_expired_artifact_and_permissions(self):
        source = FileAsset.objects.create(
            recording=self.recording,
            role=FileAsset.Role.RADIO_FLAC,
            filename="delivery-source.flac",
        )
        location = FileLocation.objects.create(
            asset=source,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="delivery-source.flac",
            status=FileLocation.Status.ACTIVE,
        )
        delivery, _ = self._delivery(
            self.recording, source=source, location=location
        )
        self.assertContains(
            self.client.get(self.url("deliveries")), ">Last ned</a>"
        )
        DeliveryArtifact.objects.create(
            delivery=delivery,
            kind=DeliveryArtifact.Kind.ZIP,
            relative_path="expired.zip",
            filename="expired.zip",
            size_bytes=10,
            sha256="b" * 64,
            expires_at=timezone.now() - timedelta(days=1),
        )
        response = self.client.get(self.url("deliveries"))
        self.assertContains(response, "utløpt")
        self.assertNotContains(response, ">Last ned</a>")
        restricted = get_user_model().objects.create_user(
            "restricted", password="test"
        )
        self.client.force_login(restricted)
        for tab in ("releases", "radio", "deliveries"):
            self.assertEqual(self.client.get(self.url(tab)).status_code, 403)
