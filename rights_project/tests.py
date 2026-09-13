from pathlib import Path
from decimal import Decimal
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from catalogue.models import Release
from music_publisher.royalty_calculation import (
    RoyaltyCalculation,
    RoyaltyCalculationView,
)


class IntegratedHomeTests(TestCase):
    password = "test-password"

    def create_user(self, username, **extra):
        return get_user_model().objects.create_user(
            username=username,
            password=self.password,
            **extra,
        )

    def test_anonymous_start_redirects_to_login_and_preserves_destination(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response.url)
        self.assertEqual(parsed.path, reverse("admin:login"))
        self.assertEqual(parse_qs(parsed.query)["next"], [reverse("home")])

    def test_logged_in_administrator_sees_integrated_navigation(self):
        user = self.create_user("administrator", is_staff=True, is_superuser=True)
        self.client.force_login(user)

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Startside")
        self.assertContains(response, 'id="nav-sidebar"', html=False)
        for label in (
            "Musikkarkiv",
            "Forvaltet musikk",
            "Utgivelser",
            "Kontroll",
            "Hjelp",
        ):
            self.assertContains(response, label)

    def test_start_navigation_respects_model_permissions(self):
        user = self.create_user("release-reader", is_staff=True)
        permission = Permission.objects.get(
            content_type__app_label="catalogue",
            codename="view_release",
        )
        user.user_permissions.add(permission)
        self.client.force_login(user)

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Utgivelser")
        self.assertContains(response, "Hjelp")
        self.assertNotContains(response, "Åpne Musikkarkiv")
        self.assertNotContains(response, "Åpne Forvaltet musikk")
        self.assertNotContains(response, "Mulige dubletter")
        self.assertNotContains(response, "Kilder og verifikasjon")

    def test_non_staff_user_cannot_open_internal_start(self):
        user = self.create_user("ordinary-user")
        self.client.force_login(user)

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response.url).path, reverse("admin:login"))

    def test_login_returns_user_to_requested_record(self):
        user = self.create_user("deep-link-admin", is_staff=True, is_superuser=True)
        release = Release.objects.create(title="Direkte utgivelse")
        change_url = reverse("admin:catalogue_release_change", args=(release.pk,))

        response = self.client.get(change_url)
        parsed = urlparse(response.url)
        self.assertEqual(parsed.path, reverse("admin:login"))
        self.assertEqual(parse_qs(parsed.query)["next"], [change_url])

        response = self.client.post(
            reverse("admin:login"),
            {
                "username": user.username,
                "password": self.password,
                "next": change_url,
            },
        )
        self.assertRedirects(response, change_url, fetch_redirect_response=False)
        response = self.client.get(change_url)
        self.assertContains(response, "Direkte utgivelse")


class DownloadCompatibilityTests(SimpleTestCase):
    def test_royalty_download_deleted_after_stream_closed(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "royalties.csv"
            path.write_bytes(b"amount\n1.00\n")
            result = SimpleNamespace(out_file_path=str(path), filename="royalties.csv")
            with patch(
                "music_publisher.royalty_calculation.RoyaltyCalculation",
                return_value=result,
            ):
                response = RoyaltyCalculationView().form_valid(None)
            self.assertTrue(path.exists())
            self.assertEqual(b"".join(response.streaming_content), b"amount\n1.00\n")
            response.close()
            self.assertFalse(path.exists())
            response.close()


class MigrationCompatibilityTests(TestCase):
    def test_dmp_and_canonical_tables_and_renamed_index_exist(self):
        tables = connection.introspection.table_names()
        for name in (
            "music_publisher_work",
            "music_publisher_recording",
            "catalogue_recording",
            "parties_party",
        ):
            self.assertIn(name, tables)
        with connection.cursor() as cursor:
            indexes = connection.introspection.get_constraints(
                cursor, "music_publisher_workacknowledgement"
            )
        self.assertEqual(
            indexes["music_publi_society_ebcad0_idx"]["columns"],
            ["society_code", "remote_work_id"],
        )


class RoyaltyCalculationSafetyTests(SimpleTestCase):
    def calculation(self, share):
        calculation = RoyaltyCalculation.__new__(RoyaltyCalculation)
        calculation.wc = 0
        calculation.ac = 1
        calculation.right = "p"
        calculation.rc = None
        calculation.algo = "share"
        calculation.work_id_source = "ISWC"
        calculation.default_fee = Decimal("0")
        calculation.works = {
            "T1234567894": [
                {
                    "writer_id": 1,
                    "role": "Composer",
                    "relative_share": Decimal(share),
                    "fee": None,
                }
            ]
        }
        calculation.writers = {1: {"name": "Writer", "account_number": "", "fee": None}}
        return calculation

    def test_invalid_amount_is_reported_instead_of_crashing(self):
        rows = list(
            self.calculation("100").process_row(["T-123.456.789-4", "not-a-number"])
        )
        self.assertEqual(rows[0][-1], "ERROR: Invalid amount")

    def test_zero_controlled_share_is_reported_instead_of_dividing_by_zero(self):
        rows = list(self.calculation("0").process_row(["T-123.456.789-4", "100"]))
        self.assertEqual(rows[0][-1], "ERROR: Controlled share is zero")
