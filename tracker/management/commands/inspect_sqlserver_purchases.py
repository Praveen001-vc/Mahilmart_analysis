import json

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from tracker.purchase_inspector import (
    PurchaseSourceInspectionResult,
    PurchaseSourceInspectorError,
    inspect_purchase_sources_in_sqlserver,
)


def build_result_payload(result):
    return {
        "purchase_preview_rows": result.purchase_preview_rows,
        "payment_column_rows": result.payment_column_rows,
        "active_keywords": result.active_keywords,
        "preview_table_name": result.preview_table_name,
        "preview_table_rows": result.preview_table_rows,
    }


def format_candidate_tables(payment_column_rows):
    grouped_tables = {}
    for row in payment_column_rows:
        table_key = f"{row['TABLE_SCHEMA']}.{row['TABLE_NAME']}"
        grouped_tables.setdefault(table_key, []).append(
            f"{row['COLUMN_NAME']} ({row['DATA_TYPE']})"
        )
    return grouped_tables


class Command(BaseCommand):
    help = (
        "Preview SQL Server PurMas purchase rows and search schema metadata for payment-related tables and columns."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=25,
            help="Maximum number of preview rows to show for PurMas and optional table preview.",
        )
        parser.add_argument(
            "--date-from",
            type=str,
            default="",
            help="Start purchase date in YYYY-MM-DD format.",
        )
        parser.add_argument(
            "--date-to",
            type=str,
            default="",
            help="End purchase date in YYYY-MM-DD format.",
        )
        parser.add_argument(
            "--supplier",
            type=str,
            default="",
            help="Optional supplier-name filter against PurMas_Add1.",
        )
        parser.add_argument(
            "--party",
            type=str,
            default="",
            help="Optional party reference filter against PurMas_Party.",
        )
        parser.add_argument(
            "--bill-no",
            type=str,
            default="",
            help="Optional bill number filter against PurMas_BillNo.",
        )
        parser.add_argument(
            "--voucher-no",
            type=str,
            default="",
            help="Optional voucher number filter against PurMas_VouNo.",
        )
        parser.add_argument(
            "--keyword",
            action="append",
            dest="keywords",
            default=[],
            help="Repeat to search additional payment-related keywords in table and column names.",
        )
        parser.add_argument(
            "--preview-table",
            type=str,
            default="",
            help="Optional table or schema.table name to preview after candidate discovery.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="output_json",
            help="Output machine-readable JSON instead of formatted text.",
        )

    def handle(self, *args, **options):
        raw_date_from = (options.get("date_from") or "").strip()
        raw_date_to = (options.get("date_to") or "").strip()
        date_from = parse_date(raw_date_from) if raw_date_from else None
        date_to = parse_date(raw_date_to) if raw_date_to else None

        if raw_date_from and date_from is None:
            raise CommandError("--date-from must be in YYYY-MM-DD format.")
        if raw_date_to and date_to is None:
            raise CommandError("--date-to must be in YYYY-MM-DD format.")

        try:
            result = inspect_purchase_sources_in_sqlserver(
                limit=options["limit"],
                date_from=date_from,
                date_to=date_to,
                supplier_name=options["supplier"],
                party_reference=options["party"],
                bill_no=options["bill_no"],
                voucher_no=options["voucher_no"],
                keywords=options.get("keywords") or None,
                preview_table=options["preview_table"],
            )
        except PurchaseSourceInspectorError as exc:
            raise CommandError(str(exc)) from exc

        if options["output_json"]:
            self.stdout.write(
                json.dumps(
                    build_result_payload(result),
                    indent=2,
                    default=str,
                )
            )
            return

        self._write_purchase_preview(result)
        self.stdout.write("")
        self._write_payment_candidates(result)
        if result.preview_table_name:
            self.stdout.write("")
            self._write_table_preview(result)

    def _write_purchase_preview(self, result: PurchaseSourceInspectionResult):
        self.stdout.write(self.style.MIGRATE_HEADING("PurMas Purchase Preview"))
        if not result.purchase_preview_rows:
            self.stdout.write("No matching PurMas purchase rows were found.")
            return

        for row in result.purchase_preview_rows:
            self.stdout.write(json.dumps(row, default=str))

    def _write_payment_candidates(self, result: PurchaseSourceInspectionResult):
        self.stdout.write(self.style.MIGRATE_HEADING("Payment Candidate Columns"))
        self.stdout.write(
            "Keywords used: " + ", ".join(result.active_keywords)
        )

        grouped_tables = format_candidate_tables(result.payment_column_rows)
        if not grouped_tables:
            self.stdout.write("No payment-related columns were found in INFORMATION_SCHEMA.COLUMNS.")
            return

        for table_name, columns in grouped_tables.items():
            self.stdout.write(f"{table_name}:")
            for column in columns:
                self.stdout.write(f"  - {column}")

    def _write_table_preview(self, result: PurchaseSourceInspectionResult):
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Preview Table Rows: {result.preview_table_name}"
            )
        )
        if not result.preview_table_rows:
            self.stdout.write("No rows were returned from the preview table.")
            return

        for row in result.preview_table_rows:
            self.stdout.write(json.dumps(row, default=str))
