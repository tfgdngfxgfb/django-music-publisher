from django.test import TestCase

from catalogue.models import RecordingContribution, Release
from gui_v2.forms import TrackRowForm
from gui_v2.services import save_release_track_rows
from parties.models import ArtistIdentity, Party


class PhysicalCreditTests(TestCase):
    def test_printed_credit_can_link_distinct_canonical_artist(self):
        release = Release.objects.create(title="Kassett")
        party = Party.objects.create(
            name="Kari Hansen", kind=Party.Kind.PERSON
        )
        identity = ArtistIdentity.objects.create(
            party=party, display_name="Kari Hansen"
        )
        form = TrackRowForm(
            {
                "sequence_number": "1",
                "recording_title": "Sangen",
                "artists": "K. Hansen",
                "primary_artist_identity_name": "Kari Hansen",
            },
            release=release,
        )
        self.assertTrue(form.is_valid(), form.errors)
        save_release_track_rows(release=release, rows=[form.cleaned_data])
        credit = RecordingContribution.objects.get(
            recording=release.tracks.get().recording,
            role=RecordingContribution.Role.PRIMARY,
        )
        self.assertEqual(credit.credited_as, "K. Hansen")
        self.assertEqual(credit.artist_identity, identity)

    def test_unknown_identity_is_rejected_without_invention(self):
        form = TrackRowForm(
            {
                "sequence_number": "1",
                "recording_title": "Sangen",
                "artists": "K. Hansen",
                "primary_artist_identity_name": "Ukjent identitet",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("primary_artist_identity_name", form.errors)
