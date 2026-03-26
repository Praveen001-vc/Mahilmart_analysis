from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils.dateparse import parse_date

from .models import PurchaseRecord, Supplier
from .purchase_helpers import sync_purchase_to_expense
from .sales_sync import (
    build_sqlserver_connection_string,
    format_sqlserver_date_literal,
    normalize_text,
    pyodbc,
)
from .user_roles import filter_queryset_by_role


SQLSERVER_PURCHASE_SOURCE_PREFIX = "SQLPURMAS:"

PURCHASE_SYNC_SELECT = """
SELECT
    PurMas_SNo AS PurMas_SNo,
    LTRIM(RTRIM(ISNULL(CONVERT(varchar(50), PurMas_Party), ''))) AS PurMas_Party,
    LTRIM(RTRIM(ISNULL(PurMas_Add1, ''))) AS SupplierName,
    LTRIM(RTRIM(ISNULL(PurMas_BillNo, ''))) AS PurMas_BillNo,
    LTRIM(RTRIM(ISNULL(PurMas_VouNo, ''))) AS PurMas_VouNo,
    ISNULL(PurMas_Type, 0) AS PurMas_Type,
    CAST(ISNULL(PurMas_VouDate, PurMas_Date) AS date) AS PurchaseDate,
    ISNULL(PurMas_NetAmt, 0) AS PurMas_NetAmt,
    LTRIM(RTRIM(ISNULL(PurMas_Remarks, ''))) AS PurMas_Remarks
FROM dbo.PurMas_Table
WHERE ISNULL(PurMas_Cancel, 0) = 0
  AND ISNULL(PurMas_NetAmt, 0) > 0
"""


class PurchaseSyncError(RuntimeError):
    pass


@dataclass
class PurchaseSyncStats:
    fetched_count: int = 0
    inserted_count: int = 0
    refreshed_count: int = 0
    skipped_count: int = 0


def normalize_purchase_source_type(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def classify_purchase_type(source_purchase_type):
    if source_purchase_type == 1:
        return "Cash"
    if source_purchase_type == 2:
        return "Credit"
    return "Imported"


def get_source_paid_amount(source_purchase_type, total_amount):
    if source_purchase_type == 1:
        return total_amount
    return Decimal("0.00")


def normalize_purchase_amount(value):
    if value in (None, ""):
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def normalize_purchase_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return parse_date(value.strip())
    return None


def normalize_date_range(date_from=None, date_to=None):
    if date_from and date_to and date_from > date_to:
        return date_to, date_from
    return date_from, date_to


def normalize_source_party_number(value):
    text_value = normalize_source_text(value)
    if not text_value:
        return None
    try:
        return int(text_value)
    except (TypeError, ValueError):
        return None


def normalize_source_text(value):
    if value in (None, ""):
        return ""
    return normalize_text(str(value))


def normalize_lookup_key(value):
    normalized_value = normalize_source_text(value)
    if not normalized_value:
        return ""
    return normalized_value.casefold()


def build_purchase_sync_query(
    date_from=None,
    date_to=None,
    supplier_name="",
    invoice_number="",
):
    date_from, date_to = normalize_date_range(date_from=date_from, date_to=date_to)
    query_parts = [PURCHASE_SYNC_SELECT]
    parameters = []

    if date_from is not None:
        query_parts.append(
            "AND CAST(ISNULL(PurMas_VouDate, PurMas_Date) AS date) >= "
            f"{format_sqlserver_date_literal(date_from)}"
        )
    if date_to is not None:
        query_parts.append(
            "AND CAST(ISNULL(PurMas_VouDate, PurMas_Date) AS date) <= "
            f"{format_sqlserver_date_literal(date_to)}"
        )

    supplier_name = normalize_source_text(supplier_name)
    if supplier_name:
        query_parts.append("AND LTRIM(RTRIM(ISNULL(PurMas_Add1, ''))) LIKE ?")
        parameters.append(f"%{supplier_name}%")

    invoice_number = normalize_source_text(invoice_number)
    if invoice_number:
        query_parts.append(
            "AND ("
            "LTRIM(RTRIM(ISNULL(PurMas_BillNo, ''))) LIKE ? "
            "OR LTRIM(RTRIM(ISNULL(PurMas_VouNo, ''))) LIKE ?"
            ")"
        )
        parameters.extend((f"%{invoice_number}%", f"%{invoice_number}%"))

    query_parts.append(
        "ORDER BY CAST(ISNULL(PurMas_VouDate, PurMas_Date) AS date) DESC, PurMas_SNo DESC"
    )
    return "\n".join(query_parts), parameters


def build_visible_supplier_maps(user):
    visible_suppliers = list(
        filter_queryset_by_role(Supplier.objects.all().order_by("pk"), user)
    )
    suppliers_by_source_number = {
        supplier.source_supplier_no: supplier
        for supplier in visible_suppliers
        if supplier.source_supplier_no is not None
    }
    suppliers_by_name = {}
    for supplier in visible_suppliers:
        supplier_name_key = normalize_lookup_key(supplier.name)
        if supplier_name_key and supplier_name_key not in suppliers_by_name:
            suppliers_by_name[supplier_name_key] = supplier
    return suppliers_by_source_number, suppliers_by_name


def get_matched_supplier(
    suppliers_by_source_number,
    suppliers_by_name,
    party_reference,
    supplier_name,
):
    source_supplier_number = normalize_source_party_number(party_reference)
    if source_supplier_number is not None:
        supplier = suppliers_by_source_number.get(source_supplier_number)
        if supplier is not None:
            return supplier

    supplier_name_key = normalize_lookup_key(supplier_name)
    if supplier_name_key:
        return suppliers_by_name.get(supplier_name_key)
    return None


def build_synced_purchase_notes(
    bill_no,
    voucher_no,
    party_reference,
    remarks,
):
    note_parts = [
        "Synced from SQL Server PurMas_Table",
        f"Bill No: {bill_no}" if bill_no else "",
        f"Voucher No: {voucher_no}" if voucher_no else "",
        f"Party Ref: {party_reference}" if party_reference else "",
        f"Remarks: {remarks}" if remarks else "",
    ]
    return " | ".join(part for part in note_parts if part)


def sync_purchases_from_rows(rows, user):
    stats = PurchaseSyncStats()
    suppliers_by_source_number, suppliers_by_name = build_visible_supplier_maps(user)
    visible_purchases = list(
        filter_queryset_by_role(
            PurchaseRecord.objects.select_related("supplier", "user").order_by("pk"),
            user,
        ).filter(source_reference__startswith=SQLSERVER_PURCHASE_SOURCE_PREFIX)
    )
    purchases_by_source_reference = {
        purchase.source_reference: purchase for purchase in visible_purchases
    }

    with transaction.atomic():
        for row in rows:
            stats.fetched_count += 1

            source_purchase_number = normalize_source_text(
                getattr(row, "PurMas_SNo", "")
            )
            party_reference = normalize_source_text(getattr(row, "PurMas_Party", ""))
            supplier_name = normalize_source_text(
                getattr(row, "SupplierName", "")
            ) or party_reference
            bill_no = normalize_source_text(getattr(row, "PurMas_BillNo", ""))
            voucher_no = normalize_source_text(getattr(row, "PurMas_VouNo", ""))
            remarks = normalize_source_text(getattr(row, "PurMas_Remarks", ""))
            purchase_date = normalize_purchase_date(getattr(row, "PurchaseDate", None))
            total_amount = normalize_purchase_amount(getattr(row, "PurMas_NetAmt", 0))
            source_purchase_type = normalize_purchase_source_type(
                getattr(row, "PurMas_Type", 0)
            )

            if (
                not source_purchase_number
                or not supplier_name
                or total_amount <= 0
                or purchase_date is None
            ):
                stats.skipped_count += 1
                continue

            invoice_number = bill_no or voucher_no or f"PURMAS-{source_purchase_number}"
            matched_supplier = get_matched_supplier(
                suppliers_by_source_number=suppliers_by_source_number,
                suppliers_by_name=suppliers_by_name,
                party_reference=party_reference,
                supplier_name=supplier_name,
            )
            source_reference = (
                f"{SQLSERVER_PURCHASE_SOURCE_PREFIX}{source_purchase_number}"
            )
            notes = build_synced_purchase_notes(
                bill_no=bill_no,
                voucher_no=voucher_no,
                party_reference=party_reference,
                remarks=remarks,
            )
            existing_purchase = purchases_by_source_reference.get(source_reference)
            purchase_type = classify_purchase_type(source_purchase_type)
            source_paid_amount = get_source_paid_amount(
                source_purchase_type=source_purchase_type,
                total_amount=total_amount,
            )

            if existing_purchase is None:
                purchase = PurchaseRecord.objects.create(
                    user=user,
                    supplier=matched_supplier,
                    supplier_name=matched_supplier.name if matched_supplier else supplier_name,
                    purchase_type=purchase_type,
                    invoice_number=invoice_number,
                    total_amount=total_amount,
                    paid_amount=source_paid_amount,
                    transaction_date=purchase_date,
                    notes=notes,
                    source_reference=source_reference,
                )
                purchases_by_source_reference[source_reference] = purchase
                stats.inserted_count += 1
            else:
                if matched_supplier is not None or not existing_purchase.supplier_id:
                    existing_purchase.supplier = matched_supplier
                existing_purchase.purchase_type = purchase_type
                existing_purchase.supplier_name = (
                    matched_supplier.name
                    if matched_supplier is not None
                    else (
                        existing_purchase.supplier.name
                        if existing_purchase.supplier_id
                        else supplier_name
                    )
                )
                existing_purchase.invoice_number = invoice_number
                existing_purchase.paid_amount = max(
                    existing_purchase.paid_amount or Decimal("0.00"),
                    source_paid_amount,
                )
                existing_purchase.total_amount = max(
                    total_amount,
                    existing_purchase.paid_amount or Decimal("0.00"),
                )
                existing_purchase.transaction_date = purchase_date
                existing_purchase.notes = notes
                existing_purchase.save()
                purchase = existing_purchase
                stats.refreshed_count += 1

            sync_purchase_to_expense(purchase)

    return stats


def sync_purchases_from_sqlserver(
    user,
    date_from=None,
    date_to=None,
    supplier_name="",
    invoice_number="",
    batch_size=1000,
):
    try:
        connection_string = build_sqlserver_connection_string()
    except Exception as exc:
        raise PurchaseSyncError(str(exc)) from exc

    if pyodbc is None:
        raise PurchaseSyncError(
            "pyodbc is not installed. Add pyodbc to the environment before syncing purchases."
        )

    query, parameters = build_purchase_sync_query(
        date_from=date_from,
        date_to=date_to,
        supplier_name=supplier_name,
        invoice_number=invoice_number,
    )

    try:
        connection = pyodbc.connect(
            connection_string,
            timeout=10,
        )
    except Exception as exc:
        raise PurchaseSyncError(f"Could not connect to SQL Server: {exc}") from exc

    stats = PurchaseSyncStats()
    try:
        cursor = connection.cursor()
        if parameters:
            cursor.execute(query, parameters)
        else:
            cursor.execute(query)

        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break

            batch_stats = sync_purchases_from_rows(rows, user)
            stats.fetched_count += batch_stats.fetched_count
            stats.inserted_count += batch_stats.inserted_count
            stats.refreshed_count += batch_stats.refreshed_count
            stats.skipped_count += batch_stats.skipped_count
    except Exception as exc:
        raise PurchaseSyncError(f"Could not sync purchase data: {exc}") from exc
    finally:
        connection.close()

    return stats
