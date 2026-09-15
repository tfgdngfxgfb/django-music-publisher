from django.core.management.base import BaseCommand
from django.utils import timezone

from delivery.models import Delivery, DeliveryArtifact
from delivery.services import artifact_path


class Command(BaseCommand):
    help = "Fjerner utløpte Delivery-artefakter, men beholder leveransehistorikken."

    def handle(self, *args, **options):
        count = 0
        for artifact in DeliveryArtifact.objects.filter(
            expires_at__lte=timezone.now(), removed_at__isnull=True
        ).select_related("delivery"):
            path = artifact_path(artifact.relative_path)
            path.unlink(missing_ok=True)
            parent = path.parent
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
            delivery = artifact.delivery
            artifact.removed_at = timezone.now()
            artifact.save(update_fields={"removed_at"})
            if not delivery.artifacts.filter(removed_at__isnull=True).exists():
                delivery.status = Delivery.Status.EXPIRED
                delivery.save(update_fields={"status"})
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Fjernet {count} utløpte artefakter."))
