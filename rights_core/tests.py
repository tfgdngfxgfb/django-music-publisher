import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings

from catalogue.models import DuplicateCandidate, Recording, Release
from managed_music.models import ManagedRecording
from media_assets.models import FileAsset, FileLocation
from music_library.models import MusicLibraryEntry
from parties.models import Party
from provenance.models import MetadataAssertion
from rights.models import Agreement, RightsClaim, RightsConfiguration


class DevelopmentAdminCommandTests(TestCase):
    def test_generates_password_for_first_local_administrator(self):
        output = StringIO()
        with patch.dict(
            "os.environ",
            {"P7_DEV_ADMIN_USERNAME": "generated-admin"},
            clear=True,
        ):
            call_command("ensure_dev_admin", stdout=output, verbosity=0)
        user = get_user_model().objects.get(username="generated-admin")
        self.assertTrue(user.has_usable_password())
        self.assertIn("Midlertidig passord", output.getvalue())
        self.assertNotIn("123", output.getvalue())

    def test_creates_and_updates_configured_administrator(self):
        credentials = {
            "P7_DEV_ADMIN_USERNAME": "script-admin",
            "P7_DEV_ADMIN_PASSWORD": "first-password",
        }
        with patch.dict("os.environ", credentials):
            call_command("ensure_dev_admin", verbosity=0)

        user = get_user_model().objects.get(username="script-admin")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("first-password"))

        credentials["P7_DEV_ADMIN_PASSWORD"] = "updated-password"
        with patch.dict("os.environ", credentials):
            call_command("ensure_dev_admin", verbosity=0)
        user.refresh_from_db()
        self.assertTrue(user.check_password("updated-password"))

    def test_existing_administrator_is_not_reset_without_password(self):
        user = get_user_model().objects.create_superuser(
            username="existing-admin", password="keep-this-password"
        )
        with patch.dict(
            "os.environ",
            {"P7_DEV_ADMIN_USERNAME": "existing-admin"},
            clear=True,
        ):
            call_command("ensure_dev_admin", verbosity=0)
        user.refresh_from_db()
        self.assertTrue(user.check_password("keep-this-password"))


class SmokeDataCommandTests(TestCase):
    def test_refuses_to_write_without_explicit_smoke_environment(self):
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(CommandError):
            call_command("create_phase2_smoke_data", verbosity=0)


class DemoDataTests(TestCase):
    @override_settings(DEBUG=True)
    def test_demo_data_and_files_are_fixed_and_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            output = StringIO()
            call_command("load_demo_data", files_root=folder, stdout=output)
            counts = self._counts()
            call_command("load_demo_data", files_root=folder, stdout=output)

            self.assertEqual(self._counts(), counts)
            self.assertEqual(counts["recordings"], 5)
            self.assertEqual(counts["managed"], 1)
            self.assertEqual(counts["duplicates"], 1)
            self.assertEqual(counts["rights_claims"], 4)
            self.assertEqual(counts["agreements"], 1)
            self.assertEqual(counts["rights_configuration"], 1)
            self.assertTrue(
                Recording.objects.filter(
                    pk="70000000-0000-4000-8000-000000000030",
                    title="Nordlys over byen (demo)",
                ).exists()
            )
            self.assertTrue(
                Path(
                    folder,
                    "P7-Demo/Aurora/P7-DEMO-001/Cover/p7-demo-cover.png",
                ).is_file()
            )
            self.assertTrue(
                Path(
                    folder,
                    "P7-Demo/Aurora/P7-DEMO-001/Audio/nordlys-demo-tone.wav",
                ).is_file()
            )

    @override_settings(DEBUG=False)
    def test_demo_data_is_refused_outside_debug(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(CommandError):
                call_command("load_demo_data", files_root=folder, verbosity=0)

    def _counts(self):
        return {
            "parties": Party.objects.count(),
            "recordings": Recording.objects.count(),
            "releases": Release.objects.count(),
            "library": MusicLibraryEntry.objects.count(),
            "managed": ManagedRecording.objects.count(),
            "assertions": MetadataAssertion.objects.count(),
            "duplicates": DuplicateCandidate.objects.count(),
            "assets": FileAsset.objects.count(),
            "locations": FileLocation.objects.count(),
            "rights_claims": RightsClaim.objects.count(),
            "agreements": Agreement.objects.count(),
            "rights_configuration": RightsConfiguration.objects.count(),
        }
