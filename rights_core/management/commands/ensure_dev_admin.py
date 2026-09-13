import os
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or update the explicitly configured local test administrator."

    def handle(self, *args, **options):
        username = os.environ.get("P7_DEV_ADMIN_USERNAME")
        password = os.environ.get("P7_DEV_ADMIN_PASSWORD")
        if not username:
            raise CommandError("Brukernavn for lokal administrator mangler.")

        user_model = get_user_model()
        user = user_model.objects.filter(username=username).first()
        if user and not password:
            self.stdout.write(self.style.SUCCESS("Lokal administrator er klar."))
            return

        generated = not password
        password = password or secrets.token_urlsafe(18)
        user, created = user_model.objects.get_or_create(username=username)
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save()
        message = (
            "Lokal administrator ble opprettet."
            if created
            else "Passordet til lokal administrator ble oppdatert."
        )
        self.stdout.write(self.style.SUCCESS(message))
        if generated:
            self.stdout.write(f"Midlertidig passord (vises bare nå): {password}")
