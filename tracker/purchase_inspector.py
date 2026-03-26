import re
from dataclasses import dataclass, field

from .sales_sync import (
    build_sqlserver_connection_string,
    format_sqlserver_date_literal,
    normalize_text,
    pyodbc,
)


DEFAULT_PAYMENT_SEARCH_KEYWORDS = (
    "pay",
    "paid",
    "balance",
    "bal",
    "due",
    "receipt",
    "recd",
    "recv",
    "settle",
)

PURMAS_PREVIEW_SELECT = """
SELECT TOP {limit}
    PurMas_SNo AS PurMas_SNo,
    LTRIM(RTRIM(ISNULL(CONVERT(varchar(50), PurMas_Party), ''))) AS PurMas_Party,
    LTRIM(RTRIM(ISNULL(PurMas_Add1, ''))) AS SupplierName,
    LTRIM(RTRIM(ISNULL(PurMas_BillNo, ''))) AS PurMas_BillNo,
    LTRIM(RTRIM(ISNULL(PurMas_VouNo, ''))) AS PurMas_VouNo,
    CAST(ISNULL(PurMas_VouDate, PurMas_Date) AS date) AS PurchaseDate,
    ISNULL(PurMas_NetAmt, 0) AS PurMas_NetAmt,
    LTRIM(RTRIM(ISNULL(PurMas_Remarks, ''))) AS PurMas_Remarks
FROM dbo.PurMas_Table
"""

QUALIFIED_TABLE_NAME_PATTERN = re.compile(
    r"^(?:(?P<schema>[A-Za-z0-9_]+)\.)?(?P<table>[A-Za-z0-9_]+)$"
)


class PurchaseSourceInspectorError(RuntimeError):
    pass


@dataclass
class PurchaseSourceInspectionResult:
    purchase_preview_rows: list[dict] = field(default_factory=list)
    payment_column_rows: list[dict] = field(default_factory=list)
    active_keywords: list[str] = field(default_factory=list)
    preview_table_name: str = ""
    preview_table_rows: list[dict] = field(default_factory=list)


def normalize_preview_limit(limit, maximum=200):
    try:
        normalized_limit = int(limit)
    except (TypeError, ValueError):
        normalized_limit = 25
    return max(1, min(normalized_limit, maximum))


def normalize_search_keywords(keywords=None):
    normalized_keywords = []
    seen_keywords = set()

    for raw_keyword in keywords or DEFAULT_PAYMENT_SEARCH_KEYWORDS:
        keyword = normalize_text(raw_keyword).casefold()
        if not keyword or keyword in seen_keywords:
            continue
        seen_keywords.add(keyword)
        normalized_keywords.append(keyword)

    if normalized_keywords:
        return normalized_keywords
    return list(DEFAULT_PAYMENT_SEARCH_KEYWORDS)


def normalize_date_range(date_from=None, date_to=None):
    if date_from and date_to and date_from > date_to:
        return date_to, date_from
    return date_from, date_to


def build_purmas_preview_query(
    limit=25,
    date_from=None,
    date_to=None,
    supplier_name="",
    party_reference="",
    bill_no="",
    voucher_no="",
):
    date_from, date_to = normalize_date_range(date_from=date_from, date_to=date_to)
    query_parts = [PURMAS_PREVIEW_SELECT.format(limit=normalize_preview_limit(limit))]
    query_parts.append("WHERE ISNULL(PurMas_Cancel, 0) = 0")
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

    supplier_name = normalize_text(supplier_name)
    if supplier_name:
        query_parts.append(
            "AND LTRIM(RTRIM(ISNULL(PurMas_Add1, ''))) LIKE ?"
        )
        parameters.append(f"%{supplier_name}%")

    party_reference = normalize_text(party_reference)
    if party_reference:
        query_parts.append(
            "AND LTRIM(RTRIM(ISNULL(CONVERT(varchar(50), PurMas_Party), ''))) LIKE ?"
        )
        parameters.append(f"%{party_reference}%")

    bill_no = normalize_text(bill_no)
    if bill_no:
        query_parts.append("AND LTRIM(RTRIM(ISNULL(PurMas_BillNo, ''))) LIKE ?")
        parameters.append(f"%{bill_no}%")

    voucher_no = normalize_text(voucher_no)
    if voucher_no:
        query_parts.append("AND LTRIM(RTRIM(ISNULL(PurMas_VouNo, ''))) LIKE ?")
        parameters.append(f"%{voucher_no}%")

    query_parts.append(
        "ORDER BY CAST(ISNULL(PurMas_VouDate, PurMas_Date) AS date) DESC, PurMas_SNo DESC"
    )
    return "\n".join(query_parts), parameters


def build_payment_column_search_query(keywords=None):
    normalized_keywords = normalize_search_keywords(keywords)
    where_clauses = []
    parameters = []

    for keyword in normalized_keywords:
        where_clauses.append("(LOWER(TABLE_NAME) LIKE ? OR LOWER(COLUMN_NAME) LIKE ?)")
        parameters.extend((f"%{keyword}%", f"%{keyword}%"))

    query = """
SELECT
    TABLE_SCHEMA,
    TABLE_NAME,
    COLUMN_NAME,
    DATA_TYPE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE {where_clause}
ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
""".strip().format(where_clause=" OR ".join(where_clauses))
    return query, parameters


def normalize_qualified_table_name(table_name):
    normalized_name = normalize_text(table_name)
    match = QUALIFIED_TABLE_NAME_PATTERN.fullmatch(normalized_name)
    if match is None:
        raise PurchaseSourceInspectorError(
            "preview table must be in table or schema.table format using only letters, numbers, and underscores."
        )

    schema_name = match.group("schema") or "dbo"
    base_table_name = match.group("table")
    qualified_table_name = f"[{schema_name}].[{base_table_name}]"
    return schema_name, base_table_name, qualified_table_name


def build_table_preview_query(table_name, limit=25):
    _schema_name, _base_table_name, qualified_table_name = (
        normalize_qualified_table_name(table_name)
    )
    return f"SELECT TOP {normalize_preview_limit(limit)} * FROM {qualified_table_name}"


def map_cursor_rows(cursor, rows):
    column_names = [description[0] for description in cursor.description or []]
    return [dict(zip(column_names, row)) for row in rows]


def inspect_purchase_sources_in_sqlserver(
    limit=25,
    date_from=None,
    date_to=None,
    supplier_name="",
    party_reference="",
    bill_no="",
    voucher_no="",
    keywords=None,
    preview_table="",
):
    try:
        connection_string = build_sqlserver_connection_string()
    except Exception as exc:
        raise PurchaseSourceInspectorError(str(exc)) from exc

    if pyodbc is None:
        raise PurchaseSourceInspectorError(
            "pyodbc is not installed. Add pyodbc to the environment before inspecting purchase sources."
        )

    purchase_query, purchase_parameters = build_purmas_preview_query(
        limit=limit,
        date_from=date_from,
        date_to=date_to,
        supplier_name=supplier_name,
        party_reference=party_reference,
        bill_no=bill_no,
        voucher_no=voucher_no,
    )
    payment_query, payment_parameters = build_payment_column_search_query(
        keywords=keywords
    )
    normalized_keywords = normalize_search_keywords(keywords)

    try:
        connection = pyodbc.connect(
            connection_string,
            timeout=10,
        )
    except Exception as exc:
        raise PurchaseSourceInspectorError(
            f"Could not connect to SQL Server: {exc}"
        ) from exc

    try:
        cursor = connection.cursor()

        cursor.execute(purchase_query, purchase_parameters)
        purchase_preview_rows = map_cursor_rows(cursor, cursor.fetchall())

        cursor.execute(payment_query, payment_parameters)
        payment_column_rows = map_cursor_rows(cursor, cursor.fetchall())

        preview_table_rows = []
        preview_table_name = ""
        if normalize_text(preview_table):
            preview_query = build_table_preview_query(preview_table, limit=limit)
            cursor.execute(preview_query)
            preview_table_rows = map_cursor_rows(cursor, cursor.fetchall())
            schema_name, base_table_name, _qualified_table_name = (
                normalize_qualified_table_name(preview_table)
            )
            preview_table_name = f"{schema_name}.{base_table_name}"
    except PurchaseSourceInspectorError:
        raise
    except Exception as exc:
        raise PurchaseSourceInspectorError(
            f"Could not inspect purchase sources: {exc}"
        ) from exc
    finally:
        connection.close()

    return PurchaseSourceInspectionResult(
        purchase_preview_rows=purchase_preview_rows,
        payment_column_rows=payment_column_rows,
        active_keywords=normalized_keywords,
        preview_table_name=preview_table_name,
        preview_table_rows=preview_table_rows,
    )
