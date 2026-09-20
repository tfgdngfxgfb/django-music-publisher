"""Regression cases from the September 2026 workflow/security review."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from catalogue.models import (
    Recording,
    RecordingContribution,
    Release,
    ReleaseTrack,
)
from gui_v2.forms import TrackRowForm
from gui_v2.services import save_release_track_rows
from managed_music.models import ManagedRelease
from media_assets.models import FileAsset
from parties.models import ArtistIdentity, Party
from provenance.models import SourceRecord, SourceSystem


@override_settings(GUI_V2_WRITES_ENABLED=True)
class TrackReviewTests(TestCase):
    def setUp(self):
        self.release = Release.objects.create(title="Utgivelse")
        self.recording = Recording.objects.create(title="Felles innspilling")
        self.track = ReleaseTrack.objects.create(
            release=self.release, recording=self.recording, sequence_number=1
        )
        self.user = get_user_model().objects.create_user("review-operator")
        self.grant(
            "view_release",
            "change_release",
            "add_releasetrack",
            "change_releasetrack",
        )
        self.url = reverse("gui_v2:release_detail", args=[self.release.pk])

    def grant(self, *codes):
        self.user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="catalogue", codename__in=codes
            )
        )
        self.client.force_login(self.user)

    def row(self, **changes):
        data = {
            "track_id": str(self.track.pk),
            "recording_id": str(self.recording.pk),
            "sequence_number": "1",
            "recording_title": self.recording.title,
        }
        data.update(changes)
        return data

    def post_rows(self, rows):
        payload = {
            "action": "tracks",
            "tracks-TOTAL_FORMS": str(len(rows)),
            "tracks-INITIAL_FORMS": "0",
            "tracks-MAX_NUM_FORMS": "1000",
            "tracks-MIN_NUM_FORMS": "0",
        }
        for index, row in enumerate(rows):
            payload.update(
                {f"tracks-{index}-{key}": value for key, value in row.items()}
            )
        return self.client.post(self.url, payload)

    def save_rows(self, rows):
        cleaned = []
        for row in rows:
            form = TrackRowForm(row, release=self.release)
            self.assertTrue(form.is_valid(), form.errors)
            cleaned.append(form.cleaned_data)
        return save_release_track_rows(release=self.release, rows=cleaned)

    def test_direct_post_cannot_change_shared_recording_without_permission(
        self,
    ):
        self.grant(
            "add_recordingcontribution",
            "delete_recordingcontribution",
            "add_externalidentifier",
            "change_externalidentifier",
            "delete_externalidentifier",
        )
        response = self.post_rows(
            [self.row(update_shared_recording="on", recording_title="Endret")]
        )
        self.assertEqual(response.status_code, 403)
        self.recording.refresh_from_db()
        self.assertEqual(self.recording.title, "Felles innspilling")
        self.grant("change_recording")
        self.assertEqual(
            self.post_rows(
                [
                    self.row(
                        update_shared_recording="on", recording_title="Endret"
                    )
                ]
            ).status_code,
            302,
        )
        self.recording.refresh_from_db()
        self.assertEqual(self.recording.title, "Endret")

    def test_existing_track_cannot_create_recording_without_add_permission(
        self,
    ):
        self.grant("add_recordingcontribution", "add_externalidentifier")
        response = self.post_rows(
            [self.row(recording_id="", recording_title="En helt ny sang")]
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Recording.objects.count(), 1)

    def test_new_track_can_link_existing_recording_without_add_recording(self):
        response = self.post_rows(
            [self.row(), self.row(track_id="", sequence_number="2")]
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.release.tracks.count(), 2)
        self.assertEqual(Recording.objects.count(), 1)

    def test_omitted_duplicate_and_foreign_track_ids_leave_grid_unchanged(
        self,
    ):
        foreign_release = Release.objects.create(title="Annen utgivelse")
        foreign = ReleaseTrack.objects.create(
            release=foreign_release,
            recording=self.recording,
            sequence_number=1,
        )
        cases = [
            [],
            [self.row(), self.row(sequence_number="2")],
            [self.row(track_id=str(foreign.pk))],
        ]
        before = list(ReleaseTrack.objects.order_by("pk").values())
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaisesMessage(
                ValidationError, "ufullstendig"
            ):
                self.save_rows(rows)
            self.assertEqual(
                list(ReleaseTrack.objects.order_by("pk").values()), before
            )

    def test_attached_file_prevents_reassignment_but_unlinked_track_can_move(
        self,
    ):
        other = Recording.objects.create(title="Annen innspilling")
        asset = FileAsset.objects.create(
            recording=self.recording,
            release_track=self.track,
            filename="master.wav",
            role=FileAsset.Role.EDITED_WAV_MASTER,
        )
        with self.assertRaisesMessage(ValidationError, "tilknyttede filer"):
            self.save_rows([self.row(recording_id=str(other.pk))])
        self.track.refresh_from_db()
        self.assertEqual(self.track.recording_id, self.recording.pk)
        asset.release_track = None
        asset.save()
        self.save_rows([self.row(recording_id=str(other.pk))])
        self.track.refresh_from_db()
        self.assertEqual(self.track.recording_id, other.pk)

    def test_unchanged_credit_preserves_identity_source_and_revision(self):
        system = SourceSystem.objects.create(
            name="Cover", kind=SourceSystem.Kind.PHYSICAL
        )
        source = SourceRecord.objects.create(
            source_system=system, raw_payload={"artist": "K. Hansen"}
        )
        party = Party.objects.create(
            name="Kari Hansen", kind=Party.Kind.PERSON
        )
        identity = ArtistIdentity.objects.create(
            party=party, display_name="Kari Hansen"
        )
        credit = RecordingContribution.objects.create(
            recording=self.recording,
            party=party,
            artist_identity=identity,
            credited_as="K. Hansen",
            source_record=source,
            role=RecordingContribution.Role.PRIMARY,
        )
        before = dict(RecordingContribution.objects.values().get(pk=credit.pk))
        self.save_rows(
            [self.row(update_shared_recording="on", artists="K. Hansen")]
        )
        self.assertEqual(
            RecordingContribution.objects.values().get(pk=credit.pk), before
        )

    def test_ambiguous_artist_name_remains_an_unresolved_physical_credit(self):
        for number in (1, 2):
            party = Party.objects.create(
                name=f"Navnelik person {number}", kind=Party.Kind.PERSON
            )
            ArtistIdentity.objects.create(
                party=party, display_name="Kari Hansen"
            )
        self.save_rows(
            [self.row(update_shared_recording="on", artists="Kari Hansen")]
        )
        credit = self.recording.contributions.get()
        self.assertEqual(credit.credited_as, "Kari Hansen")
        self.assertIsNone(credit.artist_identity_id)
        self.assertIsNone(credit.party_id)

    def test_release_view_does_not_grant_access_to_management_details(self):
        ManagedRelease.objects.create(
            release=self.release,
            relationship=ManagedRelease.Relationship.OWNED_CATALOGUE,
        )
        response = self.client.get(self.url, {"tab": "details"})
        self.assertNotContains(response, 'class="release-management-card"')
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="managed_music",
                codename="view_managedrelease",
            )
        )
        self.client.force_login(self.user)
        self.assertContains(
            self.client.get(self.url, {"tab": "details"}),
            'class="release-management-card"',
        )
