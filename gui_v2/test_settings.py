import tempfile
from pathlib import Path

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from catalogue.models import Release
from media_assets.digitization_configuration import picker_defaults
from media_assets.digitization_storage import suggested_folder
from media_assets.models import DigitizationBatch, DigitizationConfiguration, FileAsset
from parties.models import Party
from rights.models import RightsConfiguration


@override_settings(GUI_V2_WRITES_ENABLED=True)
class SettingsPageTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            "settings-admin", password="test"
        )
        self.client.force_login(self.admin)
        self.url = reverse("gui_v2:settings")
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "RAW").mkdir()
        (self.root / "Master").mkdir()
        self.override = override_settings(
            P7_MUSIC_ROOT=str(self.root), P7_STORAGE_ROOTS={}
        )
        self.override.enable()
        self.addCleanup(self.override.disable)

    def payload(self, **extra):
        return {
            "action": "digitization",
            "raw_root_key": "music_library",
            "raw_base_path": "RAW",
            "raw_folder_template": "{label}/{series}",
            "master_root_key": "music_library",
            "master_base_path": "Master",
            "master_folder_template": "{catalogue_number} - {title}",
            **extra,
        }

    def test_get_is_read_only_and_all_admin_tabs_render(self):
        for tab in ("personal", "digitization", "organization", "operation"):
            response = self.client.get(self.url, {"tab": tab})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Innstillinger")
        self.assertFalse(DigitizationConfiguration.objects.exists())
        self.assertFalse(RightsConfiguration.objects.exists())
        self.assertFalse(LogEntry.objects.exists())

    def test_login_and_admin_boundary(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        reader = get_user_model().objects.create_user("settings-reader", is_staff=True)
        self.client.force_login(reader)
        response = self.client.get(self.url, {"tab": "digitization"})
        self.assertContains(response, "Min arbeidsflate")
        self.assertNotContains(response, str(self.root))
        self.assertNotContains(response, "Lagring og digitalisering")
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 403)

    def test_save_defaults_used_by_source_picker_and_folder_hint(self):
        response = self.client.post(self.url, self.payload())
        self.assertRedirects(response, self.url + "?tab=digitization")
        config = DigitizationConfiguration.objects.get()
        self.assertEqual(config.updated_by, self.admin)
        defaults = picker_defaults(FileAsset.Role.EDITED_WAV_MASTER)
        self.assertEqual(defaults.base_path, "Master")
        release = Release.objects.create(title="Min sang", catalogue_number="FMC 102")
        (self.root / "Master" / "FMC 102 - Min sang").mkdir()
        hint = suggested_folder(
            release, FileAsset.Role.EDITED_WAV_MASTER, "music_library"
        )
        self.assertEqual(hint["path"], "Master/FMC 102 - Min sang")
        self.assertTrue(hint["matched"])
        self.assertEqual(LogEntry.objects.count(), 1)
        self.assertEqual(list((self.root / "RAW").iterdir()), [])

    def test_missing_release_metadata_starts_in_configured_base(self):
        self.client.post(self.url, self.payload())
        release = Release.objects.create(title="Ukjent kilde")
        hint = suggested_folder(
            release, FileAsset.Role.RAW_DIGITIZATION, "music_library"
        )
        self.assertTrue(hint["missing"])
        self.assertEqual(hint["path"], "RAW")

    def test_digitization_uses_saved_root_instead_of_deployment_default(self):
        with self.settings(
            P7_STORAGE_ROOTS={"raw_sources": {"server_root": str(self.root)}}
        ):
            self.client.post(self.url, self.payload())
            release = Release.objects.create(title="Ny utgivelse")
            batch = DigitizationBatch.objects.create(
                release=release, title="Digitalisering", created_by=self.admin
            )
            response = self.client.get(
                reverse("gui_v2:digitization_detail", args=[batch.pk])
            )
            self.assertEqual(response.context["raw_default_root"], "music_library")
            response = self.client.get(
                reverse("gui_v2:digitization_browse_files", args=[batch.pk]),
                {
                    "role": "raw_digitization",
                    "root_key": "music_library",
                    "browse": "1",
                    "suggest": "1",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.context["browse_form"].cleaned_data["relative_path"], "RAW"
            )

    def test_unsafe_paths_unknown_roots_and_templates_are_rejected(self):
        for extra in (
            {"raw_base_path": "../outside"},
            {"raw_base_path": str(self.root)},
            {"raw_root_key": "arbitrary-root"},
            {"raw_base_path": "not-found"},
            {"raw_folder_template": "{title.__class__}"},
            {"master_folder_template": "../outside"},
            {"master_folder_template": "{title:>1000000}"},
        ):
            with self.subTest(extra=extra):
                response = self.client.post(self.url, self.payload(**extra))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["digitization_form"].errors)
                self.assertFalse(DigitizationConfiguration.objects.exists())

    def test_writes_disabled_and_csrf_protected(self):
        with self.settings(GUI_V2_WRITES_ENABLED=False):
            self.assertEqual(
                self.client.post(self.url, self.payload()).status_code, 403
            )
        protected_client = Client(enforce_csrf_checks=True)
        protected_client.force_login(self.admin)
        self.assertEqual(
            protected_client.post(self.url, self.payload()).status_code, 403
        )
        self.assertFalse(DigitizationConfiguration.objects.exists())

    def test_blank_template_uses_base_and_no_file_scan(self):
        self.client.post(self.url, self.payload(master_folder_template=""))
        release = Release.objects.create(title="Utgivelse")
        self.assertEqual(
            suggested_folder(
                release, FileAsset.Role.EDITED_WAV_MASTER, "music_library"
            )["path"],
            "Master",
        )

    def test_organization_change_requires_confirmation_and_is_audited(self):
        local = Party.objects.create(name="P7", kind="organization")
        other = Party.objects.create(name="Annen organisasjon", kind="organization")
        response = self.client.post(
            self.url, {"action": "organization", "local_organization": local.pk}
        )
        self.assertRedirects(response, self.url + "?tab=organization")
        response = self.client.post(
            self.url, {"action": "organization", "local_organization": other.pk}
        )
        self.assertContains(response, "Bekreft konsekvensen")
        self.assertEqual(response.context["organization_name"], "P7")
        self.assertEqual(RightsConfiguration.objects.get().local_organization, local)
        response = self.client.post(
            self.url,
            {
                "action": "organization",
                "local_organization": other.pk,
                "confirm_change": "on",
            },
        )
        self.assertRedirects(response, self.url + "?tab=organization")
        self.assertEqual(RightsConfiguration.objects.get().local_organization, other)
        self.assertEqual(LogEntry.objects.count(), 2)

    def test_person_cannot_be_selected_as_local_organization(self):
        person = Party.objects.create(name="Person", kind="person")
        response = self.client.post(
            self.url, {"action": "organization", "local_organization": person.pk}
        )
        self.assertTrue(response.context["organization_form"].errors)
        self.assertFalse(RightsConfiguration.objects.exists())
