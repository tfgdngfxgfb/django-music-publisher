from django.contrib.auth import get_user_model
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse

from catalogue.models import Recording, Release
from music_library.models import MusicLibraryEntry
from parties.models import Party
from rights.models import RightsClaim, RightsConfiguration
from rights.services import decide_rights_claim
from rights_core.models import VerificationStatus

from managed_music.models import ManagedRecording, ManagedRelease
from managed_music.services import (
    create_managed_recording,
    save_managed_release,
)


class ManagedMusicTests(TestCase):
    def setUp(self):
        self.local_organization = Party.objects.create(
            name="Lokal testorganisasjon", kind=Party.Kind.ORGANIZATION
        )
        RightsConfiguration.objects.create(
            local_organization=self.local_organization
        )

    def test_managed_recording_structurally_requires_library_entry(self):
        recording = Recording.objects.create(title="Forvaltet")
        managed = create_managed_recording(
            recording=recording,
            relationship_type=RightsClaim.RightType.ADMINISTRATION,
        )
        self.assertEqual(managed.library_entry.recording, recording)
        with self.assertRaises(ProtectedError):
            managed.library_entry.delete()

    def test_adding_existing_recording_creates_library_membership(self):
        recording = Recording.objects.create(title="Eksisterende")
        self.assertFalse(
            MusicLibraryEntry.objects.filter(recording=recording).exists()
        )
        create_managed_recording(
            recording=recording,
            relationship_type=RightsClaim.RightType.DISTRIBUTION,
        )
        self.assertTrue(
            MusicLibraryEntry.objects.filter(recording=recording).exists()
        )

    def test_music_library_never_automatically_becomes_managed(self):
        first = Recording.objects.create(title="Første")
        second = Recording.objects.create(title="Andre")
        MusicLibraryEntry.objects.create(recording=second)
        create_managed_recording(
            recording=first,
            relationship_type=RightsClaim.RightType.ADMINISTRATION,
        )
        self.assertEqual(ManagedRecording.objects.count(), 1)
        self.assertFalse(
            hasattr(second.music_library_entry, "managed_recording")
        )

    def test_new_recording_created_through_managed_is_in_both_bases(self):
        managed = create_managed_recording(
            new_recording_title="Ny forvaltet",
            relationship_type=RightsClaim.RightType.OWNERSHIP,
            ownership_share="100",
        )
        self.assertEqual(managed.recording.title, "Ny forvaltet")
        self.assertEqual(MusicLibraryEntry.objects.count(), 1)
        self.assertTrue(
            RightsClaim.objects.filter(
                recording=managed.recording,
                right_type=RightsClaim.RightType.OWNERSHIP,
                rights_holder=self.local_organization,
            ).exists()
        )

    def test_managed_status_becomes_active_when_local_basis_is_confirmed(self):
        recording = Recording.objects.create(title="Kontrollert forvaltning")
        managed = create_managed_recording(
            recording=recording,
            relationship_type=RightsClaim.RightType.ADMINISTRATION,
        )
        self.assertEqual(managed.status, ManagedRecording.Status.PENDING)
        claim = RightsClaim.objects.get(recording=recording)
        reviewer = get_user_model().objects.create_user(username="beslutter")
        decide_rights_claim(
            claim,
            VerificationStatus.CONFIRMED,
            user=reviewer,
        )
        managed.refresh_from_db()
        self.assertEqual(managed.status, ManagedRecording.Status.ACTIVE)

    def test_managed_release_is_independent_of_recording_management_and_rights(
        self,
    ):
        release = Release.objects.create(title="Forvaltet samleplate")
        recording = Recording.objects.create(title="Tredjepartsspor")
        managed = save_managed_release(
            release=release,
            status=ManagedRelease.Status.ACTIVE,
            relationship=ManagedRelease.Relationship.MANAGED_CATALOGUE,
            notes="Forvaltes som katalogprodukt.",
        )

        self.assertEqual(managed.release, release)
        self.assertFalse(ManagedRecording.objects.exists())
        self.assertFalse(
            RightsClaim.objects.filter(recording=recording).exists()
        )

    def test_managed_release_is_unique_and_protects_release(self):
        release = Release.objects.create(title="Beskyttet utgivelse")
        managed = save_managed_release(
            release=release,
            status=ManagedRelease.Status.PENDING,
            relationship=ManagedRelease.Relationship.OWNED_CATALOGUE,
        )
        updated = save_managed_release(
            release=release,
            status=ManagedRelease.Status.ACTIVE,
            relationship=ManagedRelease.Relationship.OWNED_CATALOGUE,
        )

        self.assertEqual(updated.pk, managed.pk)
        self.assertEqual(ManagedRelease.objects.count(), 1)
        with self.assertRaises(ProtectedError):
            release.delete()


class ManagedMusicAdminTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            "admin", password="test"
        )
        local_organization = Party.objects.create(
            name="Lokal testorganisasjon", kind=Party.Kind.ORGANIZATION
        )
        RightsConfiguration.objects.create(
            local_organization=local_organization
        )
        self.client.force_login(self.admin)

    def test_explicit_admin_workflow(self):
        response = self.client.post(
            reverse("admin:managed_music_managedrecording_add"),
            {
                "new_recording_title": "Administrert master",
                "relationship_type": RightsClaim.RightType.ADMINISTRATION,
                "ownership_share": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        managed = ManagedRecording.objects.get()
        self.assertEqual(managed.recording.title, "Administrert master")
        self.assertTrue(
            MusicLibraryEntry.objects.filter(
                recording=managed.recording
            ).exists()
        )
        self.assertTrue(
            RightsClaim.objects.filter(
                recording=managed.recording,
                right_type=RightsClaim.RightType.ADMINISTRATION,
            ).exists()
        )
