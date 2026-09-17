from datetime import date

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from managed_music.lifecycle import reconcile_management_statuses


class Command(BaseCommand):
    help = (
        "Oppdater avledet status på eksisterende Forvaltet musikk-medlemskap."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--on-date",
            type=date.fromisoformat,
            metavar="YYYY-MM-DD",
            help="Vurderingsdato; standard er dagens dato i Django-tidssonen.",
        )
        parser.add_argument("--batch-size", type=int, default=200)

    def handle(self, *args, **options):
        try:
            result = reconcile_management_statuses(
                on_date=options["on_date"],
                dry_run=options["dry_run"],
                batch_size=options["batch_size"],
            )
        except (ValidationError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"Dato: {result.on_date}. "
            f"{'Dry-run' if result.dry_run else 'Reconciliation'}: "
            f"{result.examined} vurdert, "
            f"{result.needs_reconciliation} med statusavvik, "
            f"{result.updated} oppdatert, "
            f"{result.uncertain} med usikker historikk, "
            f"{result.requires_follow_up} krever oppfølging."
        )
