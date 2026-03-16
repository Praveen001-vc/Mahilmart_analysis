from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
import json
import re
import textwrap
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.dateparse import parse_date
from django.views import View
from django.views.generic import CreateView, ListView, RedirectView, TemplateView, UpdateView
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from .forms import (
    DailyCashSettlementForm,
    ExpenseForm,
    IncomeForm,
    PurchaseForm,
    PurchasePaymentForm,
    SupplierForm,
    UserManagementForm,
)
from .models import (
    DailyCashSettlement,
    ExpenseRecord,
    IncomeRecord,
    PaymentMethod,
    PurchasePayment,
    PurchaseRecord,
    SalesLedgerRecord,
    SalesPaymentMode,
    Supplier,
)
from .sales_sync import SalesSyncError, sync_sales_from_sqlserver
from .user_roles import filter_queryset_by_role, get_user_role_label

User = get_user_model()

def _sum_amount(queryset):
    return queryset.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")


def _sum_settlement_income(queryset):
    return queryset.aggregate(total=Sum("actual_sales"))["total"] or Decimal("0.00")


def get_sales_cash_from_settlement(settlement):
    return settlement.actual_sales - settlement.gpay_settled


def get_cash_in_hand_amount(opening_balance, sales_ledger_cash, expense_amount):
    return opening_balance + sales_ledger_cash - expense_amount


def get_settlement_closing_balance(
    opening_balance,
    sales_ledger_cash,
    expense_amount,
    cash_settled,
):
    return get_cash_in_hand_amount(
        opening_balance,
        sales_ledger_cash,
        expense_amount,
    ) - cash_settled


def build_income_ledger_entries(user):
    entries = []

    for record in filter_queryset_by_role(IncomeRecord.objects.all(), user).order_by(
        "-transaction_date", "-created_at", "-pk"
    ):
        entries.append(
            {
                "transaction_date": record.transaction_date,
                "title": record.title,
                "source": record.source,
                "category": record.category,
                "payment_method": record.payment_method,
                "amount": record.amount,
                "entry_type": "manual",
                "sort_date": record.transaction_date,
                "sort_timestamp": record.created_at,
            }
        )

    for settlement in filter_queryset_by_role(
        DailyCashSettlement.objects.all(),
        user,
    ).order_by(
        "-settlement_date", "-updated_at", "-pk"
    ):
        settlement_sales_cash = get_sales_cash_from_settlement(settlement)
        entries.append(
            {
                "transaction_date": settlement.settlement_date,
                "title": "Daily Settlement Income",
                "source": (
                    f"Cash Rs. {settlement_sales_cash} | "
                    f"GPay Rs. {settlement.gpay_settled}"
                ),
                "category": "Daily Settlement",
                "payment_method": "Cash + UPI",
                "amount": settlement.actual_sales,
                "entry_type": "settlement",
                "sort_date": settlement.settlement_date,
                "sort_timestamp": settlement.updated_at,
            }
        )

    entries.sort(
        key=lambda item: (item["sort_date"], item["sort_timestamp"]),
        reverse=True,
    )
    return entries


def _shift_month(month_start, months_back):
    year = month_start.year
    month = month_start.month - months_back
    while month <= 0:
        year -= 1
        month += 12
    return date(year, month, 1)


def _next_month(month_start):
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1)
    return date(month_start.year, month_start.month + 1, 1)


def build_monthly_overview(user, months=6, anchor_date=None):
    anchor_date = anchor_date or date.today()
    month_anchor = anchor_date.replace(day=1)
    rows = []

    for months_back in range(months - 1, -1, -1):
        month_start = _shift_month(month_anchor, months_back)
        month_end = _next_month(month_start)
        if month_start.year == anchor_date.year and month_start.month == anchor_date.month:
            month_end = anchor_date + timedelta(days=1)

        income_total = _sum_amount(
            filter_queryset_by_role(IncomeRecord.objects.all(), user).filter(
                transaction_date__gte=month_start,
                transaction_date__lt=month_end,
            )
        )
        expense_total = _sum_amount(
            filter_queryset_by_role(ExpenseRecord.objects.all(), user).filter(
                transaction_date__gte=month_start,
                transaction_date__lt=month_end,
            )
        )
        rows.append(
            {
                "label": month_start.strftime("%b %Y"),
                "income": income_total,
                "expense": expense_total,
                "balance": income_total - expense_total,
            }
        )

    highest_total = max(
        [max(row["income"], row["expense"]) for row in rows],
        default=Decimal("1.00"),
    )
    if highest_total == 0:
        highest_total = Decimal("1.00")

    for row in rows:
        row["income_width"] = round((row["income"] / highest_total) * 100, 2)
        row["expense_width"] = round((row["expense"] / highest_total) * 100, 2)

    return rows


def get_next_supplier_code():
    last_supplier = Supplier.objects.order_by("-pk").first()
    next_number = 1 if last_supplier is None else last_supplier.pk + 1
    return f"SUP-{next_number:04d}"


def build_page_url(request, page_number):
    query_params = request.GET.copy()
    query_params["page"] = page_number
    return f"{request.path}?{query_params.urlencode()}"


def get_selected_date(request, parameter_name="as_of_date"):
    selected_date = parse_date((request.GET.get(parameter_name) or "").strip())
    return selected_date or date.today()


def get_purchase_base_queryset(user):
    queryset = filter_queryset_by_role(PurchaseRecord.objects.all(), user)
    return (
        queryset.filter(Q(source_reference__startswith="MANUAL:") | Q(source_reference=""))
        .select_related("supplier", "user")
    )


def get_raw_purchase_filter_values(request):
    return {
        "supplier_name": (request.GET.get("supplier_name") or "").strip(),
        "invoice_number": (request.GET.get("invoice_number") or "").strip(),
        "saved_by": (request.GET.get("saved_by") or "").strip(),
        "pending_amount": (request.GET.get("pending_amount") or "").strip(),
        "date_from": (request.GET.get("date_from") or "").strip(),
        "date_to": (request.GET.get("date_to") or "").strip(),
    }


def get_purchase_filter_values(request):
    filter_values = get_raw_purchase_filter_values(request)
    if not any(filter_values.values()):
        today_value = date.today().isoformat()
        filter_values["date_from"] = today_value
        filter_values["date_to"] = today_value
    return filter_values


def apply_purchase_filters(queryset, filter_values):
    supplier_name = filter_values["supplier_name"]
    invoice_number = filter_values["invoice_number"]
    saved_by = filter_values["saved_by"]
    pending_amount = filter_values["pending_amount"]
    date_from = parse_date(filter_values["date_from"])
    date_to = parse_date(filter_values["date_to"])

    if supplier_name:
        queryset = queryset.filter(supplier_name__icontains=supplier_name)
    if invoice_number:
        queryset = queryset.filter(invoice_number__icontains=invoice_number)
    if saved_by:
        queryset = queryset.filter(user__username__icontains=saved_by)
    if pending_amount:
        try:
            queryset = queryset.filter(pending_amount=Decimal(pending_amount))
        except InvalidOperation:
            pass
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from
    if date_from:
        queryset = queryset.filter(transaction_date__gte=date_from)
    if date_to:
        queryset = queryset.filter(transaction_date__lte=date_to)
    return queryset


def get_raw_sales_filter_values(params):
    return {
        "bill_no": (params.get("bill_no") or "").strip(),
        "customer_name": (params.get("customer_name") or "").strip(),
        "payment_mode": (params.get("payment_mode") or "").strip(),
        "date_from": (params.get("date_from") or "").strip(),
        "date_to": (params.get("date_to") or "").strip(),
    }


def get_sales_filter_values(params):
    filter_values = get_raw_sales_filter_values(params)
    if not filter_values["date_from"] and not filter_values["date_to"]:
        today_value = date.today().isoformat()
        filter_values["date_from"] = today_value
        filter_values["date_to"] = today_value
    elif filter_values["date_from"] and not filter_values["date_to"]:
        filter_values["date_to"] = filter_values["date_from"]
    elif filter_values["date_to"] and not filter_values["date_from"]:
        filter_values["date_from"] = filter_values["date_to"]
    return filter_values


def apply_sales_filters(queryset, filter_values):
    bill_no = filter_values["bill_no"]
    customer_name = filter_values["customer_name"]
    payment_mode = filter_values["payment_mode"]
    date_from = parse_date(filter_values["date_from"])
    date_to = parse_date(filter_values["date_to"])

    if bill_no:
        queryset = queryset.filter(bill_no__icontains=bill_no)
    if customer_name:
        queryset = queryset.filter(customer_name__icontains=customer_name)
    if payment_mode and payment_mode != "All":
        queryset = queryset.filter(payment_mode=payment_mode)
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from
    if date_from:
        queryset = queryset.filter(sale_date__gte=date_from)
    if date_to:
        queryset = queryset.filter(sale_date__lte=date_to)
    return queryset


def get_sales_redirect_params(params):
    redirect_params = {}
    for key in ("bill_no", "customer_name", "payment_mode", "date_from", "date_to"):
        value = (params.get(key) or "").strip()
        if value:
            redirect_params[key] = value
    return redirect_params


def build_sales_redirect_url(params):
    redirect_params = get_sales_redirect_params(params)
    if not redirect_params:
        return reverse("sales-list")
    return f"{reverse('sales-list')}?{urlencode(redirect_params)}"


DEFAULT_EXPENSE_CATEGORIES = (
    "Transport",
    "Utilities",
    "Salary",
    "Rent",
    "Purchase",
    "Maintenance",
    "Delivery",
    "Fuel",
    "General",
)


def get_raw_expense_filter_values(request):
    return {
        "start_date": (request.GET.get("start_date") or "").strip(),
        "end_date": (request.GET.get("end_date") or "").strip(),
        "category": (request.GET.get("category") or "").strip(),
        "payment_method": (request.GET.get("payment_method") or "").strip(),
    }


def get_expense_filter_values(request):
    filter_values = get_raw_expense_filter_values(request)
    if not any(filter_values.values()):
        today_value = date.today().isoformat()
        filter_values["start_date"] = today_value
        filter_values["end_date"] = today_value
        return filter_values

    if filter_values["start_date"] and not filter_values["end_date"]:
        filter_values["end_date"] = filter_values["start_date"]
    if filter_values["end_date"] and not filter_values["start_date"]:
        filter_values["start_date"] = filter_values["end_date"]
    return filter_values


def apply_expense_filters(queryset, filter_values):
    start_date = parse_date(filter_values["start_date"])
    end_date = parse_date(filter_values["end_date"])
    category = filter_values["category"]
    payment_method = filter_values["payment_method"]

    if start_date and end_date and start_date > end_date:
        start_date, end_date = end_date, start_date
    if start_date:
        queryset = queryset.filter(transaction_date__gte=start_date)
    if end_date:
        queryset = queryset.filter(transaction_date__lte=end_date)
    if category and category != "All":
        queryset = queryset.filter(category__iexact=category)
    if payment_method and payment_method != "All":
        queryset = queryset.filter(payment_method=payment_method)
    return queryset


def get_expense_category_options(user):
    categories = list(
        filter_queryset_by_role(ExpenseRecord.objects.all(), user)
        .exclude(category="")
        .order_by("category")
        .values_list("category", flat=True)
        .distinct()
    )
    ordered_categories = []
    seen = set()
    for category_name in [*DEFAULT_EXPENSE_CATEGORIES, *categories]:
        normalized = (category_name or "").strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered_categories.append(normalized)
    return ordered_categories


def get_expense_redirect_params(params):
    redirect_params = {}
    for key in ("start_date", "end_date", "category", "payment_method"):
        value = (params.get(key) or "").strip()
        if value:
            redirect_params[key] = value
    return redirect_params


def build_expense_redirect_url(params):
    redirect_params = get_expense_redirect_params(params)
    if not redirect_params:
        return reverse("expense-list")
    return f"{reverse('expense-list')}?{urlencode(redirect_params)}"


def parse_money_value(value):
    if isinstance(value, Decimal):
        return value
    if value in (None, ""):
        return Decimal("0.00")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


CASH_DENOMINATION_VALUES = (2000, 500, 200, 100, 50, 20, 10, 5, 2, 1)


def normalize_count_value(value):
    try:
        count = int(str(value or "0").strip())
    except (TypeError, ValueError):
        return 0
    return max(count, 0)


def parse_cash_denominations_payload(raw_payload):
    if not raw_payload:
        return {}
    try:
        parsed_payload = json.loads(raw_payload)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed_payload, dict):
        return {}

    normalized = {}
    for denomination in CASH_DENOMINATION_VALUES:
        count = normalize_count_value(parsed_payload.get(str(denomination)))
        if count > 0:
            normalized[str(denomination)] = count
    return normalized


def build_cash_denominations_payload(denominations):
    normalized = {
        str(denomination): normalize_count_value(denominations.get(str(denomination)))
        for denomination in CASH_DENOMINATION_VALUES
        if normalize_count_value(denominations.get(str(denomination))) > 0
    }
    return json.dumps(normalized) if normalized else ""


def get_cash_denominations_total(denominations):
    total = Decimal("0.00")
    for denomination in CASH_DENOMINATION_VALUES:
        count = normalize_count_value(denominations.get(str(denomination)))
        if count:
            total += Decimal(str(denomination)) * Decimal(str(count))
    return total


def get_effective_sales_payment_amount(record):
    if record.received_amount > 0:
        return min(record.net_amount, record.received_amount)
    return max(record.net_amount, Decimal("0.00"))


def build_sales_settlement_summary(settlement_date):
    sales_queryset = SalesLedgerRecord.objects.filter(
        is_cancelled=False,
        sale_date=settlement_date,
    ).order_by("source_sale_no")
    sales_count = sales_queryset.count()

    if not sales_count:
        return {
            "gpay_settled": Decimal("0.00"),
            "cash_settled": Decimal("0.00"),
            "sales_count": 0,
            "manual_split_count": 0,
        }

    gpay_total = Decimal("0.00")
    cash_total = Decimal("0.00")
    manual_split_count = 0

    for record in sales_queryset:
        if record.has_manual_split:
            manual_split_count += 1
            cash_total += record.split_cash_amount
            gpay_total += record.split_card_amount
            continue

        effective_amount = get_effective_sales_payment_amount(record)
        if record.payment_mode == SalesPaymentMode.CARD:
            gpay_total += effective_amount
        elif record.payment_mode == SalesPaymentMode.CASH:
            cash_total += effective_amount

    return {
        "gpay_settled": gpay_total,
        "cash_settled": cash_total,
        "sales_count": sales_count,
        "manual_split_count": manual_split_count,
    }


def get_settlement_filter_values(params):
    start_date = parse_date((params.get("start_date") or "").strip()) or date.today()
    end_date = parse_date((params.get("end_date") or "").strip()) or start_date
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    return {
        "start_date": start_date,
        "end_date": end_date,
    }


def get_settlement_entry_date(params, default_date):
    entry_date = parse_date(
        (params.get("entry_date") or params.get("settlement_date") or "").strip()
    )
    return entry_date or default_date


def get_default_settlement_opening_balance(user, settlement_date):
    previous_settlement = (
        filter_queryset_by_role(DailyCashSettlement.objects.all(), user).filter(
            settlement_date__lt=settlement_date,
        )
        .order_by("-settlement_date", "-updated_at", "-pk")
        .first()
    )
    if previous_settlement:
        return previous_settlement.closing_balance

    income_total = _sum_amount(
        filter_queryset_by_role(IncomeRecord.objects.all(), user).filter(
            transaction_date__lt=settlement_date,
        )
    )
    expense_total = _sum_amount(
        filter_queryset_by_role(ExpenseRecord.objects.all(), user).filter(
            transaction_date__lt=settlement_date,
        )
    )
    return income_total - expense_total


def build_settlement_autofill_summary(user, settlement_date):
    previous_settlement = (
        filter_queryset_by_role(DailyCashSettlement.objects.all(), user).filter(
            settlement_date__lt=settlement_date,
        )
        .order_by("-settlement_date", "-updated_at", "-pk")
        .first()
    )
    sales_summary = build_sales_settlement_summary(settlement_date)
    expense_queryset = filter_queryset_by_role(ExpenseRecord.objects.all(), user).filter(
        transaction_date=settlement_date,
    )
    if previous_settlement:
        opening_balance = previous_settlement.closing_balance
    else:
        opening_balance = get_default_settlement_opening_balance(user, settlement_date)

    sales_ledger_cash = sales_summary["cash_settled"]
    if sales_summary["sales_count"] > 0:
        gpay_settled = sales_summary["gpay_settled"]
        settlement_source = "sales"
    else:
        gpay_settled = Decimal("0.00")
        settlement_source = "manual"

    expense_amount = _sum_amount(expense_queryset)
    cash_in_hand = get_cash_in_hand_amount(
        opening_balance,
        sales_ledger_cash,
        expense_amount,
    )

    return {
        "opening_balance": opening_balance,
        "sales_ledger_cash": sales_ledger_cash,
        "gpay_settled": gpay_settled,
        "cash_settled": Decimal("0.00"),
        "cash_in_hand": cash_in_hand,
        "expense_amount": expense_amount,
        "upi_count": 0,
        "cash_count": 0,
        "expense_count": expense_queryset.count(),
        "sales_count": sales_summary["sales_count"],
        "manual_split_count": sales_summary["manual_split_count"],
        "settlement_source": settlement_source,
        "previous_settlement_date": (
            previous_settlement.settlement_date if previous_settlement else None
        ),
        "previous_closing_balance": (
            previous_settlement.closing_balance if previous_settlement else opening_balance
        ),
    }


def build_settlement_preview(values):
    opening_balance = parse_money_value(values.get("opening_balance"))
    sales_ledger_cash = parse_money_value(values.get("sales_ledger_cash"))
    gpay_settled = parse_money_value(values.get("gpay_settled"))
    cash_settled = parse_money_value(values.get("cash_settled"))
    expense_amount = parse_money_value(values.get("expense_amount"))
    closing_balance = get_settlement_closing_balance(
        opening_balance,
        sales_ledger_cash,
        expense_amount,
        cash_settled,
    )
    cash_in_hand = get_cash_in_hand_amount(
        opening_balance,
        sales_ledger_cash,
        expense_amount,
    )
    total_amount = gpay_settled + cash_settled + expense_amount + closing_balance
    actual_sales = total_amount - opening_balance
    return {
        "opening_balance": opening_balance,
        "sales_ledger_cash": sales_ledger_cash,
        "gpay_settled": gpay_settled,
        "cash_settled": cash_settled,
        "cash_in_hand": cash_in_hand,
        "expense_amount": expense_amount,
        "closing_balance": closing_balance,
        "total_amount": total_amount,
        "actual_sales": actual_sales,
    }


def build_invoice_filename(invoice_number):
    safe_invoice = re.sub(r"[^A-Za-z0-9_-]+", "_", invoice_number or "").strip("_")
    if not safe_invoice:
        safe_invoice = "purchase"
    return f"{safe_invoice}_invoice.pdf"


def build_purchase_invoice_response(purchase):
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, pageCompression=0)
    page_width, page_height = A4
    saved_on = purchase.created_at.strftime("%d-%m-%Y %I:%M %p") if purchase.created_at else "-"
    payment_history = build_purchase_payment_history(purchase)
    pdf.setTitle(f"Purchase Invoice - {purchase.invoice_number}")

    premium_navy = colors.HexColor("#12263A")
    premium_gold = colors.HexColor("#D5B26F")
    premium_cream = colors.HexColor("#F7F1E3")
    panel_fill = colors.HexColor("#F9F7F2")
    accent_fill = colors.HexColor("#E8D8B5")
    text_dark = colors.HexColor("#22313F")
    muted_text = colors.HexColor("#667786")
    line_color = colors.HexColor("#D9C7A0")

    def draw_label_value(x_position, y_position, label, value):
        pdf.setFillColor(muted_text)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(x_position, y_position, label.upper())
        pdf.setFillColor(text_dark)
        pdf.setFont("Helvetica", 11)
        pdf.drawString(x_position, y_position - 14, str(value))

    def draw_amount_card(x_position, y_position, width, title, amount, fill_color):
        pdf.setFillColor(fill_color)
        pdf.roundRect(x_position, y_position, width, 62, 10, fill=1, stroke=0)
        pdf.setFillColor(text_dark)
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(x_position + 14, y_position + 42, title)
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(x_position + 14, y_position + 20, f"Rs. {amount}")

    def draw_footer(message):
        pdf.setStrokeColor(premium_gold)
        pdf.line(32, 60, page_width - 32, 60)
        pdf.setFillColor(muted_text)
        pdf.setFont("Helvetica-Oblique", 9)
        pdf.drawString(32, 44, message)

    def draw_payment_history_page_header():
        pdf.showPage()
        pdf.setFillColor(premium_navy)
        pdf.roundRect(32, page_height - 126, page_width - 64, 86, 18, fill=1, stroke=0)
        pdf.setFillColor(premium_cream)
        pdf.setFont("Helvetica-Bold", 20)
        pdf.drawString(52, page_height - 78, "Payment Tracking")
        pdf.setFont("Helvetica", 10)
        pdf.drawString(52, page_height - 98, "Full invoice payment register with date, time, amount, and staff entry.")
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawRightString(page_width - 52, page_height - 76, f"Invoice No: {purchase.invoice_number}")
        pdf.setFillColor(text_dark)
        pdf.setFont("Helvetica", 10)
        pdf.drawString(52, page_height - 152, f"Supplier: {purchase.supplier_name}")
        pdf.drawString(52, page_height - 170, f"Purchase Date: {purchase.transaction_date or '-'}")
        pdf.drawString(280, page_height - 152, f"Total Amount: Rs. {purchase.total_amount}")
        pdf.drawString(280, page_height - 170, f"Pending Amount: Rs. {purchase.pending_amount}")
        pdf.setStrokeColor(line_color)
        pdf.line(32, page_height - 190, page_width - 32, page_height - 190)
        pdf.setFillColor(muted_text)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(40, page_height - 210, "DATE")
        pdf.drawString(102, page_height - 210, "TIME")
        pdf.drawString(160, page_height - 210, "ENTRY")
        pdf.drawString(296, page_height - 210, "AMOUNT")
        pdf.drawString(370, page_height - 210, "STAFF")
        pdf.drawRightString(page_width - 40, page_height - 210, "BALANCE")
        pdf.setStrokeColor(line_color)
        pdf.line(32, page_height - 218, page_width - 32, page_height - 218)
        return page_height - 238

    pdf.setFillColor(premium_navy)
    pdf.roundRect(32, page_height - 142, page_width - 64, 102, 18, fill=1, stroke=0)
    pdf.setFillColor(premium_gold)
    pdf.circle(72, page_height - 91, 18, fill=1, stroke=0)
    pdf.setFillColor(premium_navy)
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawCentredString(72, page_height - 96, "M")
    pdf.setFillColor(premium_cream)
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(104, page_height - 78, "Mahilmart Purchase Invoice")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(104, page_height - 98, "Official supplier purchase invoice for Mahilmart")
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawRightString(page_width - 52, page_height - 72, f"Invoice No: {purchase.invoice_number}")
    pdf.setFont("Helvetica", 10)
    pdf.drawRightString(page_width - 52, page_height - 92, f"Saved On: {saved_on}")

    pdf.setFillColor(panel_fill)
    pdf.roundRect(32, page_height - 332, page_width - 64, 160, 16, fill=1, stroke=0)
    pdf.setStrokeColor(line_color)
    pdf.roundRect(32, page_height - 332, page_width - 64, 160, 16, fill=0, stroke=1)
    pdf.setFillColor(text_dark)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(52, page_height - 196, "Purchase Details")

    draw_label_value(52, page_height - 220, "Supplier Name", purchase.supplier_name)
    draw_label_value(280, page_height - 220, "Type", purchase.purchase_type)
    draw_label_value(52, page_height - 262, "Invoice Number", purchase.invoice_number)
    draw_label_value(280, page_height - 262, "Prepared By", purchase.user.username)
    draw_label_value(52, page_height - 304, "Purchase Date", purchase.transaction_date or "-")
    draw_label_value(280, page_height - 304, "Saved On", saved_on)
    draw_label_value(52, page_height - 346, "Supplier Code", purchase.supplier.supplier_code if purchase.supplier_id else "-")
    draw_label_value(280, page_height - 346, "Uploaded File", "Attached" if purchase.attachment else "No Attachment")

    pdf.setFillColor(text_dark)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(32, page_height - 402, "Amount Summary")
    draw_amount_card(32, page_height - 482, 165, "Total Amount", purchase.total_amount, accent_fill)
    draw_amount_card(214, page_height - 482, 165, "Paid Amount", purchase.paid_amount, colors.HexColor("#D9F0DF"))
    draw_amount_card(396, page_height - 482, 165, "Pending Amount", purchase.pending_amount, colors.HexColor("#F7D6CF"))

    pdf.setFillColor(panel_fill)
    pdf.roundRect(32, page_height - 624, page_width - 64, 108, 16, fill=1, stroke=0)
    pdf.setStrokeColor(line_color)
    pdf.roundRect(32, page_height - 624, page_width - 64, 108, 16, fill=0, stroke=1)
    pdf.setFillColor(text_dark)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(52, page_height - 544, "Notes")
    pdf.setFont("Helvetica", 10)
    notes = purchase.notes.strip() or "No notes added for this purchase."
    note_y = page_height - 564
    for line in textwrap.wrap(notes, width=92):
        pdf.drawString(52, note_y, line)
        note_y -= 14
        if note_y < page_height - 608:
            break

    signature_y = 100
    pdf.setStrokeColor(line_color)
    pdf.line(60, signature_y, 230, signature_y)
    pdf.line(340, signature_y, 510, signature_y)
    pdf.setFillColor(text_dark)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(60, signature_y - 16, "Supplier Signature")
    pdf.drawString(340, signature_y - 16, "Authorized Sign")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(340, signature_y - 32, f"Prepared By: {purchase.user.username}")

    draw_footer("This invoice was generated from the Mahilmart purchase module.")

    detail_y = draw_payment_history_page_header()
    for entry in payment_history:
        note_lines = textwrap.wrap(
            entry["notes"] or "No note added for this entry.",
            width=50,
        )[:2]
        row_height = 32 + (len(note_lines) * 11)

        if detail_y - row_height < 92:
            draw_footer("Payment tracking continued from the Mahilmart purchase module.")
            detail_y = draw_payment_history_page_header()

        pdf.setFillColor(text_dark)
        pdf.setFont("Helvetica", 9)
        pdf.drawString(40, detail_y, entry["payment_date"])
        pdf.drawString(102, detail_y, entry["recorded_time"])
        pdf.drawString(160, detail_y, entry["label"])
        pdf.drawString(296, detail_y, f"Rs. {entry['amount']}")
        pdf.drawString(370, detail_y, entry["recorded_by"][:18])
        pdf.drawRightString(page_width - 40, detail_y, f"Rs. {entry['running_pending']}")

        pdf.setFillColor(muted_text)
        pdf.setFont("Helvetica", 8.5)
        pdf.drawString(160, detail_y - 13, f"Paid Till This Entry: Rs. {entry['running_paid']}")

        note_y = detail_y - 26
        for line in note_lines:
            pdf.drawString(160, note_y, line)
            note_y -= 11

        pdf.setStrokeColor(line_color)
        pdf.line(32, detail_y - row_height, page_width - 32, detail_y - row_height)
        detail_y -= row_height + 12

    draw_footer("This invoice includes date-wise payment history recorded by Mahilmart staff.")
    pdf.save()

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="{build_invoice_filename(purchase.invoice_number)}"'
    )
    return response


def format_money(amount):
    return f"{amount:.2f}"


def sync_purchase_to_expense(purchase):
    extra_notes = purchase.notes.strip()
    notes = [
        "Auto-created from purchase entry",
        f"Invoice No: {purchase.invoice_number}",
        f"Purchase Type: {purchase.purchase_type}",
        f"Paid Amount: {purchase.paid_amount}",
        f"Pending Amount: {purchase.pending_amount}",
    ]
    if extra_notes:
        notes.append(f"Purchase Notes: {extra_notes}")

    ExpenseRecord.objects.update_or_create(
        user=purchase.user,
        source_reference=f"PURCHASE:{purchase.source_reference}",
        defaults={
            "title": f"Purchase - {purchase.invoice_number}",
            "supplier": purchase.supplier,
            "vendor": purchase.supplier_name,
            "category": "Purchase",
            "amount": purchase.total_amount,
            "transaction_date": purchase.transaction_date,
            "payment_method": PaymentMethod.OTHER,
            "notes": " | ".join(notes),
        },
    )


def create_purchase_payment(
    purchase,
    user,
    amount,
    notes="",
    payment_date=None,
    update_totals=True,
):
    payment = PurchasePayment.objects.create(
        purchase=purchase,
        user=user,
        amount=amount,
        payment_date=payment_date or date.today(),
        notes=notes.strip(),
    )
    if update_totals:
        purchase.paid_amount += amount
        purchase.save(update_fields=["paid_amount", "pending_amount", "updated_at"])
    return payment


def build_purchase_payment_history(purchase):
    history = []
    tracked_total = Decimal("0.00")
    running_paid = Decimal("0.00")
    purchase_date = purchase.transaction_date or purchase.created_at.date()

    history.append(
        (
            purchase_date,
            purchase.created_at,
            {
                "entry_type": "purchase",
                "label": "Purchase Created",
                "amount": format_money(purchase.total_amount),
                "payment_date": purchase_date.strftime("%d-%m-%Y"),
                "recorded_on": purchase.created_at.strftime("%d-%m-%Y %I:%M %p"),
                "recorded_time": purchase.created_at.strftime("%I:%M %p"),
                "recorded_by": purchase.user.username,
                "notes": purchase.notes.strip() or "Purchase record created.",
                "running_paid": format_money(running_paid),
                "running_pending": format_money(purchase.total_amount - running_paid),
            },
        )
    )

    for payment in purchase.payments.select_related("user").order_by(
        "payment_date",
        "created_at",
        "pk",
    ):
        tracked_total += payment.amount
        running_paid += payment.amount
        history.append(
            (
                payment.payment_date,
                payment.created_at,
                {
                    "entry_type": "payment",
                    "label": "Payment Entry",
                    "amount": format_money(payment.amount),
                    "payment_date": payment.payment_date.strftime("%d-%m-%Y"),
                    "recorded_on": payment.created_at.strftime("%d-%m-%Y %I:%M %p"),
                    "recorded_time": payment.created_at.strftime("%I:%M %p"),
                    "recorded_by": payment.user.username,
                    "notes": payment.notes.strip(),
                    "running_paid": format_money(running_paid),
                    "running_pending": format_money(purchase.total_amount - running_paid),
                },
            )
        )

    legacy_balance = purchase.paid_amount - tracked_total
    if legacy_balance > 0:
        running_paid += legacy_balance
        history.append(
            (
                purchase_date,
                purchase.created_at,
                {
                    "entry_type": "opening",
                    "label": "Opening Paid Amount",
                    "amount": format_money(legacy_balance),
                    "payment_date": purchase_date.strftime("%d-%m-%Y"),
                    "recorded_on": purchase.created_at.strftime("%d-%m-%Y %I:%M %p"),
                    "recorded_time": purchase.created_at.strftime("%I:%M %p"),
                    "recorded_by": purchase.user.username,
                    "notes": "Saved with the original purchase record.",
                    "running_paid": format_money(running_paid),
                    "running_pending": format_money(purchase.total_amount - running_paid),
                },
            )
        )

    history.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in history]


def build_purchase_detail_payload(purchase):
    payment_history = build_purchase_payment_history(purchase)
    payment_entries = [
        entry for entry in payment_history if entry["entry_type"] in {"payment", "opening"}
    ]
    return {
        "supplier_name": purchase.supplier_name,
        "purchase_type": purchase.purchase_type,
        "invoice_number": purchase.invoice_number,
        "saved_by": purchase.user.username,
        "saved_on": purchase.created_at.strftime("%d-%m-%Y %I:%M %p"),
        "purchase_date": purchase.transaction_date.strftime("%d-%m-%Y") if purchase.transaction_date else "-",
        "total_amount": format_money(purchase.total_amount),
        "paid_amount": format_money(purchase.paid_amount),
        "pending_amount": format_money(purchase.pending_amount),
        "last_payment_date": payment_entries[-1]["payment_date"] if payment_entries else "-",
        "invoice_url": reverse("purchase-invoice", args=[purchase.pk]),
        "attachment_url": purchase.attachment.url if purchase.attachment else "",
        "pay_url": reverse("purchase-pay", args=[purchase.pk]),
        "can_pay": purchase.pending_amount > 0,
        "payment_history": payment_history,
    }


class HomeRedirectView(RedirectView):
    pattern_name = "dashboard"

    def get_redirect_url(self, *args, **kwargs):
        if self.request.user.is_authenticated:
            return reverse_lazy("dashboard")
        return reverse_lazy("login")


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "tracker/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        selected_date = get_selected_date(self.request)
        previous_date = selected_date - timedelta(days=1)
        income_queryset = filter_queryset_by_role(IncomeRecord.objects.all(), user)
        expense_queryset = filter_queryset_by_role(
            ExpenseRecord.objects.all(),
            user,
        ).select_related("supplier")
        settlement_queryset = filter_queryset_by_role(
            DailyCashSettlement.objects.all(),
            user,
        )
        settlement_record = settlement_queryset.filter(settlement_date=selected_date).first()
        income_total = _sum_settlement_income(
            settlement_queryset.filter(settlement_date__lte=selected_date)
        )
        expense_total = _sum_amount(expense_queryset.filter(transaction_date__lte=selected_date))
        opening_balance = get_default_settlement_opening_balance(user, selected_date)
        selected_income = _sum_settlement_income(
            settlement_queryset.filter(settlement_date=selected_date)
        )
        selected_expense = _sum_amount(expense_queryset.filter(transaction_date=selected_date))
        closing_balance = Decimal("0.00")
        if settlement_record:
            opening_balance = settlement_record.opening_balance
            closing_balance = settlement_record.closing_balance
        current_month_income = _sum_settlement_income(
            settlement_queryset.filter(
                settlement_date__year=selected_date.year,
                settlement_date__month=selected_date.month,
                settlement_date__lte=selected_date,
            )
        )
        current_month_expense = _sum_amount(
            expense_queryset.filter(
                transaction_date__year=selected_date.year,
                transaction_date__month=selected_date.month,
                transaction_date__lte=selected_date,
            )
        )
        selected_day_balance = Decimal("0.00")
        if settlement_record:
            selected_day_balance = settlement_record.actual_sales
        balance = income_total - expense_total
        savings_rate = 0
        if income_total:
            savings_rate = round(float((balance / income_total) * 100), 1)
        savings_rate_meter = min(max(savings_rate, 0), 100)

        context.update(
            {
                "income_total": income_total,
                "expense_total": expense_total,
                "balance": balance,
                "opening_balance": opening_balance,
                "closing_balance": closing_balance,
                "dashboard_settlement": settlement_record,
                "selected_income": selected_income,
                "selected_expense": selected_expense,
                "selected_day_balance": selected_day_balance,
                "selected_date": selected_date,
                "previous_date": previous_date,
                "current_month_income": current_month_income,
                "current_month_expense": current_month_expense,
                "savings_rate": savings_rate,
                "savings_rate_meter": savings_rate_meter,
                "recent_income": income_queryset.filter(transaction_date__lte=selected_date)[:5],
                "recent_expenses": expense_queryset.filter(transaction_date__lte=selected_date)[:5],
                "monthly_overview": build_monthly_overview(user, anchor_date=selected_date),
                "supplier_count": filter_queryset_by_role(Supplier.objects.all(), user).count(),
            }
        )
        return context


class AutoLoadPaginatedListView(LoginRequiredMixin, ListView):
    paginate_by = 50

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page_obj = context.get("page_obj")
        context["next_page_url"] = ""
        context["visible_count"] = len(context.get(self.context_object_name, []))
        if page_obj and page_obj.has_next():
            context["next_page_url"] = build_page_url(
                self.request, page_obj.next_page_number()
            )
        return context


class AdminRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.is_superuser:
            messages.error(request, "Only admin users can manage users.")
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)


class IncomeListView(AutoLoadPaginatedListView):
    model = IncomeRecord
    template_name = "tracker/income_list.html"
    context_object_name = "records"

    def get_queryset(self):
        return build_income_ledger_entries(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        records = self.get_queryset()
        context["page_total"] = sum(
            (record["amount"] for record in records),
            Decimal("0.00"),
        )
        context["page_count"] = len(records)
        return context


class IncomeCreateView(LoginRequiredMixin, CreateView):
    model = IncomeRecord
    form_class = IncomeForm
    template_name = "tracker/income_form.html"
    success_url = reverse_lazy("income-list")

    def form_valid(self, form):
        form.instance.user = self.request.user
        messages.success(self.request, "Income record created successfully.")
        return super().form_valid(form)


class ExpenseListView(AutoLoadPaginatedListView):
    model = ExpenseRecord
    template_name = "tracker/expense_list.html"
    context_object_name = "records"

    def get_base_queryset(self):
        return (
            filter_queryset_by_role(ExpenseRecord.objects.all(), self.request.user)
            .select_related("supplier")
            .order_by("-transaction_date", "-created_at", "-pk")
        )

    def get_queryset(self):
        return apply_expense_filters(
            self.get_base_queryset(),
            get_expense_filter_values(self.request),
        )

    def get_edit_record(self):
        record_id = (self.request.GET.get("edit") or "").strip()
        if not record_id.isdigit():
            return None
        return self.get_base_queryset().filter(pk=record_id).first()

    def build_entry_rows(self):
        edit_record = self.get_edit_record()
        if edit_record:
            return [
                {
                    "id": str(edit_record.pk),
                    "transaction_date": edit_record.transaction_date.isoformat(),
                    "category": edit_record.category,
                    "purpose": edit_record.title,
                    "amount": format_money(edit_record.amount),
                    "payment_method": edit_record.payment_method,
                }
            ]
        return [
            {
                "id": "",
                "transaction_date": date.today().isoformat(),
                "category": "",
                "purpose": "",
                "amount": "",
                "payment_method": PaymentMethod.CASH,
            }
        ]

    def post(self, request, *args, **kwargs):
        action = (request.POST.get("action") or "").strip()
        if action == "delete":
            return self.handle_delete(request)
        return self.handle_save(request)

    def handle_delete(self, request):
        record_id = (request.POST.get("record_id") or "").strip()
        record = self.get_base_queryset().filter(pk=record_id).first()
        if record is None:
            messages.error(request, "Expense record not found.")
            return redirect(build_expense_redirect_url(request.POST))

        record.delete()
        messages.success(request, "Expense record deleted successfully.")
        return redirect(build_expense_redirect_url(request.POST))

    def handle_save(self, request):
        record_ids = request.POST.getlist("entry_id")
        transaction_dates = request.POST.getlist("entry_date")
        categories = request.POST.getlist("entry_category")
        purposes = request.POST.getlist("entry_purpose")
        amounts = request.POST.getlist("entry_amount")
        payment_methods = request.POST.getlist("entry_payment_method")

        row_count = max(
            len(record_ids),
            len(transaction_dates),
            len(categories),
            len(purposes),
            len(amounts),
            len(payment_methods),
        )
        created_count = 0
        updated_count = 0
        skipped_count = 0

        valid_payment_methods = {value for value, _label in PaymentMethod.choices}

        for index in range(row_count):
            record_id = (record_ids[index] if index < len(record_ids) else "").strip()
            transaction_date_value = (
                transaction_dates[index] if index < len(transaction_dates) else ""
            ).strip()
            category_value = (categories[index] if index < len(categories) else "").strip()
            purpose_value = (purposes[index] if index < len(purposes) else "").strip()
            amount_value = (amounts[index] if index < len(amounts) else "").strip()
            payment_method_value = (
                payment_methods[index] if index < len(payment_methods) else ""
            ).strip()

            if not any(
                [
                    record_id,
                    transaction_date_value,
                    category_value,
                    purpose_value,
                    amount_value,
                    payment_method_value,
                ]
            ):
                continue

            transaction_date = parse_date(transaction_date_value)
            amount = parse_money_value(amount_value)

            if (
                not transaction_date
                or not category_value
                or not purpose_value
                or amount <= 0
                or payment_method_value not in valid_payment_methods
            ):
                skipped_count += 1
                continue

            if record_id.isdigit():
                record = self.get_base_queryset().filter(pk=record_id).first()
                if record is None:
                    skipped_count += 1
                    continue
                updated_count += 1
            else:
                record = ExpenseRecord(user=request.user)
                created_count += 1

            record.title = purpose_value[:120]
            record.vendor = purpose_value[:120]
            record.category = category_value[:80]
            record.amount = amount
            record.transaction_date = transaction_date
            record.payment_method = payment_method_value
            record.save()

        if created_count or updated_count:
            message_bits = []
            if created_count:
                message_bits.append(
                    f"{created_count} expense record{'s' if created_count != 1 else ''} added"
                )
            if updated_count:
                message_bits.append(
                    f"{updated_count} expense record{'s' if updated_count != 1 else ''} updated"
                )
            messages.success(request, ", ".join(message_bits) + ".")
        else:
            messages.error(request, "Enter at least one complete expense row to save.")

        if skipped_count:
            messages.warning(
                request,
                f"{skipped_count} incomplete row{'s were' if skipped_count != 1 else ' was'} skipped.",
            )

        return redirect(build_expense_redirect_url(request.POST))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        all_records = self.get_base_queryset()
        filtered_records = self.get_queryset()
        today = date.today()
        raw_filter_values = get_raw_expense_filter_values(self.request)
        filter_values = get_expense_filter_values(self.request)
        context["page_total"] = _sum_amount(filtered_records)
        context["page_count"] = filtered_records.count()
        context["supplier_count"] = filter_queryset_by_role(
            Supplier.objects.all(),
            user,
        ).count()
        context["expense_filters"] = filter_values
        context["has_active_filters"] = any(raw_filter_values.values())
        context["is_default_today_view"] = not context["has_active_filters"]
        context["month_total"] = _sum_amount(
            all_records.filter(
                transaction_date__year=today.year,
                transaction_date__month=today.month,
            )
        )
        context["today_total"] = _sum_amount(all_records.filter(transaction_date=today))
        context["filtered_total"] = context["page_total"]
        context["category_options"] = get_expense_category_options(user)
        context["payment_method_options"] = PaymentMethod.choices
        context["entry_rows"] = self.build_entry_rows()
        context["editing_record"] = self.get_edit_record()
        context["filter_query"] = urlencode(get_expense_redirect_params(self.request.GET))
        return context


class ExpenseCreateView(LoginRequiredMixin, CreateView):
    model = ExpenseRecord
    form_class = ExpenseForm
    template_name = "tracker/expense_form.html"
    success_url = reverse_lazy("expense-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.user = self.request.user
        messages.success(self.request, "Expense record created successfully.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["supplier_count"] = filter_queryset_by_role(
            Supplier.objects.all(),
            self.request.user,
        ).count()
        return context


class DailySettlementView(LoginRequiredMixin, TemplateView):
    template_name = "tracker/daily_settlement.html"

    def get_selected_entry_date(self, params, filter_values):
        return get_settlement_entry_date(params, filter_values["end_date"])

    def get_loaded_settlement(self, settlement_date):
        return filter_queryset_by_role(
            DailyCashSettlement.objects.all(),
            self.request.user,
        ).filter(settlement_date=settlement_date).first()

    def get_settlement_queryset(self, filter_values):
        return filter_queryset_by_role(
            DailyCashSettlement.objects.all(),
            self.request.user,
        ).filter(
            settlement_date__gte=filter_values["start_date"],
            settlement_date__lte=filter_values["end_date"],
        ).order_by("-settlement_date", "-updated_at", "-pk")

    def build_form(self, selected_entry_date, loaded_settlement, autofill_summary):
        if loaded_settlement:
            form = DailyCashSettlementForm(instance=loaded_settlement)
        else:
            preview = build_settlement_preview(
                {
                    "opening_balance": autofill_summary["opening_balance"],
                    "sales_ledger_cash": autofill_summary["sales_ledger_cash"],
                    "gpay_settled": autofill_summary["gpay_settled"],
                    "cash_settled": Decimal("0.00"),
                    "expense_amount": autofill_summary["expense_amount"],
                }
            )
            form = DailyCashSettlementForm(
                initial={
                    "settlement_date": selected_entry_date,
                    "gpay_settled": autofill_summary["gpay_settled"],
                    "cash_settled": Decimal("0.00"),
                    "closing_balance": preview["closing_balance"],
                }
            )
        return self.configure_settlement_form(form, autofill_summary)

    def configure_settlement_form(self, form, autofill_summary):
        closing_class = form.fields["closing_balance"].widget.attrs.get("class", "")
        form.fields["closing_balance"].widget.attrs["readonly"] = True
        form.fields["closing_balance"].widget.attrs["class"] = (
            f"{closing_class} settlement-readonly".strip()
        )

        if autofill_summary["settlement_source"] == "sales":
            current_class = form.fields["gpay_settled"].widget.attrs.get("class", "")
            form.fields["gpay_settled"].widget.attrs["readonly"] = True
            form.fields["gpay_settled"].widget.attrs["class"] = (
                f"{current_class} settlement-readonly".strip()
            )
        return form

    def get_cash_denominations(self, params=None, loaded_settlement=None):
        if params is not None and "cash_denominations" in params:
            return parse_cash_denominations_payload(params.get("cash_denominations"))
        if loaded_settlement:
            return parse_cash_denominations_payload(
                build_cash_denominations_payload(loaded_settlement.cash_denominations)
            )
        return {}

    def get_preview_source(self, form, loaded_settlement, autofill_summary):
        if form.is_bound:
            return {
                "opening_balance": autofill_summary["opening_balance"],
                "sales_ledger_cash": autofill_summary["sales_ledger_cash"],
                "gpay_settled": form["gpay_settled"].value(),
                "cash_settled": form["cash_settled"].value(),
                "expense_amount": autofill_summary["expense_amount"],
            }
        if loaded_settlement:
            return {
                "opening_balance": autofill_summary["opening_balance"],
                "sales_ledger_cash": autofill_summary["sales_ledger_cash"],
                "gpay_settled": loaded_settlement.gpay_settled,
                "cash_settled": loaded_settlement.cash_settled,
                "expense_amount": autofill_summary["expense_amount"],
            }
        return {
            "opening_balance": autofill_summary["opening_balance"],
            "sales_ledger_cash": autofill_summary["sales_ledger_cash"],
            "gpay_settled": autofill_summary["gpay_settled"],
            "cash_settled": Decimal("0.00"),
            "expense_amount": autofill_summary["expense_amount"],
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filter_values = kwargs.pop(
            "filter_values",
            get_settlement_filter_values(self.request.GET),
        )
        selected_entry_date = kwargs.pop(
            "selected_entry_date",
            self.get_selected_entry_date(self.request.GET, filter_values),
        )
        loaded_settlement = kwargs.pop(
            "loaded_settlement",
            self.get_loaded_settlement(selected_entry_date),
        )
        cash_denominations = kwargs.pop(
            "cash_denominations",
            self.get_cash_denominations(loaded_settlement=loaded_settlement),
        )
        autofill_summary = build_settlement_autofill_summary(
            self.request.user,
            selected_entry_date,
        )
        form = kwargs.pop("form", None)
        if form is None:
            form = self.build_form(
                selected_entry_date,
                loaded_settlement,
                autofill_summary,
            )
        else:
            form = self.configure_settlement_form(form, autofill_summary)

        settlement_records = self.get_settlement_queryset(filter_values)
        settlement_totals = settlement_records.aggregate(
            gpay_total=Sum("gpay_settled"),
            cash_total=Sum("cash_settled"),
            expense_total=Sum("expense_amount"),
            actual_sales_total=Sum("actual_sales"),
            closing_total=Sum("closing_balance"),
        )

        context.update(
            {
                "form": form,
                "settlement_filters": filter_values,
                "selected_entry_date": selected_entry_date,
                "selected_settlement": loaded_settlement,
                "settlement_preview": build_settlement_preview(
                    self.get_preview_source(form, loaded_settlement, autofill_summary)
                ),
                "settlement_records": settlement_records,
                "settlement_count": settlement_records.count(),
                "gpay_total": settlement_totals["gpay_total"] or Decimal("0.00"),
                "cash_total": settlement_totals["cash_total"] or Decimal("0.00"),
                "expense_total": settlement_totals["expense_total"] or Decimal("0.00"),
                "actual_sales_total": settlement_totals["actual_sales_total"]
                or Decimal("0.00"),
                "closing_total": settlement_totals["closing_total"] or Decimal("0.00"),
                "autofill_summary": autofill_summary,
                "cash_denomination_values": CASH_DENOMINATION_VALUES,
                "cash_denominations": cash_denominations,
                "cash_denominations_json": build_cash_denominations_payload(
                    cash_denominations
                ),
                "cash_denomination_total": get_cash_denominations_total(
                    cash_denominations
                ),
            }
        )
        return context

    def post(self, request, *args, **kwargs):
        filter_values = get_settlement_filter_values(request.POST)
        selected_entry_date = self.get_selected_entry_date(request.POST, filter_values)
        loaded_settlement = self.get_loaded_settlement(selected_entry_date)
        cash_denominations = self.get_cash_denominations(
            params=request.POST,
            loaded_settlement=loaded_settlement,
        )
        post_data = request.POST.copy()
        form = DailyCashSettlementForm(post_data, instance=loaded_settlement)

        if form.is_valid():
            settlement = form.save(commit=False)
            if settlement.pk is None:
                settlement.user = request.user
            autofill_summary = build_settlement_autofill_summary(
                request.user,
                settlement.settlement_date,
            )
            settlement.opening_balance = autofill_summary["opening_balance"]
            settlement.expense_amount = autofill_summary["expense_amount"]
            if autofill_summary["settlement_source"] == "sales":
                settlement.gpay_settled = autofill_summary["gpay_settled"]
            closing_balance = get_settlement_closing_balance(
                settlement.opening_balance,
                autofill_summary["sales_ledger_cash"],
                settlement.expense_amount,
                settlement.cash_settled,
            )
            if closing_balance < 0:
                form.add_error(
                    "cash_settled",
                    (
                        "Cash settled cannot be greater than cash in hand "
                        f"of Rs. {format_money(autofill_summary['cash_in_hand'])}."
                    ),
                )
                return self.render_to_response(
                    self.get_context_data(
                        form=form,
                        filter_values=filter_values,
                        selected_entry_date=selected_entry_date,
                        loaded_settlement=loaded_settlement,
                        cash_denominations=cash_denominations,
                    )
                )
            settlement.closing_balance = closing_balance
            settlement.cash_denominations = cash_denominations
            settlement.save()
            messages.success(
                request,
                f"Daily cash settlement saved for {settlement.settlement_date:%d-%m-%Y}.",
            )
            return redirect(
                f"{reverse('daily-settlement')}?{urlencode({'start_date': settlement.settlement_date.isoformat(), 'end_date': settlement.settlement_date.isoformat(), 'entry_date': settlement.settlement_date.isoformat()})}"
            )

        messages.error(request, "Please correct the highlighted settlement details.")
        return self.render_to_response(
            self.get_context_data(
                form=form,
                filter_values=filter_values,
                selected_entry_date=selected_entry_date,
                loaded_settlement=loaded_settlement,
                cash_denominations=cash_denominations,
            )
        )


class PurchaseListView(AutoLoadPaginatedListView):
    model = PurchaseRecord
    template_name = "tracker/purchase_list.html"
    context_object_name = "records"

    def get_queryset(self):
        queryset = get_purchase_base_queryset(self.request.user)
        queryset = apply_purchase_filters(queryset, get_purchase_filter_values(self.request))
        return queryset.order_by("-transaction_date", "-created_at", "-pk")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        records = self.get_queryset()
        context["page_count"] = records.count()
        context["page_total"] = (
            records.aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00")
        )
        context["paid_total"] = (
            records.aggregate(total=Sum("paid_amount"))["total"] or Decimal("0.00")
        )
        context["pending_total"] = (
            records.aggregate(total=Sum("pending_amount"))["total"] or Decimal("0.00")
        )
        raw_filter_values = get_raw_purchase_filter_values(self.request)
        filter_values = get_purchase_filter_values(self.request)
        context["purchase_filters"] = filter_values
        context["has_active_filters"] = any(raw_filter_values.values())
        context["is_default_today_view"] = not context["has_active_filters"]
        context["default_view_date"] = date.today()
        return context


class SalesListView(AutoLoadPaginatedListView):
    model = SalesLedgerRecord
    template_name = "tracker/sales_list.html"
    context_object_name = "records"

    def get_base_queryset(self):
        return SalesLedgerRecord.objects.filter(is_cancelled=False)

    def get_queryset(self):
        queryset = apply_sales_filters(
            self.get_base_queryset(),
            get_sales_filter_values(self.request.GET),
        )
        return queryset.order_by("-sale_date", "-source_sale_no")

    def post(self, request, *args, **kwargs):
        action = (request.POST.get("action") or "").strip()
        if action == "save_split":
            return self.handle_split_save(request)

        sales_filters = get_sales_filter_values(request.POST)
        try:
            stats = sync_sales_from_sqlserver(
                date_from=parse_date(sales_filters["date_from"]),
                date_to=parse_date(sales_filters["date_to"]),
            )
        except SalesSyncError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                "Sales sync completed. "
                f"{stats.fetched_count} rows processed, "
                f"{stats.inserted_count} inserted, "
                f"{stats.refreshed_count} refreshed.",
            )
        return redirect(build_sales_redirect_url(sales_filters))

    def handle_split_save(self, request):
        record_id = (request.POST.get("record_id") or "").strip()
        record = self.get_base_queryset().filter(pk=record_id).first()
        if record is None:
            messages.error(request, "Sales bill not found.")
            return redirect(build_sales_redirect_url(request.POST))

        split_cash_amount = parse_money_value(request.POST.get("split_cash_amount"))
        split_card_amount = parse_money_value(request.POST.get("split_card_amount"))

        if split_cash_amount < 0 or split_card_amount < 0:
            messages.error(request, "Cash and card amounts cannot be negative.")
            return redirect(build_sales_redirect_url(request.POST))

        split_total = split_cash_amount + split_card_amount
        if split_total > record.net_amount:
            messages.error(
                request,
                (
                    f"Split total for bill {record.bill_no} cannot exceed "
                    f"the net amount of Rs. {format_money(record.net_amount)}."
                ),
            )
            return redirect(build_sales_redirect_url(request.POST))

        record.split_cash_amount = split_cash_amount
        record.split_card_amount = split_card_amount
        record.save(update_fields=["split_cash_amount", "split_card_amount"])

        if split_total == 0:
            messages.success(
                request,
                f"Split values cleared for bill {record.bill_no}.",
            )
        else:
            messages.success(
                request,
                (
                    f"Split saved for bill {record.bill_no}: "
                    f"Cash Rs. {format_money(split_cash_amount)} and "
                    f"Card Rs. {format_money(split_card_amount)}."
                ),
            )
        return redirect(build_sales_redirect_url(request.POST))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        records = self.get_queryset()
        aggregates = records.aggregate(
            total_net=Sum("net_amount"),
            total_received=Sum("received_amount"),
            total_balance=Sum("balance_amount"),
            total_split_cash=Sum("split_cash_amount"),
            total_split_card=Sum("split_card_amount"),
        )
        context["page_count"] = records.count()
        context["page_total"] = aggregates["total_net"] or Decimal("0.00")
        context["received_total"] = aggregates["total_received"] or Decimal("0.00")
        context["balance_total"] = aggregates["total_balance"] or Decimal("0.00")
        context["split_cash_total"] = aggregates["total_split_cash"] or Decimal("0.00")
        context["split_card_total"] = aggregates["total_split_card"] or Decimal("0.00")
        context["sales_filters"] = get_sales_filter_values(self.request.GET)
        context["has_active_filters"] = any(
            get_raw_sales_filter_values(self.request.GET).values()
        )
        context["payment_mode_options"] = SalesPaymentMode.choices
        context["synced_record_count"] = self.get_base_queryset().count()
        context["last_synced_at"] = (
            SalesLedgerRecord.objects.order_by("-synced_at")
            .values_list("synced_at", flat=True)
            .first()
        )
        context["is_default_today_view"] = not context["has_active_filters"]
        return context


class PurchaseDetailView(LoginRequiredMixin, View):
    def get(self, request, pk, *args, **kwargs):
        purchase = get_object_or_404(
            get_purchase_base_queryset(request.user).prefetch_related("payments__user"),
            pk=pk,
        )
        return JsonResponse(build_purchase_detail_payload(purchase))


class PurchaseCreateView(LoginRequiredMixin, CreateView):
    model = PurchaseRecord
    form_class = PurchaseForm
    template_name = "tracker/purchase_form.html"
    success_url = reverse_lazy("purchase-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.user = self.request.user
        self.object = form.save()
        if self.object.paid_amount > 0:
            create_purchase_payment(
                purchase=self.object,
                user=self.request.user,
                amount=self.object.paid_amount,
                notes="Initial paid amount saved with the purchase record.",
                payment_date=self.object.transaction_date,
                update_totals=False,
            )
        sync_purchase_to_expense(self.object)
        messages.success(self.request, "Purchase record saved successfully and added to expenses.")
        if "_download_invoice" in self.request.POST:
            return build_purchase_invoice_response(self.object)
        return redirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["supplier_count"] = filter_queryset_by_role(
            Supplier.objects.all(),
            self.request.user,
        ).count()
        return context


class PurchaseInvoiceDownloadView(LoginRequiredMixin, View):
    def get(self, request, pk, *args, **kwargs):
        purchase = get_object_or_404(
            get_purchase_base_queryset(request.user),
            pk=pk,
        )
        return build_purchase_invoice_response(purchase)


class PurchasePaymentCreateView(LoginRequiredMixin, View):
    success_url = reverse_lazy("purchase-list")

    def post(self, request, pk, *args, **kwargs):
        with transaction.atomic():
            locked_queryset = filter_queryset_by_role(
                PurchaseRecord.objects.select_for_update(),
                request.user,
            )
            purchase = get_object_or_404(
                locked_queryset,
                pk=pk,
            )
            form = PurchasePaymentForm(request.POST, purchase=purchase)
            if not form.is_valid():
                error_text = " ".join(
                    error
                    for field_errors in form.errors.values()
                    for error in field_errors
                )
                messages.error(request, error_text or "Could not save the payment entry.")
                return redirect(self.success_url)

            create_purchase_payment(
                purchase=purchase,
                user=request.user,
                amount=form.cleaned_data["amount"],
                notes=form.cleaned_data["notes"],
            )
            sync_purchase_to_expense(purchase)

        messages.success(
            request,
            f"Payment of Rs. {format_money(form.cleaned_data['amount'])} saved for invoice {purchase.invoice_number}.",
        )
        return redirect(self.success_url)


class SupplierListView(LoginRequiredMixin, ListView):
    model = Supplier
    template_name = "tracker/supplier_list.html"
    context_object_name = "suppliers"

    def get_queryset(self):
        return filter_queryset_by_role(Supplier.objects.all(), self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        suppliers = self.get_queryset()
        context["page_count"] = suppliers.count()
        return context


class SupplierCreateView(LoginRequiredMixin, CreateView):
    model = Supplier
    form_class = SupplierForm
    template_name = "tracker/supplier_form.html"
    success_url = reverse_lazy("supplier-list")

    def form_valid(self, form):
        form.instance.user = self.request.user
        messages.success(self.request, "Supplier details saved successfully.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["next_supplier_code"] = get_next_supplier_code()
        return context


class UserListView(AdminRequiredMixin, ListView):
    model = User
    template_name = "tracker/user_list.html"
    context_object_name = "users"

    def get_queryset(self):
        return User.objects.select_related("account_profile").order_by("username")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        for user in context["users"]:
            profile = getattr(user, "account_profile", None)
            user.master_name_display = (
                profile.master_name if profile and profile.master_name else "-"
            )
            user.role_label = get_user_role_label(user)
            user.status_label = "Active" if user.is_active else "Inactive"

        user_queryset = User.objects.all()
        context["page_count"] = user_queryset.count()
        context["active_count"] = user_queryset.filter(is_active=True).count()
        context["inactive_count"] = user_queryset.filter(is_active=False).count()
        return context


class UserCreateView(AdminRequiredMixin, CreateView):
    model = User
    form_class = UserManagementForm
    template_name = "tracker/user_form.html"
    success_url = reverse_lazy("user-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["current_user"] = self.request.user
        kwargs["require_password"] = True
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, "User created successfully.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_eyebrow"] = "User Access"
        context["page_title"] = "Create User"
        context["page_description"] = (
            "Create admin, store admin, or staff users and control whether the account is active."
        )
        context["submit_label"] = "Save User"
        return context


class UserUpdateView(AdminRequiredMixin, UpdateView):
    model = User
    form_class = UserManagementForm
    template_name = "tracker/user_form.html"
    success_url = reverse_lazy("user-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["current_user"] = self.request.user
        kwargs["require_password"] = False
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, "User updated successfully.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_eyebrow"] = "User Access"
        context["page_title"] = f"Edit User - {self.object.username}"
        context["page_description"] = (
            "Update role, active status, master name, and password for this account."
        )
        context["submit_label"] = "Update User"
        return context


class ReportsView(LoginRequiredMixin, TemplateView):
    template_name = "tracker/reports.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        income_by_category = (
            filter_queryset_by_role(IncomeRecord.objects.all(), user)
            .values("category")
            .annotate(total=Sum("amount"))
            .order_by("-total")[:5]
        )
        expense_by_category = (
            filter_queryset_by_role(ExpenseRecord.objects.all(), user)
            .values("category")
            .annotate(total=Sum("amount"))
            .order_by("-total")[:5]
        )
        context.update(
            {
                "monthly_overview": build_monthly_overview(user),
                "top_income_categories": income_by_category,
                "top_expense_categories": expense_by_category,
            }
        )
        return context
