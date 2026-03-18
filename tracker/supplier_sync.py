from dataclasses import dataclass

from .models import Supplier, SupplierStatus
from .sales_sync import build_sqlserver_connection_string, normalize_text, pyodbc
from .user_roles import filter_queryset_by_role


SUPPLIER_SYNC_SELECT = """
SELECT
    ranked_suppliers.SourceSupplierNo,
    ranked_suppliers.SupplierName,
    ranked_suppliers.AddressLine1,
    ranked_suppliers.AddressLine2,
    ranked_suppliers.AddressLine3,
    ranked_suppliers.AddressLine4,
    ranked_suppliers.PostalCode,
    ranked_suppliers.PhoneNumber,
    ranked_suppliers.EmailAddress,
    ranked_suppliers.GSTNumber
FROM (
    SELECT
        PurMas_Party AS SourceSupplierNo,
        LTRIM(RTRIM(ISNULL(PurMas_Add1, ''))) AS SupplierName,
        LTRIM(RTRIM(ISNULL(PurMas_Add2, ''))) AS AddressLine1,
        LTRIM(RTRIM(ISNULL(PurMas_Add3, ''))) AS AddressLine2,
        LTRIM(RTRIM(ISNULL(PurMas_Add4, ''))) AS AddressLine3,
        LTRIM(RTRIM(ISNULL(PurMas_Add5, ''))) AS AddressLine4,
        LTRIM(RTRIM(ISNULL(CONVERT(varchar(20), PurMas_Pincode), ''))) AS PostalCode,
        LTRIM(RTRIM(ISNULL(PurMas_Cell, ''))) AS PhoneNumber,
        LTRIM(RTRIM(ISNULL(PurMas_EMail, ''))) AS EmailAddress,
        LTRIM(RTRIM(ISNULL(PurMas_GST, ''))) AS GSTNumber,
        ROW_NUMBER() OVER (
            PARTITION BY PurMas_Party
            ORDER BY
                CASE WHEN LTRIM(RTRIM(ISNULL(PurMas_GST, ''))) <> '' THEN 0 ELSE 1 END,
                CASE WHEN LTRIM(RTRIM(ISNULL(PurMas_Cell, ''))) <> '' THEN 0 ELSE 1 END,
                ISNULL(PurMas_VouDate, PurMas_Date) DESC,
                PurMas_SNo DESC
        ) AS supplier_row_number
    FROM dbo.PurMas_Table
    WHERE ISNULL(PurMas_Cancel, 0) = 0
      AND LTRIM(RTRIM(ISNULL(PurMas_Add1, ''))) <> ''
      AND LTRIM(RTRIM(ISNULL(CONVERT(varchar(50), PurMas_Party), ''))) <> ''
) AS ranked_suppliers
WHERE ranked_suppliers.supplier_row_number = 1
ORDER BY ranked_suppliers.SupplierName, ranked_suppliers.SourceSupplierNo
"""


class SupplierSyncError(RuntimeError):
    pass


@dataclass
class SupplierSyncStats:
    fetched_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0


def normalize_source_supplier_no(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_supplier_lookup_key(value):
    normalized_value = normalize_text(value)
    if not normalized_value:
        return ""
    return normalized_value.casefold()


def build_supplier_address(row):
    address_parts = [
        normalize_text(getattr(row, "AddressLine1", "")),
        normalize_text(getattr(row, "AddressLine2", "")),
        normalize_text(getattr(row, "AddressLine3", "")),
        normalize_text(getattr(row, "AddressLine4", "")),
        normalize_text(getattr(row, "PostalCode", "")),
    ]
    return ", ".join(part for part in address_parts if part)


def build_supplier_sync_payload(row):
    source_supplier_no = normalize_source_supplier_no(
        getattr(row, "SourceSupplierNo", None)
    )
    supplier_name = normalize_text(getattr(row, "SupplierName", ""))
    if source_supplier_no is None and not supplier_name:
        return None

    return {
        "source_supplier_no": source_supplier_no,
        "name": supplier_name,
        "contact_person": supplier_name,
        "phone_number": normalize_text(getattr(row, "PhoneNumber", "")) or "Not Provided",
        "email": normalize_text(getattr(row, "EmailAddress", "")),
        "address": build_supplier_address(row),
        "gstin_number": normalize_text(getattr(row, "GSTNumber", "")),
        "status": SupplierStatus.ACTIVE,
    }


def update_name_mapping(suppliers_by_name, supplier, previous_name_key=""):
    if previous_name_key:
        mapped_supplier = suppliers_by_name.get(previous_name_key)
        if mapped_supplier is not None and mapped_supplier.pk == supplier.pk:
            del suppliers_by_name[previous_name_key]

    current_name_key = normalize_supplier_lookup_key(supplier.name)
    if current_name_key:
        suppliers_by_name[current_name_key] = supplier


def update_source_mapping(suppliers_by_source_no, supplier, previous_source_supplier_no=None):
    if previous_source_supplier_no is not None:
        mapped_supplier = suppliers_by_source_no.get(previous_source_supplier_no)
        if mapped_supplier is not None and mapped_supplier.pk == supplier.pk:
            del suppliers_by_source_no[previous_source_supplier_no]

    if supplier.source_supplier_no is not None:
        suppliers_by_source_no[supplier.source_supplier_no] = supplier


def sync_suppliers_from_rows(rows, user):
    stats = SupplierSyncStats()
    visible_suppliers = list(
        filter_queryset_by_role(Supplier.objects.all().order_by("pk"), user)
    )
    suppliers_by_source_no = {
        supplier.source_supplier_no: supplier
        for supplier in visible_suppliers
        if supplier.source_supplier_no is not None
    }
    suppliers_by_name = {}
    for supplier in visible_suppliers:
        supplier_name_key = normalize_supplier_lookup_key(supplier.name)
        if supplier_name_key and supplier_name_key not in suppliers_by_name:
            suppliers_by_name[supplier_name_key] = supplier

    for row in rows:
        stats.fetched_count += 1
        supplier_payload = build_supplier_sync_payload(row)
        if supplier_payload is None:
            stats.skipped_count += 1
            continue

        source_supplier_no = supplier_payload["source_supplier_no"]
        supplier_name_key = normalize_supplier_lookup_key(supplier_payload["name"])
        existing_supplier = None
        if source_supplier_no is not None:
            existing_supplier = suppliers_by_source_no.get(source_supplier_no)
        if existing_supplier is None and supplier_name_key:
            existing_supplier = suppliers_by_name.get(supplier_name_key)

        if existing_supplier is None:
            created_supplier = Supplier.objects.create(
                user=user,
                source_supplier_no=source_supplier_no,
                name=supplier_payload["name"],
                contact_person=supplier_payload["contact_person"],
                phone_number=supplier_payload["phone_number"],
                email=supplier_payload["email"],
                address=supplier_payload["address"],
                gstin_number=supplier_payload["gstin_number"],
                status=supplier_payload["status"],
            )
            update_source_mapping(suppliers_by_source_no, created_supplier)
            update_name_mapping(suppliers_by_name, created_supplier)
            stats.inserted_count += 1
            continue

        changed = False
        previous_name_key = normalize_supplier_lookup_key(existing_supplier.name)
        previous_name_value = normalize_text(existing_supplier.name)
        previous_source_supplier_no = existing_supplier.source_supplier_no

        if (
            source_supplier_no is not None
            and existing_supplier.source_supplier_no != source_supplier_no
        ):
            existing_supplier.source_supplier_no = source_supplier_no
            changed = True

        new_supplier_name = supplier_payload["name"]
        if new_supplier_name and normalize_supplier_lookup_key(new_supplier_name) != previous_name_key:
            conflicting_supplier = suppliers_by_name.get(
                normalize_supplier_lookup_key(new_supplier_name)
            )
            if conflicting_supplier is None or conflicting_supplier.pk == existing_supplier.pk:
                existing_supplier.name = new_supplier_name
                changed = True

        if supplier_payload["contact_person"]:
            current_contact_person = normalize_text(existing_supplier.contact_person)
            if (
                not current_contact_person
                or current_contact_person in {
                    previous_name_value,
                    normalize_text(existing_supplier.name),
                }
            ) and existing_supplier.contact_person != supplier_payload["contact_person"]:
                existing_supplier.contact_person = supplier_payload["contact_person"]
                changed = True

        new_phone_number = supplier_payload["phone_number"]
        if (
            new_phone_number
            and new_phone_number != existing_supplier.phone_number
            and (
                normalize_text(existing_supplier.phone_number) in {"", "Not Provided"}
                or new_phone_number != "Not Provided"
            )
        ):
            existing_supplier.phone_number = new_phone_number
            changed = True

        for field_name in ("email", "address", "gstin_number"):
            new_value = supplier_payload[field_name]
            if new_value and getattr(existing_supplier, field_name) != new_value:
                setattr(existing_supplier, field_name, new_value)
                changed = True

        if changed:
            existing_supplier.save()
            update_source_mapping(
                suppliers_by_source_no,
                existing_supplier,
                previous_source_supplier_no=previous_source_supplier_no,
            )
            update_name_mapping(
                suppliers_by_name,
                existing_supplier,
                previous_name_key=previous_name_key,
            )
            stats.updated_count += 1
        else:
            stats.skipped_count += 1

    return stats


def sync_suppliers_from_sqlserver(user, batch_size=1000):
    try:
        connection_string = build_sqlserver_connection_string()
    except Exception as exc:
        raise SupplierSyncError(str(exc)) from exc

    if pyodbc is None:
        raise SupplierSyncError(
            "pyodbc is not installed. Add pyodbc to the environment before syncing suppliers."
        )

    try:
        connection = pyodbc.connect(
            connection_string,
            timeout=10,
        )
    except Exception as exc:  # pragma: no cover - integration failure path.
        raise SupplierSyncError(f"Could not connect to SQL Server: {exc}") from exc

    stats = SupplierSyncStats()
    try:
        cursor = connection.cursor()
        cursor.execute(SUPPLIER_SYNC_SELECT)

        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break

            batch_stats = sync_suppliers_from_rows(rows, user)
            stats.fetched_count += batch_stats.fetched_count
            stats.inserted_count += batch_stats.inserted_count
            stats.updated_count += batch_stats.updated_count
            stats.skipped_count += batch_stats.skipped_count
    except Exception as exc:  # pragma: no cover - integration failure path.
        raise SupplierSyncError(f"Could not sync supplier data: {exc}") from exc
    finally:
        connection.close()

    return stats
