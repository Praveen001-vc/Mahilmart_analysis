from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import os

from django.utils import timezone

from .models import SalesLedgerRecord, SalesPaymentMode

try:
    import pyodbc
except ImportError:  # pragma: no cover - covered through runtime configuration.
    pyodbc = None


SALES_SYNC_SELECT = """
SELECT
    SalMas_SNo AS SalMas_SNo,
    ISNULL(SalMas_BillNo, '') AS SalMas_BillNo,
    SalMas_Date AS SalMas_Date,
    ISNULL(SalMas_Add1, '') AS SalMas_Add1,
    ISNULL(SalMas_NetAmt, 0) AS SalMas_NetAmt,
    ISNULL(Recd_Amt, 0) AS Recd_Amt,
    ISNULL(Bal_Amt, 0) AS Bal_Amt,
    ISNULL(SalMas_CardNo, '') AS SalMas_CardNo,
    ISNULL(SalMas_Cancel, 0) AS SalMas_Cancel
FROM dbo.SalMas_Table
"""

PREFERRED_SQLSERVER_DRIVERS = (
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "SQL Server",
)


class SalesSyncError(RuntimeError):
    pass


@dataclass
class SalesSyncStats:
    fetched_count: int = 0
    inserted_count: int = 0
    refreshed_count: int = 0


def normalize_text(value):
    return (value or "").strip()


def normalize_decimal(value):
    if value in (None, ""):
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def normalize_sale_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def has_meaningful_card_number(card_number):
    cleaned = normalize_text(card_number)
    if not cleaned or "http" in cleaned.casefold():
        return False
    condensed = "".join(character for character in cleaned if character.isalnum())
    return len(condensed) >= 4


def classify_sales_payment_mode(received_amount, balance_amount, card_number=""):
    if balance_amount > 0:
        return SalesPaymentMode.CREDIT
    if received_amount > 0 and balance_amount <= 0 and has_meaningful_card_number(card_number):
        return SalesPaymentMode.CARD
    if received_amount > 0 and balance_amount <= 0:
        return SalesPaymentMode.CASH
    return SalesPaymentMode.UNKNOWN


def is_implausible_money_amount(amount, net_amount):
    absolute_amount = abs(amount)
    absolute_net_amount = abs(net_amount)
    if absolute_amount >= Decimal("1000000.00"):
        return True
    if absolute_net_amount == 0:
        return False
    return absolute_amount > absolute_net_amount * Decimal("100")


def sanitize_sales_amounts(net_amount, received_amount, balance_amount):
    cleaned_net_amount = normalize_decimal(net_amount)
    cleaned_received_amount = normalize_decimal(received_amount)
    cleaned_balance_amount = normalize_decimal(balance_amount)
    had_amount_anomaly = False

    if is_implausible_money_amount(cleaned_received_amount, cleaned_net_amount):
        cleaned_received_amount = Decimal("0.00")
        had_amount_anomaly = True

    if is_implausible_money_amount(cleaned_balance_amount, cleaned_net_amount):
        cleaned_balance_amount = (
            cleaned_net_amount - cleaned_received_amount
            if cleaned_received_amount > 0
            else cleaned_net_amount
        )
        had_amount_anomaly = True

    return (
        cleaned_net_amount,
        cleaned_received_amount,
        cleaned_balance_amount,
        had_amount_anomaly,
    )


def get_sqlserver_driver():
    if pyodbc is None:
        raise SalesSyncError(
            "pyodbc is not installed. Add pyodbc to the environment before syncing sales."
        )

    available_drivers = list(pyodbc.drivers())
    configured_driver = normalize_text(os.getenv("SQLSERVER_DRIVER"))
    candidate_drivers = []
    if configured_driver:
        candidate_drivers.append(configured_driver)
    candidate_drivers.extend(PREFERRED_SQLSERVER_DRIVERS)

    for driver_name in candidate_drivers:
        if driver_name in available_drivers:
            return driver_name

    raise SalesSyncError(
        "No supported SQL Server ODBC driver was found. "
        f"Available drivers: {', '.join(available_drivers) or 'none'}."
    )


def get_required_sqlserver_setting(name):
    value = normalize_text(os.getenv(name))
    if value:
        return value
    raise SalesSyncError(
        f"{name} is not set. Add it to the .env file before syncing sales."
    )


def build_sqlserver_connection_string():
    driver_name = get_sqlserver_driver()
    host = get_required_sqlserver_setting("SQLSERVER_HOST")
    port = normalize_text(os.getenv("SQLSERVER_PORT")) or "1433"
    database = normalize_text(os.getenv("SQLSERVER_DATABASE")) or "MahilMart-Analytics"
    user = normalize_text(os.getenv("SQLSERVER_USER")) or "mahilmartuser"
    password = os.getenv("SQLSERVER_PASSWORD") or "Admin@123"

    connection_parts = [
        f"DRIVER={{{driver_name}}}",
        f"SERVER={host},{port}",
        f"DATABASE={database}",
        f"UID={user}",
        f"PWD={password}",
    ]
    if driver_name != "SQL Server":
        connection_parts.append("TrustServerCertificate=yes")
    return ";".join(connection_parts) + ";"


def normalize_sales_sync_dates(date_from=None, date_to=None):
    if date_from and not date_to:
        date_to = date_from
    if date_to and not date_from:
        date_from = date_to
    if not date_from and not date_to:
        today = timezone.localdate()
        return today, today
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from
    return date_from, date_to


def build_sales_sync_query(date_from=None, date_to=None):
    date_from, date_to = normalize_sales_sync_dates(date_from, date_to)
    query_parts = [SALES_SYNC_SELECT]
    parameters = []

    if date_from:
        query_parts.append("WHERE CAST(SalMas_Date AS date) >= ?")
        parameters.append(date_from)
    if date_to:
        where_or_and = "AND" if parameters else "WHERE"
        query_parts.append(f"{where_or_and} CAST(SalMas_Date AS date) <= ?")
        parameters.append(date_to)

    query_parts.append("ORDER BY SalMas_SNo")
    return "\n".join(query_parts), parameters


def build_sales_record(row, synced_at):
    net_amount, received_amount, balance_amount, had_amount_anomaly = (
        sanitize_sales_amounts(
            net_amount=row.SalMas_NetAmt,
            received_amount=row.Recd_Amt,
            balance_amount=row.Bal_Amt,
        )
    )
    source_card_no = normalize_text(row.SalMas_CardNo)
    return SalesLedgerRecord(
        source_sale_no=int(row.SalMas_SNo),
        bill_no=normalize_text(row.SalMas_BillNo),
        sale_date=normalize_sale_date(row.SalMas_Date),
        customer_name=normalize_text(row.SalMas_Add1),
        net_amount=net_amount,
        received_amount=received_amount,
        balance_amount=balance_amount,
        payment_mode=(
            SalesPaymentMode.UNKNOWN
            if had_amount_anomaly
            else classify_sales_payment_mode(
                received_amount=received_amount,
                balance_amount=balance_amount,
                card_number=source_card_no,
            )
        ),
        source_card_no=source_card_no,
        is_cancelled=bool(row.SalMas_Cancel),
        synced_at=synced_at,
    )


def upsert_sales_batch(records, stats):
    if not records:
        return

    source_sale_numbers = [record.source_sale_no for record in records]
    existing_sale_numbers = set(
        SalesLedgerRecord.objects.filter(
            source_sale_no__in=source_sale_numbers
        ).values_list("source_sale_no", flat=True)
    )
    stats.inserted_count += len(source_sale_numbers) - len(existing_sale_numbers)
    stats.refreshed_count += len(existing_sale_numbers)

    SalesLedgerRecord.objects.bulk_create(
        records,
        batch_size=len(records),
        update_conflicts=True,
        update_fields=[
            "bill_no",
            "sale_date",
            "customer_name",
            "net_amount",
            "received_amount",
            "balance_amount",
            "payment_mode",
            "source_card_no",
            "is_cancelled",
            "synced_at",
        ],
        unique_fields=["source_sale_no"],
    )


def sync_sales_from_sqlserver(date_from=None, date_to=None, batch_size=2000):
    connection_string = build_sqlserver_connection_string()
    stats = SalesSyncStats()
    query, parameters = build_sales_sync_query(date_from=date_from, date_to=date_to)

    try:
        connection = pyodbc.connect(
            connection_string,
            timeout=10,
        )
    except Exception as exc:  # pragma: no cover - integration failure path.
        raise SalesSyncError(f"Could not connect to SQL Server: {exc}") from exc

    try:
        cursor = connection.cursor()
        cursor.execute(query, parameters)

        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break

            synced_at = timezone.now()
            batch_records = [build_sales_record(row, synced_at) for row in rows]
            upsert_sales_batch(batch_records, stats)
            stats.fetched_count += len(batch_records)
    except Exception as exc:  # pragma: no cover - integration failure path.
        raise SalesSyncError(f"Could not sync sales data: {exc}") from exc
    finally:
        connection.close()

    return stats
