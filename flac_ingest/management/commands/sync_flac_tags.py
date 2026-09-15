from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from flac_ingest.services import sync_file_asset
from media_assets.models import FileAsset


class Command(BaseCommand):
    help = "Prøv katalogsynkronisering for ventende radio-FLAC-filer på nytt."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all-failed",
            action="store_true",
            help="Ta også med filer som mangler eller feilet ved forrige forsøk.",
        )

    def handle(self, *args, **options):
        if not getattr(settings, "P7_ALLOW_FILE_WRITES", False):
            raise CommandError(
                "Filskriving er deaktivert. P7_ALLOW_FILE_WRITES må aktiveres "
                "uttrykkelig før denne synkroniseringskommandoen kan brukes."
            )
        statuses = [FileAsset.SyncStatus.PENDING]
        if options["all_failed"]:
            statuses.extend(
                (FileAsset.SyncStatus.MISSING, FileAsset.SyncStatus.FAILED)
            )
        assets = FileAsset.objects.filter(
            role=FileAsset.Role.RADIO_FLAC,
            recording__isnull=False,
            sync_status__in=statuses,
        ).order_by("created_at", "id")
        success = 0
        failed = 0
        for asset in assets:
            result = sync_file_asset(asset.pk)
            if result and result.result == "success":
                success += 1
            else:
                failed += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"FLAC-synkronisering ferdig: {success} synkronisert, {failed} ikke synkronisert."
            )
        )
