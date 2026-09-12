import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or update the explicitly configured local test administrator."

    def handle(self, *args, **options):
        username = os.environ.get("P7_DEV_ADMIN_USERNAME")
        password = os.environ.get("P7_DEV_ADMIN_PASSWORD")
        if not username or not password:
            raise CommandError("Local administrator credentials were not provided.")

        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(username=username)
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save()
        message = (
            "Created local test administrator."
            if created
            else "Local test administrator is ready."
        )
        self.stdout.write(self.style.SUCCESS(message))
