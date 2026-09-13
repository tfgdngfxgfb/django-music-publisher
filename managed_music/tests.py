from django.contrib.auth import get_user_model
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse

from catalogue.models import Recording
from music_library.models import MusicLibraryEntry

from managed_music.models import ManagedRecording
from managed_music.services import create_managed_recording


class ManagedMusicTests(TestCase):
    def test_managed_recording_structurally_requires_library_entry(self):
        recording = Recording.objects.create(title="Forvaltet")
        managed = create_managed_recording(recording=recording)
        self.assertEqual(managed.library_entry.recording, recording)
        with self.assertRaises(ProtectedError):
            managed.library_entry.delete()

    def test_adding_existing_recording_creates_library_membership(self):
        recording = Recording.objects.create(title="Eksisterende")
        self.assertFalse(MusicLibraryEntry.objects.filter(recording=recording).exists())
        create_managed_recording(recording=recording)
        self.assertTrue(MusicLibraryEntry.objects.filter(recording=recording).exists())

    def test_music_library_never_automatically_becomes_managed(self):
        first = Recording.objects.create(title="Første")
        second = Recording.objects.create(title="Andre")
        MusicLibraryEntry.objects.create(recording=second)
        create_managed_recording(recording=first)
        self.assertEqual(ManagedRecording.objects.count(), 1)
        self.assertFalse(hasattr(second.music_library_entry, "managed_recording"))

    def test_new_recording_created_through_managed_is_in_both_bases(self):
        managed = create_managed_recording(new_recording_title="Ny forvaltet")
        self.assertEqual(managed.recording.title, "Ny forvaltet")
        self.assertEqual(MusicLibraryEntry.objects.count(), 1)


class ManagedMusicAdminTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("admin", password="test")
        self.client.force_login(self.admin)

    def test_explicit_admin_workflow(self):
        response = self.client.post(
            reverse("admin:managed_music_managedrecording_add"),
            {
                "new_recording_title": "Administrert master",
                "status": ManagedRecording.Status.ACTIVE,
            },
        )
        self.assertEqual(response.status_code, 302)
        managed = ManagedRecording.objects.get()
        self.assertEqual(managed.recording.title, "Administrert master")
        self.assertTrue(
            MusicLibraryEntry.objects.filter(recording=managed.recording).exists()
        )
