from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from parties.models import ArtistIdentity, Party

from catalogue.models import (
    DuplicateCandidate,
    ExternalIdentifier,
    Label,
    Recording,
    Release,
    ReleaseTrack,
)
from catalogue.services import create_release_track, find_recording_candidates


class ReleaseCatalogueTests(TestCase):
    def test_release_allows_unknown_optional_metadata(self):
        release = Release.objects.create(title="Ukjent utgivelse")
        self.assertIsNone(release.release_date)
        self.assertIsNone(release.label)
        self.assertEqual(release.catalogue_number, "")

    def test_tracks_reuse_recording_across_release_formats_and_positions(self):
        recording = Recording.objects.create(title="Samme master")
        lp = Release.objects.create(title="LP", release_type=Release.Type.LP)
        cd = Release.objects.create(title="CD", release_type=Release.Type.CD)
        a = ReleaseTrack.objects.create(
            release=lp, recording=recording, side="A", track_number=1, sequence_number=1
        )
        b = ReleaseTrack.objects.create(
            release=lp, recording=recording, side="B", track_number=2, sequence_number=2
        )
        disc = ReleaseTrack.objects.create(
            release=cd,
            recording=recording,
            disc_number=2,
            track_number=4,
            sequence_number=1,
        )
        self.assertEqual({a.side, b.side}, {"A", "B"})
        self.assertEqual(disc.disc_number, 2)
        self.assertEqual(recording.release_tracks.count(), 3)

    def test_new_recording_and_track_are_atomic(self):
        release = Release.objects.create(title="Testutgivelse")
        with patch.object(ReleaseTrack.objects, "create", side_effect=IntegrityError):
            with self.assertRaises(IntegrityError):
                create_release_track(
                    release=release,
                    sequence_number=1,
                    new_recording_title="Skal rulles tilbake",
                    new_isrc="NO-ABC-26-12345",
                )
        self.assertFalse(Recording.objects.filter(title="Skal rulles tilbake").exists())

    def test_existing_recording_is_reused(self):
        release = Release.objects.create(title="Testutgivelse")
        recording = Recording.objects.create(title="Eksisterende")
        track = create_release_track(
            release=release, sequence_number=1, recording=recording
        )
        self.assertEqual(track.recording_id, recording.pk)
        self.assertEqual(Recording.objects.count(), 1)

    def test_possible_duplicate_is_never_automatically_merged(self):
        existing = Recording.objects.create(title="Nordlys", duration_ms=180000)
        release = Release.objects.create(title="Testutgivelse")
        matches = find_recording_candidates(title="nordlys", duration_ms=181000)
        self.assertEqual(matches[0].recording, existing)
        with self.assertRaisesMessage(ValueError, "Mulig eksisterende"):
            create_release_track(
                release=release,
                sequence_number=1,
                new_recording_title="NORDLYS",
                duration_ms=181000,
            )
        self.assertEqual(Recording.objects.count(), 1)
        track = create_release_track(
            release=release,
            sequence_number=1,
            new_recording_title="NORDLYS",
            duration_ms=181000,
            force_create=True,
        )
        self.assertNotEqual(track.recording_id, existing.pk)
        self.assertEqual(DuplicateCandidate.objects.filter(status="open").count(), 1)

    def test_artist_strengthens_candidate_and_is_credited_on_new_recording(self):
        party = Party.objects.create(name="Kari Nordmann", kind=Party.Kind.PERSON)
        artist = ArtistIdentity.objects.create(party=party, display_name="KARI N")
        release = Release.objects.create(title="Single")
        track = create_release_track(
            release=release,
            sequence_number=1,
            new_recording_title="Nordlys",
            artist_identity=artist,
        )
        self.assertEqual(track.recording.contributions.get().artist_identity, artist)

    def test_isrc_and_release_barcodes_are_normalized_and_unique(self):
        recording = Recording.objects.create(title="Spor")
        release = Release.objects.create(title="Utgivelse")
        isrc = ExternalIdentifier.objects.create(
            recording=recording, scheme="ISRC", value="no-abc-26-00001"
        )
        upc = ExternalIdentifier.objects.create(
            release=release, scheme="UPC", value="0 36000-29145 2"
        )
        self.assertEqual(isrc.normalized_value, "NOABC2600001")
        self.assertEqual(upc.normalized_value, "036000291452")
        self.assertEqual(upc.value, "0 36000-29145 2")
        with self.assertRaises(ValidationError):
            ExternalIdentifier.objects.create(
                release=Release.objects.create(title="Annen"),
                scheme="UPC",
                value="036000291452",
            )

    def test_conflicting_isrc_cannot_be_assigned_to_forced_duplicate(self):
        original = Recording.objects.create(title="Original")
        ExternalIdentifier.objects.create(recording=original, value="NOABC2600001")
        release = Release.objects.create(title="Utgivelse")
        with self.assertRaisesMessage(ValueError, "ISRC finnes allerede"):
            create_release_track(
                release=release,
                sequence_number=1,
                new_recording_title="Annen påstått master",
                new_isrc="NO-ABC-26-00001",
                force_create=True,
            )
        self.assertEqual(Recording.objects.count(), 1)

    def test_identifier_scheme_requires_correct_target(self):
        recording = Recording.objects.create(title="Spor")
        release = Release.objects.create(title="Utgivelse")
        with self.assertRaises(ValidationError):
            ExternalIdentifier.objects.create(
                recording=recording, scheme="UPC", value="036000291452"
            )
        with self.assertRaises(ValidationError):
            ExternalIdentifier.objects.create(
                release=release, scheme="ISRC", value="NOABC2600001"
            )


class OperationalCatalogueAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "phase2-admin", password="test"
        )
        self.client.force_login(self.user)

    def test_release_track_workflow_and_search(self):
        label = Label.objects.create(name="Lynor")
        release = Release.objects.create(title="Nordlys-album", label=label)
        url = reverse("admin:catalogue_release_add_track", args=(release.pk,))
        response = self.client.post(
            url,
            {
                "new_recording_title": "Blåbær 東京",
                "new_isrc": "NO-ABC-26-00001",
                "sequence_number": 1,
                "disc_number": 1,
                "track_number": 1,
                "side": "",
                "title_override": "",
                "duration_ms": 180000,
            },
        )
        self.assertRedirects(
            response, reverse("admin:catalogue_release_change", args=(release.pk,))
        )
        recording = Recording.objects.get(title="Blåbær 東京")
        self.assertEqual(release.tracks.get().recording, recording)
        response = self.client.get(
            reverse("admin:catalogue_recording_changelist"), {"q": "NOABC2600001"}
        )
        self.assertContains(response, "Blåbær 東京")

    def test_candidate_requires_explicit_override(self):
        Recording.objects.create(title="Lik tittel")
        release = Release.objects.create(title="Test")
        url = reverse("admin:catalogue_release_add_track", args=(release.pk,))
        data = {"new_recording_title": "Lik tittel", "sequence_number": 1}
        response = self.client.post(url, data)
        self.assertContains(response, "Mulig dublett")
        self.assertEqual(Recording.objects.count(), 1)
        data["force_create"] = "on"
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Recording.objects.count(), 2)
        self.assertEqual(DuplicateCandidate.objects.count(), 1)
