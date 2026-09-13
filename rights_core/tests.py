from unittest.mock import patch
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase


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
