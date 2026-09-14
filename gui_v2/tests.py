import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from catalogue.models import Recording, Release


class GuiV2WorkspaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="prototype-user",
            password="test-password",
        )

    def test_login_redirect_keeps_requested_v2_url(self):
        requested = reverse("gui_v2:music_library")

        response = self.client.get(requested)

        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response.url).query)
        self.assertEqual(query["next"], [requested])

    def test_prototype_start_is_authenticated_and_sections_require_view_permission(self):
        self.client.force_login(self.user)

        self.assertEqual(self.client.get(reverse("gui_v2:home")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("gui_v2:music_library")).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse("gui_v2:release_tracks")).status_code,
            403,
        )

        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="music_library",
                codename="view_musiclibraryentry",
            ),
            Permission.objects.get(
                content_type__app_label="catalogue",
                codename="view_release",
            ),
        )
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.client.force_login(self.user)

        self.assertContains(
            self.client.get(reverse("gui_v2:music_library")),
            "Fiktive visningsdata",
        )
        self.assertContains(
            self.client.get(reverse("gui_v2:release_tracks")),
            "Ingen endringer lagres",
        )

    def test_prototype_requests_do_not_change_database_or_files(self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)
        Recording.objects.create(title="Kontrollpost")
        database_before = (Recording.objects.count(), Release.objects.count())

        with tempfile.TemporaryDirectory() as folder:
            sentinel = Path(folder) / "skal-ikke-skrives.flac"
            sentinel.write_bytes(b"read-only prototype sentinel")
            content_before = sentinel.read_bytes()
            modified_before = sentinel.stat().st_mtime_ns
            with override_settings(P7_MUSIC_ROOT=folder, P7_NAS_ROOT=folder):
                for name in (
                    "gui_v2:home",
                    "gui_v2:music_library",
                    "gui_v2:release_tracks",
                ):
                    url = reverse(name)
                    self.assertEqual(self.client.get(url).status_code, 200)
                    self.assertEqual(self.client.post(url, {"title": "Forsøk"}).status_code, 405)

            self.assertEqual(sentinel.read_bytes(), content_before)
            self.assertEqual(sentinel.stat().st_mtime_ns, modified_before)

        self.assertEqual(
            (Recording.objects.count(), Release.objects.count()),
            database_before,
        )

    def test_existing_workbench_urls_still_open(self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)

        for name in ("home", "workbench:library", "workbench:releases"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
