from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from tracker.sales_sync import SalesSyncError, sync_sales_from_sqlserver


class Command(BaseCommand):
    help = "Sync sales records from SQL Server SalMas_Table into PostgreSQL."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=2000,
            help="Number of SQL Server rows to upsert into PostgreSQL per batch.",
        )
        parser.add_argument(
            "--date-from",
            type=str,
            help="Start sale date in YYYY-MM-DD format. Defaults to today.",
        )
        parser.add_argument(
            "--date-to",
            type=str,
            help="End sale date in YYYY-MM-DD format. Defaults to today.",
        )

    def handle(self, *args, **options):
        batch_size = max(1, options["batch_size"])
        raw_date_from = (options.get("date_from") or "").strip()
        raw_date_to = (options.get("date_to") or "").strip()
        date_from = parse_date(raw_date_from) if raw_date_from else None
        date_to = parse_date(raw_date_to) if raw_date_to else None

        if raw_date_from and date_from is None:
            raise CommandError("--date-from must be in YYYY-MM-DD format.")
        if raw_date_to and date_to is None:
            raise CommandError("--date-to must be in YYYY-MM-DD format.")

        try:
            stats = sync_sales_from_sqlserver(
                date_from=date_from,
                date_to=date_to,
                batch_size=batch_size,
            )
        except SalesSyncError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Sales sync completed: "
                f"{stats.fetched_count} rows processed, "
                f"{stats.inserted_count} inserted, "
                f"{stats.refreshed_count} refreshed."
            )
        )
