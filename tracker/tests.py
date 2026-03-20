import json
import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from openpyxl import load_workbook
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .expense_categories import COUNTER_EXPENSE_CATEGORY
from .models import (
    DailyCashSettlement,
    ExpenseCategory,
    ExpenseRecord,
    IncomeRecord,
    PaymentMethod,
    PurchasePayment,
    PurchaseRecord,
    ReconciliationExpenseEntry,
    ReconciliationIncomeEntry,
    ReconciliationOpeningBalance,
    SalesLedgerRecord,
    SalesPaymentMode,
    Supplier,
    UserAccountProfile,
    UserModulePermission,
)
from .sales_sync import (
    SOURCE_SALE_TYPE_PAYMENT_MODE_MAP,
    SalesSyncError,
    SalesSyncStats,
    build_sales_sync_query,
    build_sqlserver_connection_string,
    classify_sales_payment_mode,
    sanitize_sales_amounts,
)
from .supplier_sync import SupplierSyncStats, sync_suppliers_from_rows
from .user_sync import (
    USER_SYNC_SOURCE_REFERENCE,
    UserSyncStats,
    sync_users_from_rows,
)


class TrackerViewsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="mahilmart_admin",
            password="StrongPass123!",
        )
        self.admin_user = get_user_model().objects.create_superuser(
            username="system_admin",
            password="SuperPass123!",
        )

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_dashboard_displays_financial_totals(self):
        self.client.force_login(self.user)
        supplier = Supplier.objects.create(
            user=self.user,
            name="Fresh Foods Ltd",
            contact_person="Ravi",
            phone_number="9876500001",
        )
        today = date.today()
        yesterday = today - timedelta(days=1)
        IncomeRecord.objects.create(
            user=self.user,
            title="Online Sales",
            source="Mahilmart Store",
            category="Retail",
            amount=Decimal("35000.00"),
            transaction_date=yesterday,
        )
        ExpenseRecord.objects.create(
            user=self.user,
            title="Supplier Payment",
            supplier=supplier,
            vendor=supplier.name,
            category="Inventory",
            amount=Decimal("18500.00"),
            transaction_date=today,
        )

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["income_total"], Decimal("0.00"))
        self.assertEqual(response.context["expense_total"], Decimal("18500.00"))
        self.assertEqual(response.context["balance"], Decimal("-18500.00"))
        self.assertEqual(response.context["opening_balance"], Decimal("35000.00"))
        self.assertEqual(response.context["closing_balance"], Decimal("0.00"))
        self.assertEqual(response.context["selected_day_balance"], Decimal("0.00"))

    def test_dashboard_renders_premium_sections(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cash Flow Analytics")
        self.assertContains(response, "Settlement Status")
        self.assertContains(response, "Quick Moves")

    def test_dashboard_shares_admin_role_data_across_admin_accounts(self):
        peer_admin = get_user_model().objects.create_superuser(
            username="peer_admin",
            password="PeerPass123!",
        )
        supplier = Supplier.objects.create(
            user=self.admin_user,
            name="Admin Shared Supplier",
            contact_person="Peer",
            phone_number="9876500011",
        )
        DailyCashSettlement.objects.create(
            user=self.admin_user,
            settlement_date=date.today(),
            opening_balance=Decimal("0.00"),
            gpay_settled=Decimal("700.00"),
            cash_settled=Decimal("300.00"),
            expense_amount=Decimal("0.00"),
            closing_balance=Decimal("150.00"),
        )
        ExpenseRecord.objects.create(
            user=self.admin_user,
            title="Shared Admin Expense",
            supplier=supplier,
            vendor=supplier.name,
            category="Inventory",
            amount=Decimal("250.00"),
            transaction_date=date.today(),
        )

        self.client.force_login(peer_admin)
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["income_total"], Decimal("1150.00"))
        self.assertEqual(response.context["expense_total"], Decimal("250.00"))
        self.assertContains(response, "Admin Role")
        self.assertContains(response, "Dashboard")

    def test_reports_page_shows_requested_summary_totals(self):
        self.client.force_login(self.user)
        supplier = Supplier.objects.create(
            user=self.user,
            name="Reports Supplier",
            contact_person="Kumar",
            phone_number="9876500042",
        )
        IncomeRecord.objects.create(
            user=self.user,
            title="Counter Sales",
            source="Mahilmart Store",
            category="Retail",
            amount=Decimal("1200.00"),
            transaction_date=date.today(),
        )
        ExpenseRecord.objects.create(
            user=self.user,
            title="Shop Expense",
            supplier=supplier,
            vendor=supplier.name,
            category="Operations",
            amount=Decimal("300.00"),
            transaction_date=date.today(),
        )
        PurchaseRecord.objects.create(
            user=self.user,
            supplier=supplier,
            supplier_name="",
            purchase_type="Groceries",
            invoice_number="INV-REPORT-1",
            total_amount=Decimal("450.00"),
            paid_amount=Decimal("200.00"),
            transaction_date=date.today(),
        )

        response = self.client.get(reverse("reports"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_sales"], Decimal("1200.00"))
        self.assertEqual(response.context["total_expenses"], Decimal("300.00"))
        self.assertEqual(response.context["net_profit"], Decimal("900.00"))
        self.assertEqual(response.context["total_purchases"], Decimal("450.00"))
        self.assertEqual(response.context["total_suppliers"], 1)
        self.assertContains(response, "Total Sales")
        self.assertContains(response, "Total Purchases")
        self.assertContains(response, "Net Profit")
        self.assertNotContains(response, "<th>Balance</th>", html=False)

    def test_reports_page_includes_daily_settlement_income(self):
        self.client.force_login(self.user)
        IncomeRecord.objects.create(
            user=self.user,
            title="Manual Income",
            source="Counter Sale",
            category="Retail",
            amount=Decimal("250.00"),
            transaction_date=date.today(),
        )
        DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=date.today(),
            opening_balance=Decimal("0.00"),
            gpay_settled=Decimal("700.00"),
            cash_settled=Decimal("300.00"),
            expense_amount=Decimal("50.00"),
            closing_balance=Decimal("100.00"),
        )

        response = self.client.get(reverse("reports"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_sales"], Decimal("1400.00"))
        self.assertEqual(response.context["net_profit"], Decimal("1400.00"))
        self.assertEqual(
            response.context["monthly_overview"][-1]["income"],
            Decimal("1400.00"),
        )
        self.assertEqual(
            response.context["top_income_categories"][0]["category"],
            "Daily Settlement",
        )
        self.assertContains(response, "Daily Settlement")

    def test_reports_excel_download_exports_selected_month_records(self):
        self.client.force_login(self.user)
        selected_month = date.today().replace(day=1)
        previous_month_date = selected_month - timedelta(days=1)
        supplier = Supplier.objects.create(
            user=self.user,
            name="Excel Reports Supplier",
            contact_person="Meena",
            phone_number="9876500099",
        )
        IncomeRecord.objects.create(
            user=self.user,
            title="Selected Month Income",
            source="Counter Sale",
            category="Retail",
            amount=Decimal("250.00"),
            transaction_date=selected_month,
        )
        IncomeRecord.objects.create(
            user=self.user,
            title="Old Income",
            source="Old Counter",
            category="Retail",
            amount=Decimal("999.00"),
            transaction_date=previous_month_date,
        )
        ExpenseRecord.objects.create(
            user=self.user,
            title="Selected Month Expense",
            supplier=supplier,
            vendor=supplier.name,
            category="Operations",
            amount=Decimal("100.00"),
            transaction_date=selected_month,
        )
        PurchaseRecord.objects.create(
            user=self.user,
            supplier=supplier,
            supplier_name="",
            purchase_type="Groceries",
            invoice_number="INV-EXCEL-1",
            total_amount=Decimal("450.00"),
            paid_amount=Decimal("200.00"),
            transaction_date=selected_month,
        )
        DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=selected_month,
            opening_balance=Decimal("0.00"),
            gpay_settled=Decimal("700.00"),
            cash_settled=Decimal("300.00"),
            expense_amount=Decimal("50.00"),
            closing_balance=Decimal("100.00"),
        )
        DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=previous_month_date,
            opening_balance=Decimal("0.00"),
            gpay_settled=Decimal("100.00"),
            cash_settled=Decimal("100.00"),
            expense_amount=Decimal("0.00"),
            closing_balance=Decimal("0.00"),
        )

        response = self.client.get(
            reverse("reports"),
            {
                "report_month": selected_month.strftime("%Y-%m"),
                "export": "excel",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn(
            f'mahilmart_report_{selected_month.strftime("%Y-%m")}.xlsx',
            response["Content-Disposition"],
        )

        workbook = load_workbook(BytesIO(response.content), data_only=True)
        self.assertEqual(
            workbook.sheetnames,
            ["Summary", "Income", "Expenses", "Purchases", "Daily Settlement"],
        )

        summary_sheet = workbook["Summary"]
        summary_values = {
            row[0]: row[1]
            for row in summary_sheet.iter_rows(min_row=4, max_col=2, values_only=True)
            if row[0]
        }
        self.assertEqual(summary_values["Selected Month"], selected_month.strftime("%B %Y"))
        self.assertEqual(summary_values["Daily Settlement Income"], 1150)
        self.assertEqual(summary_values["Total Sales"], 1400)
        self.assertEqual(summary_values["Total Expenses"], 100)
        self.assertEqual(summary_values["Total Purchases"], 450)

        income_titles = [
            row[1]
            for row in workbook["Income"].iter_rows(min_row=2, values_only=True)
            if row[1]
        ]
        self.assertIn("Selected Month Income", income_titles)
        self.assertIn("Daily Settlement Income", income_titles)
        self.assertNotIn("Old Income", income_titles)

    def test_reconciliation_page_shows_income_and_expense_in_one_place(self):
        self.client.force_login(self.user)
        ReconciliationIncomeEntry.objects.create(
            user=self.user,
            title="Reconciliation Cash Collection",
            source="Separate Counter",
            category="Retail",
            amount=Decimal("300.00"),
            transaction_date=date.today(),
        )
        ReconciliationExpenseEntry.objects.create(
            user=self.user,
            title="Reconciliation Office Expense",
            vendor="Office",
            category="Operations",
            amount=Decimal("120.00"),
            transaction_date=date.today(),
        )

        response = self.client.get(
            reverse("reconciliation"),
            {
                "start_date": date.today().isoformat(),
                "end_date": date.today().isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["manual_income_total"], Decimal("300.00"))
        self.assertEqual(response.context["settlement_income_total"], Decimal("0.00"))
        self.assertEqual(response.context["income_total"], Decimal("300.00"))
        self.assertEqual(response.context["expense_total"], Decimal("120.00"))
        self.assertEqual(response.context["net_total"], Decimal("180.00"))
        self.assertEqual(response.context["income_count"], 1)
        self.assertEqual(response.context["expense_count"], 1)
        self.assertContains(response, "Add reconciliation income")
        self.assertContains(response, "Add reconciliation expense")
        self.assertContains(response, "Reconciliation Cash Collection")
        self.assertContains(response, "Reconciliation Office Expense")

    def test_reconciliation_page_defaults_to_current_date_only(self):
        self.client.force_login(self.user)
        today = date.today()
        yesterday = today - timedelta(days=1)
        tomorrow = today + timedelta(days=1)

        ReconciliationIncomeEntry.objects.create(
            user=self.user,
            title="Yesterday Income",
            source="Old Range",
            category="Retail",
            amount=Decimal("100.00"),
            transaction_date=yesterday,
        )
        ReconciliationIncomeEntry.objects.create(
            user=self.user,
            title="Today Income",
            source="Current Day",
            category="Retail",
            amount=Decimal("250.00"),
            transaction_date=today,
        )
        ReconciliationExpenseEntry.objects.create(
            user=self.user,
            title="Tomorrow Expense",
            vendor="Future",
            category="Ops",
            amount=Decimal("80.00"),
            transaction_date=tomorrow,
        )

        response = self.client.get(reverse("reconciliation"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["reconciliation_filters"]["start_date"], today.isoformat())
        self.assertEqual(response.context["reconciliation_filters"]["end_date"], today.isoformat())
        self.assertEqual(response.context["income_count"], 1)
        self.assertEqual(response.context["expense_count"], 0)
        self.assertContains(response, "Today Income")
        self.assertNotContains(response, "Yesterday Income")
        self.assertNotContains(response, "Tomorrow Expense")

    def test_reconciliation_page_includes_cash_and_card_amounts_from_daily_settlement(self):
        self.client.force_login(self.user)
        DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=date.today(),
            opening_balance=Decimal("100.00"),
            gpay_settled=Decimal("250.00"),
            cash_settled=Decimal("400.00"),
            cash_settled_to="Admin Counter",
            expense_amount=Decimal("50.00"),
            closing_balance=Decimal("100.00"),
            notes="Shift closed",
        )
        ReconciliationExpenseEntry.objects.create(
            user=self.user,
            title="Recon Cash Drop Expense",
            vendor="Office Safe",
            category="Transfer",
            amount=Decimal("120.00"),
            transaction_date=date.today(),
        )

        response = self.client.get(
            reverse("reconciliation"),
            {
                "start_date": date.today().isoformat(),
                "end_date": date.today().isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["manual_income_total"], Decimal("0.00"))
        self.assertEqual(response.context["settlement_cash_total"], Decimal("400.00"))
        self.assertEqual(response.context["settlement_card_total"], Decimal("250.00"))
        self.assertEqual(response.context["split_card_balance_total"], Decimal("250.00"))
        self.assertEqual(response.context["reconciliation_opening_balance"], Decimal("0.00"))
        self.assertEqual(response.context["reconciliation_closing_balance"], Decimal("650.00"))
        self.assertEqual(response.context["next_day_opening_balance"], Decimal("650.00"))
        self.assertEqual(response.context["settlement_income_total"], Decimal("650.00"))
        self.assertEqual(response.context["income_total"], Decimal("650.00"))
        self.assertEqual(response.context["expense_total"], Decimal("120.00"))
        self.assertEqual(response.context["net_total"], Decimal("530.00"))
        self.assertEqual(response.context["income_count"], 2)
        self.assertContains(response, "Daily Cash Settlement")
        self.assertContains(response, "Daily Card Settlement")
        self.assertContains(response, "Cash settled to Admin Counter")

    def test_reconciliation_page_includes_purchase_records_in_expense_history(self):
        self.client.force_login(self.user)
        supplier = Supplier.objects.create(
            user=self.user,
            name="Recon Purchase Supplier",
            contact_person="Selvam",
            phone_number="9876500033",
        )
        PurchaseRecord.objects.create(
            user=self.user,
            supplier=supplier,
            supplier_name="",
            purchase_type="Stock Purchase",
            invoice_number="PUR-1001",
            total_amount=Decimal("875.00"),
            paid_amount=Decimal("400.00"),
            transaction_date=date.today(),
        )

        response = self.client.get(
            reverse("reconciliation"),
            {
                "start_date": date.today().isoformat(),
                "end_date": date.today().isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["manual_expense_total"], Decimal("0.00"))
        self.assertEqual(response.context["purchase_total"], Decimal("400.00"))
        self.assertEqual(response.context["expense_total"], Decimal("400.00"))
        self.assertEqual(response.context["expense_count"], 1)
        self.assertContains(response, "PUR-1001")
        self.assertContains(response, "Stock Purchase")
        self.assertContains(response, "Recon Purchase Supplier")

    def test_reconciliation_income_entry_saves_opening_balance(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("reconciliation"),
            {
                "action": "save_income",
                "start_date": date.today().isoformat(),
                "end_date": date.today().isoformat(),
                "income-title": "Opening Balance Income Entry",
                "income-source": "Recon Source",
                "income-category": "Recon Category",
                "income-amount": "450.00",
                "income-opening_balance": "125.00",
                "income-transaction_date": date.today().isoformat(),
                "income-payment_method": PaymentMethod.CASH,
                "income-notes": "Opening balance tracked",
            },
        )

        self.assertEqual(response.status_code, 302)
        entry = ReconciliationIncomeEntry.objects.get(title="Opening Balance Income Entry")
        self.assertEqual(entry.opening_balance, Decimal("125.00"))
        self.assertTrue(
            ReconciliationOpeningBalance.objects.filter(
                user=self.user,
                balance_date=date.today(),
                amount=Decimal("125.00"),
            ).exists()
        )

    def test_reconciliation_opening_balance_uses_previous_day_closing_balance(self):
        self.client.force_login(self.user)
        yesterday = date.today() - timedelta(days=1)
        today = date.today()

        DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=yesterday,
            opening_balance=Decimal("0.00"),
            gpay_settled=Decimal("300.00"),
            cash_settled=Decimal("500.00"),
            expense_amount=Decimal("0.00"),
            closing_balance=Decimal("0.00"),
        )
        ReconciliationIncomeEntry.objects.create(
            user=self.user,
            title="Yesterday Manual Income",
            source="Separate Counter",
            category="Retail",
            amount=Decimal("200.00"),
            opening_balance=Decimal("0.00"),
            transaction_date=yesterday,
        )

        response = self.client.get(
            reverse("reconciliation"),
            {
                "start_date": today.isoformat(),
                "end_date": today.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["reconciliation_opening_balance"], Decimal("1000.00"))
        self.assertEqual(response.context["income_form"].initial["opening_balance"], Decimal("1000.00"))

    def test_reconciliation_opening_balance_popup_saves_override(self):
        self.client.force_login(self.user)
        today = date.today()

        response = self.client.post(
            reverse("reconciliation"),
            {
                "action": "save_opening_balance",
                "start_date": today.isoformat(),
                "end_date": today.isoformat(),
                "opening-balance_date": today.isoformat(),
                "opening-amount": "825.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ReconciliationOpeningBalance.objects.filter(
                user=self.user,
                balance_date=today,
                amount=Decimal("825.00"),
            ).exists()
        )

        page = self.client.get(
            reverse("reconciliation"),
            {
                "start_date": today.isoformat(),
                "end_date": today.isoformat(),
            },
        )

        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["reconciliation_opening_balance"], Decimal("825.00"))
        self.assertEqual(page.context["income_total"], Decimal("825.00"))
        self.assertEqual(page.context["net_total"], Decimal("825.00"))
        self.assertContains(page, "data-open-opening-balance-modal")
        self.assertContains(page, "Save Opening Balance")

    def test_reconciliation_split_card_balance_carries_forward_and_uses_card_entries(self):
        self.client.force_login(self.user)
        today = date.today()
        tomorrow = today + timedelta(days=1)

        DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=today,
            opening_balance=Decimal("0.00"),
            gpay_settled=Decimal("1000.00"),
            cash_settled=Decimal("0.00"),
            expense_amount=Decimal("0.00"),
            closing_balance=Decimal("0.00"),
        )
        DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=tomorrow,
            opening_balance=Decimal("0.00"),
            gpay_settled=Decimal("250.00"),
            cash_settled=Decimal("0.00"),
            expense_amount=Decimal("0.00"),
            closing_balance=Decimal("0.00"),
        )
        ReconciliationIncomeEntry.objects.create(
            user=self.user,
            title="Card Income",
            source="Swipe Machine",
            category="Card",
            amount=Decimal("200.00"),
            opening_balance=Decimal("0.00"),
            transaction_date=tomorrow,
            payment_method=PaymentMethod.CARD,
        )
        ReconciliationIncomeEntry.objects.create(
            user=self.user,
            title="Cash Income",
            source="Counter",
            category="Cash",
            amount=Decimal("300.00"),
            opening_balance=Decimal("0.00"),
            transaction_date=tomorrow,
            payment_method=PaymentMethod.CASH,
        )
        ReconciliationExpenseEntry.objects.create(
            user=self.user,
            title="Card Expense",
            vendor="Gateway Fee",
            category="Card",
            amount=Decimal("50.00"),
            transaction_date=tomorrow,
            payment_method=PaymentMethod.CARD,
        )

        response = self.client.get(
            reverse("reconciliation"),
            {
                "start_date": tomorrow.isoformat(),
                "end_date": tomorrow.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["settlement_card_total"], Decimal("250.00"))
        self.assertEqual(response.context["split_card_balance_total"], Decimal("1400.00"))
        self.assertEqual(response.context["reconciliation_opening_balance"], Decimal("1000.00"))
        self.assertEqual(response.context["income_total"], Decimal("1750.00"))
        self.assertEqual(response.context["reconciliation_closing_balance"], Decimal("1750.00"))
        self.assertContains(response, "Split card balance")

    def test_reconciliation_entries_are_separate_from_income_and_expense_pages(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("reconciliation"),
            {
                "action": "save_income",
                "start_date": date.today().isoformat(),
                "end_date": date.today().isoformat(),
                "income-title": "Separate Income Entry",
                "income-source": "Recon Source",
                "income-category": "Recon Category",
                "income-amount": "450.00",
                "income-transaction_date": date.today().isoformat(),
                "income-payment_method": PaymentMethod.CASH,
                "income-notes": "Reconciliation only",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ReconciliationIncomeEntry.objects.filter(title="Separate Income Entry").exists()
        )
        self.assertFalse(IncomeRecord.objects.filter(title="Separate Income Entry").exists())

        income_page = self.client.get(reverse("income-list"))
        self.assertNotContains(income_page, "Separate Income Entry")

        response = self.client.post(
            reverse("reconciliation"),
            {
                "action": "save_expense",
                "start_date": date.today().isoformat(),
                "end_date": date.today().isoformat(),
                "expense-title": "Separate Expense Entry",
                "expense-vendor": "Recon Vendor",
                "expense-category": "Recon Expense",
                "expense-amount": "210.00",
                "expense-transaction_date": date.today().isoformat(),
                "expense-payment_method": PaymentMethod.CASH,
                "expense-notes": "Reconciliation expense only",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ReconciliationExpenseEntry.objects.filter(title="Separate Expense Entry").exists()
        )
        self.assertFalse(ExpenseRecord.objects.filter(title="Separate Expense Entry").exists())

        expense_page = self.client.get(reverse("expense-list"))
        self.assertNotContains(expense_page, "Separate Expense Entry")

    def test_expense_add_page_includes_suppliers_from_same_role(self):
        peer_admin = get_user_model().objects.create_superuser(
            username="peer_admin_supplier",
            password="PeerPass123!",
        )
        Supplier.objects.create(
            user=peer_admin,
            name="Role Shared Supplier",
            contact_person="Selvam",
            phone_number="9876500012",
        )

        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("expense-add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Role Shared Supplier")

    def test_expense_add_page_does_not_include_categories_from_same_role(self):
        peer_admin = get_user_model().objects.create_superuser(
            username="peer_admin_category",
            password="PeerPass123!",
        )
        ExpenseCategory.objects.create(
            user=peer_admin,
            name="Peer Only Category",
        )

        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("expense-add"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Peer Only Category")
        self.assertContains(response, "Purpose")
        self.assertContains(response, "Manage Categories")
        self.assertEqual(
            list(response.context["form"].fields["category"].choices),
            [
                ("", "Select Category"),
                (COUNTER_EXPENSE_CATEGORY, COUNTER_EXPENSE_CATEGORY),
            ],
        )

    def test_expense_add_page_auto_creates_categories_without_default_selection(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("expense-add"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            ExpenseCategory.objects.filter(
                user=self.user,
                name=COUNTER_EXPENSE_CATEGORY,
            ).exists()
        )
        self.assertFalse(response.context["form"].fields["category"].initial)
        self.assertContains(response, "Select Category")
        self.assertContains(response, 'data-purpose-category="expense-form"', html=False)
        self.assertContains(response, 'data-purpose-select="expense-form"', html=False)
        self.assertContains(response, 'id="expense-purpose-select"', html=False)
        self.assertEqual(
            list(response.context["form"].fields["category"].choices),
            [
                ("", "Select Category"),
                (COUNTER_EXPENSE_CATEGORY, COUNTER_EXPENSE_CATEGORY),
            ],
        )
        self.assertContains(response, COUNTER_EXPENSE_CATEGORY)
        self.assertTrue(
            ExpenseCategory.objects.filter(
                user=self.user,
                name="Utility Expenses",
            ).exists()
        )
        self.assertContains(response, "Electricity Bill")

    def test_expense_add_page_has_no_default_category_for_another_user(self):
        other_user = get_user_model().objects.create_user(
            username="expense_user_two",
            password="StrongPass456!",
        )
        self.client.force_login(other_user)

        response = self.client.get(reverse("expense-add"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["form"].fields["category"].initial)
        self.assertContains(response, "Select Category")
        self.assertContains(response, COUNTER_EXPENSE_CATEGORY)
        self.assertEqual(
            list(response.context["form"].fields["category"].choices),
            [
                ("", "Select Category"),
                (COUNTER_EXPENSE_CATEGORY, COUNTER_EXPENSE_CATEGORY),
            ],
        )
        self.assertTrue(
            ExpenseCategory.objects.filter(
                user=other_user,
                name=COUNTER_EXPENSE_CATEGORY,
            ).exists()
        )

    def test_reconciliation_page_uses_expense_category_purpose_library(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("reconciliation"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-purpose-category="reconciliation-expense"', html=False)
        self.assertContains(response, 'data-purpose-select="reconciliation-expense"', html=False)
        self.assertContains(response, 'id="reconciliation-purpose-select"', html=False)
        self.assertContains(response, "Utility Expenses")
        self.assertContains(response, "Electricity Bill")

    def test_expense_category_page_creates_category(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("expense-category-list"),
            {
                "name": "Courier Charges",
                "next": reverse("expense-add"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("expense-add"))
        self.assertTrue(
            ExpenseCategory.objects.filter(
                user=self.user,
                name="Courier Charges",
            ).exists()
        )

    def test_expense_category_page_creates_category_with_ajax(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("expense-category-list"),
            {
                "name": "Milk Run",
                "next": reverse("expense-add"),
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["created"])
        self.assertEqual(payload["name"], "Milk Run")
        self.assertGreaterEqual(payload["category_count"], 1)
        self.assertTrue(
            ExpenseCategory.objects.filter(
                user=self.user,
                name="Milk Run",
            ).exists()
        )

    def test_daily_settlement_post_updates_existing_same_role_record(self):
        peer_admin = get_user_model().objects.create_superuser(
            username="peer_admin_settlement",
            password="PeerPass123!",
        )
        DailyCashSettlement.objects.create(
            user=self.admin_user,
            settlement_date=date.today() - timedelta(days=1),
            opening_balance=Decimal("0.00"),
            gpay_settled=Decimal("0.00"),
            cash_settled=Decimal("0.00"),
            expense_amount=Decimal("0.00"),
            closing_balance=Decimal("200.00"),
        )
        settlement = DailyCashSettlement.objects.create(
            user=self.admin_user,
            settlement_date=date.today(),
            opening_balance=Decimal("0.00"),
            gpay_settled=Decimal("100.00"),
            cash_settled=Decimal("50.00"),
            expense_amount=Decimal("0.00"),
            closing_balance=Decimal("25.00"),
        )
        today_value = date.today().isoformat()

        self.client.force_login(peer_admin)
        response = self.client.post(
            reverse("daily-settlement"),
            {
                "settlement_date": today_value,
                "gpay_settled": "200.00",
                "cash_settled": "150.00",
                "cash_denominations": "",
                "cash_settled_to": "Shared Counter",
                "closing_balance": "75.00",
                "notes": "Updated by another admin account",
                "selected_date": today_value,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            DailyCashSettlement.objects.filter(
                settlement_date=date.today(),
                user__is_superuser=True,
            ).count(),
            1,
        )
        settlement.refresh_from_db()
        self.assertEqual(settlement.opening_balance, Decimal("200.00"))
        self.assertEqual(settlement.gpay_settled, Decimal("200.00"))
        self.assertEqual(settlement.cash_settled, Decimal("150.00"))
        self.assertEqual(settlement.closing_balance, Decimal("50.00"))
        self.assertEqual(settlement.cash_settled_to, "Shared Counter")
        self.assertEqual(settlement.notes, "Updated by another admin account")

    def test_daily_settlement_post_updates_when_form_uses_hidden_selected_date(self):
        self.client.force_login(self.user)
        DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=date.today() - timedelta(days=1),
            opening_balance=Decimal("0.00"),
            gpay_settled=Decimal("0.00"),
            cash_settled=Decimal("0.00"),
            expense_amount=Decimal("0.00"),
            closing_balance=Decimal("700.00"),
        )
        settlement = DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=date.today(),
            opening_balance=Decimal("700.00"),
            gpay_settled=Decimal("0.00"),
            cash_settled=Decimal("0.00"),
            expense_amount=Decimal("0.00"),
            closing_balance=Decimal("700.00"),
        )

        response = self.client.post(
            reverse("daily-settlement"),
            {
                "selected_date": date.today().isoformat(),
                "settlement_date": date.today().isoformat(),
                "gpay_settled": "0.00",
                "cash_settled": "200.00",
                "cash_settled_to": "Admin",
                "closing_balance": "0.00",
                "notes": "Updated from settlement page",
            },
        )

        self.assertEqual(response.status_code, 302)
        settlement.refresh_from_db()
        self.assertEqual(settlement.cash_settled, Decimal("200.00"))
        self.assertEqual(settlement.cash_settled_to, "Admin")
        self.assertEqual(settlement.notes, "Updated from settlement page")

    def test_supplier_name_is_shown_on_expense_page(self):
        self.client.force_login(self.user)
        supplier = Supplier.objects.create(
            user=self.user,
            name="City Wholesale",
            contact_person="Mohan",
            phone_number="9876543210",
        )
        ExpenseRecord.objects.create(
            user=self.user,
            title="Stock Purchase",
            supplier=supplier,
            vendor=supplier.name,
            category="Inventory",
            amount=Decimal("5200.00"),
        )

        response = self.client.get(reverse("expense-list"))

        self.assertContains(response, "City Wholesale")
        self.assertNotContains(response, "No expense records available")

    def test_general_expense_without_supplier_is_allowed(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("expense-add"),
            {
                "title": "Electricity Bill",
                "supplier": "",
                "vendor": "TNEB",
                "category": COUNTER_EXPENSE_CATEGORY,
                "amount": "1800.00",
                "transaction_date": date.today().isoformat(),
                "payment_method": "Cash",
                "notes": "Monthly EB payment",
            },
        )

        self.assertEqual(response.status_code, 302)
        record = ExpenseRecord.objects.get(title="Electricity Bill")
        self.assertEqual(record.vendor, "TNEB")
        self.assertIsNone(record.supplier)
        self.assertEqual(record.category, COUNTER_EXPENSE_CATEGORY)

    def test_income_add_page_renders_entry_master_layout(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("income-add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Revenue Details")
        self.assertContains(response, "Classification & Amount")
        self.assertContains(response, "Transaction & Notes")

    def test_expense_add_page_renders_entry_master_layout(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("expense-add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Expense Basics")
        self.assertContains(response, "Supplier & Reference")
        self.assertContains(response, "Amount & Payment")

    def test_expense_list_shows_add_expense_page_button(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("expense-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add Expense Record")
        self.assertNotContains(response, 'data-add-expense-row', html=False)

    def test_expense_list_inline_save_creates_record(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("expense-list"),
            {
                "entry_id": [""],
                "entry_date": [date.today().isoformat()],
                "entry_category": ["Transport"],
                "entry_purpose": ["Auto Fare"],
                "entry_amount": ["354.00"],
                "entry_payment_method": ["Cash"],
            },
        )

        self.assertEqual(response.status_code, 302)
        expense = ExpenseRecord.objects.get(title="Auto Fare")
        self.assertEqual(expense.vendor, "Auto Fare")
        self.assertEqual(expense.category, "Transport")
        self.assertEqual(expense.amount, Decimal("354.00"))

    def test_expense_list_defaults_new_row_to_counter_expense(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("expense-list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["entry_rows"][0]["category"],
            COUNTER_EXPENSE_CATEGORY,
        )
        self.assertContains(response, COUNTER_EXPENSE_CATEGORY)

    def test_daily_settlement_expense_total_counts_only_counter_expense(self):
        self.client.force_login(self.user)
        ExpenseRecord.objects.create(
            user=self.user,
            title="Counter Cash",
            vendor="Front Desk",
            category=COUNTER_EXPENSE_CATEGORY,
            amount=Decimal("200.00"),
            transaction_date=date.today(),
            payment_method=PaymentMethod.CASH,
        )
        ExpenseRecord.objects.create(
            user=self.user,
            title="General Expense",
            vendor="Vendor A",
            category="General",
            amount=Decimal("20000.00"),
            transaction_date=date.today(),
            payment_method=PaymentMethod.CASH,
        )

        expense_list_response = self.client.get(reverse("expense-list"))
        self.assertEqual(expense_list_response.status_code, 200)
        self.assertContains(expense_list_response, COUNTER_EXPENSE_CATEGORY)
        self.assertContains(expense_list_response, "Counter Cash")
        self.assertContains(expense_list_response, "General Expense")

        settlement_response = self.client.get(reverse("daily-settlement"))
        self.assertEqual(settlement_response.status_code, 200)
        self.assertEqual(
            settlement_response.context["autofill_summary"]["expense_amount"],
            Decimal("200.00"),
        )

    def test_expense_list_inline_delete_removes_record(self):
        self.client.force_login(self.user)
        expense = ExpenseRecord.objects.create(
            user=self.user,
            title="Delivery Charge",
            vendor="Delivery Charge",
            category="Transport",
            amount=Decimal("120.00"),
            transaction_date=date.today(),
        )

        response = self.client.post(
            reverse("expense-list"),
            {
                "action": "delete",
                "record_id": str(expense.pk),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ExpenseRecord.objects.filter(pk=expense.pk).exists())

    def test_opening_balance_uses_previous_day_closing_balance(self):
        self.client.force_login(self.user)
        supplier = Supplier.objects.create(
            user=self.user,
            name="Balance Supplier",
            contact_person="Kumar",
            phone_number="9876500002",
        )
        today = date.today()
        yesterday = today - timedelta(days=1)

        IncomeRecord.objects.create(
            user=self.user,
            title="Yesterday Sale",
            source="Store",
            category="Retail",
            amount=Decimal("1000.00"),
            transaction_date=yesterday,
        )
        ExpenseRecord.objects.create(
            user=self.user,
            title="Yesterday Expense",
            supplier=supplier,
            vendor=supplier.name,
            category="Purchase",
            amount=Decimal("250.00"),
            transaction_date=yesterday,
        )
        IncomeRecord.objects.create(
            user=self.user,
            title="Today Sale",
            source="Store",
            category="Retail",
            amount=Decimal("300.00"),
            transaction_date=today,
        )
        ExpenseRecord.objects.create(
            user=self.user,
            title="Today Expense",
            supplier=supplier,
            vendor=supplier.name,
            category="Delivery",
            amount=Decimal("100.00"),
            transaction_date=today,
        )

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.context["opening_balance"], Decimal("750.00"))
        self.assertEqual(response.context["closing_balance"], Decimal("0.00"))

    def test_dashboard_can_be_filtered_by_selected_date(self):
        self.client.force_login(self.user)
        target_date = date.today() - timedelta(days=2)
        later_date = target_date + timedelta(days=1)

        IncomeRecord.objects.create(
            user=self.user,
            title="Target Day Income",
            source="Store",
            category="Retail",
            amount=Decimal("1200.00"),
            transaction_date=target_date,
        )
        ExpenseRecord.objects.create(
            user=self.user,
            title="Target Day Expense",
            vendor="Supplier A",
            category="Purchase",
            amount=Decimal("300.00"),
            transaction_date=target_date,
        )
        IncomeRecord.objects.create(
            user=self.user,
            title="Later Income",
            source="Store",
            category="Retail",
            amount=Decimal("900.00"),
            transaction_date=later_date,
        )

        response = self.client.get(
            reverse("dashboard"),
            {"as_of_date": target_date.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_date"], target_date)
        self.assertEqual(response.context["income_total"], Decimal("0.00"))
        self.assertEqual(response.context["expense_total"], Decimal("300.00"))
        self.assertEqual(response.context["selected_income"], Decimal("0.00"))
        self.assertEqual(response.context["selected_expense"], Decimal("300.00"))
        self.assertEqual(response.context["selected_day_balance"], Decimal("0.00"))
        self.assertContains(response, target_date.isoformat())

    def test_dashboard_uses_daily_settlement_balances_when_available(self):
        self.client.force_login(self.user)
        target_date = date.today()
        IncomeRecord.objects.create(
            user=self.user,
            title="Target Day Income",
            source="Store",
            category="Retail",
            amount=Decimal("2000.00"),
            transaction_date=target_date,
        )
        ExpenseRecord.objects.create(
            user=self.user,
            title="Target Day Expense",
            vendor="Supplier A",
            category="Purchase",
            amount=Decimal("1000.00"),
            transaction_date=target_date,
        )
        DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=target_date,
            opening_balance=Decimal("1500.00"),
            gpay_settled=Decimal("700.00"),
            cash_settled=Decimal("500.00"),
            expense_amount=Decimal("1000.00"),
            closing_balance=Decimal("2200.00"),
        )

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["income_total"], Decimal("2900.00"))
        self.assertEqual(response.context["selected_income"], Decimal("2900.00"))
        self.assertEqual(response.context["opening_balance"], Decimal("1500.00"))
        self.assertEqual(response.context["closing_balance"], Decimal("2200.00"))
        self.assertEqual(response.context["selected_day_balance"], Decimal("2900.00"))
        self.assertEqual(
            response.context["dashboard_settlement"].settlement_date,
            target_date,
        )

    def test_supplier_form_requires_name_contact_person_and_phone(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("supplier-add"),
            {
                "name": "",
                "contact_person": "",
                "phone_number": "",
                "email": "",
                "address": "",
                "gstin_number": "",
                "fssai_number": "",
                "pan_number": "",
                "credit_terms": "",
                "opening_balance": "0.00",
                "bank_name": "",
                "account_number": "",
                "ifsc_code": "",
                "status": "Active",
                "notes": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertFormError(form, "name", "This field is required.")
        self.assertFormError(form, "contact_person", "This field is required.")
        self.assertFormError(form, "phone_number", "This field is required.")

    def test_supplier_code_is_generated_on_create(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("supplier-add"),
            {
                "name": "Metro Traders",
                "contact_person": "Ramesh",
                "phone_number": "9998887776",
                "email": "",
                "address": "",
                "gstin_number": "",
                "fssai_number": "",
                "pan_number": "",
                "credit_terms": "",
                "opening_balance": "0.00",
                "bank_name": "",
                "account_number": "",
                "ifsc_code": "",
                "status": "Active",
                "notes": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        supplier = Supplier.objects.get(name="Metro Traders")
        self.assertEqual(supplier.contact_person, "Ramesh")
        self.assertEqual(supplier.phone_number, "9998887776")
        self.assertEqual(supplier.supplier_code, f"SUP-{supplier.pk:04d}")

    def test_sync_suppliers_from_rows_creates_supplier_from_source_data(self):
        stats = sync_suppliers_from_rows(
            [
                SimpleNamespace(
                    SourceSupplierNo=112,
                    SupplierName="A1 Oil",
                    AddressLine1="Salem",
                    AddressLine2="",
                    AddressLine3="",
                    AddressLine4="",
                    PostalCode="637408",
                    PhoneNumber="9003932582",
                    EmailAddress="",
                    GSTNumber="",
                )
            ],
            self.user,
        )

        self.assertEqual(stats.fetched_count, 1)
        self.assertEqual(stats.inserted_count, 1)
        self.assertEqual(stats.updated_count, 0)
        supplier = Supplier.objects.get(user=self.user, source_supplier_no=112)
        self.assertEqual(supplier.name, "A1 Oil")
        self.assertEqual(supplier.contact_person, "A1 Oil")
        self.assertEqual(supplier.phone_number, "9003932582")
        self.assertEqual(supplier.address, "Salem, 637408")

    def test_sync_suppliers_from_rows_updates_existing_shared_role_supplier(self):
        peer_admin = get_user_model().objects.create_superuser(
            username="peer_admin_sync",
            password="PeerPass123!",
        )
        supplier = Supplier.objects.create(
            user=peer_admin,
            name="Kaleel &co",
            contact_person="Kaleel &co",
            phone_number="Not Provided",
        )

        stats = sync_suppliers_from_rows(
            [
                SimpleNamespace(
                    SourceSupplierNo=125,
                    SupplierName="Kaleel &co",
                    AddressLine1="Salem",
                    AddressLine2="",
                    AddressLine3="",
                    AddressLine4="",
                    PostalCode="",
                    PhoneNumber="9843083300",
                    EmailAddress="",
                    GSTNumber="33ABCDE1234F1Z5",
                )
            ],
            self.admin_user,
        )

        self.assertEqual(stats.fetched_count, 1)
        self.assertEqual(stats.inserted_count, 0)
        self.assertEqual(stats.updated_count, 1)
        self.assertEqual(
            Supplier.objects.filter(name="Kaleel &co", user__is_superuser=True).count(),
            1,
        )
        supplier.refresh_from_db()
        self.assertEqual(supplier.source_supplier_no, 125)
        self.assertEqual(supplier.phone_number, "9843083300")
        self.assertEqual(supplier.address, "Salem")
        self.assertEqual(supplier.gstin_number, "33ABCDE1234F1Z5")

    def test_supplier_list_shows_sync_button(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("supplier-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sync Suppliers")

    def test_supplier_list_includes_mobile_friendly_table_labels(self):
        self.client.force_login(self.user)
        Supplier.objects.create(
            user=self.user,
            name="Mobile Supplier",
            contact_person="Selvi",
            phone_number="9876500099",
        )

        response = self.client.get(reverse("supplier-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "supplier-data-table")
        self.assertContains(response, 'data-label="Supplier Name"', html=False)
        self.assertContains(response, 'data-label="Phone Number"', html=False)

    @patch("tracker.views.sync_suppliers_from_sqlserver")
    def test_supplier_list_sync_button_triggers_supplier_sync(self, sync_mock):
        self.client.force_login(self.user)
        sync_mock.return_value = SupplierSyncStats(
            fetched_count=5,
            inserted_count=3,
            updated_count=1,
            skipped_count=1,
        )

        response = self.client.post(
            reverse("supplier-list"),
            {
                "action": "sync_suppliers",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("supplier-list"))
        sync_mock.assert_called_once_with(self.user)

    def test_supplier_create_redirects_back_to_next_url(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("supplier-add"),
            {
                "name": "Return Flow Supplier",
                "contact_person": "Saravanan",
                "phone_number": "9998887775",
                "email": "",
                "address": "",
                "gstin_number": "",
                "fssai_number": "",
                "pan_number": "",
                "credit_terms": "",
                "opening_balance": "0.00",
                "bank_name": "",
                "account_number": "",
                "ifsc_code": "",
                "status": "Active",
                "notes": "",
                "next": reverse("purchase-add"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("purchase-add"))

    def test_purchase_add_page_shows_supplier_setup_actions_when_no_suppliers_exist(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("purchase-add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add First Supplier")
        self.assertContains(
            response,
            f'{reverse("supplier-add")}?next={reverse("purchase-add")}',
        )
        self.assertContains(response, "No supplier details are saved in PostgreSQL yet.")

    def test_expense_add_page_shows_supplier_setup_actions_when_no_suppliers_exist(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("expense-add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add First Supplier")
        self.assertContains(
            response,
            f'{reverse("supplier-add")}?next={reverse("expense-add")}',
        )
        self.assertContains(response, "No supplier details are saved in PostgreSQL yet.")

    def test_income_list_paginates_in_batches_of_50(self):
        self.client.force_login(self.user)

        for index in range(55):
            IncomeRecord.objects.create(
                user=self.user,
                title=f"Income {index}",
                source="Store",
                category="Sales",
                amount=Decimal("100.00"),
            )

        first_page = self.client.get(reverse("income-list"))
        second_page = self.client.get(reverse("income-list"), {"page": 2})

        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(first_page.context["page_obj"].paginator.per_page, 50)
        self.assertTrue(first_page.context["is_paginated"])
        self.assertEqual(len(first_page.context["records"]), 50)
        self.assertEqual(first_page.context["next_page_url"], f"{reverse('income-list')}?page=2")
        self.assertEqual(len(second_page.context["records"]), 5)

    def test_income_list_includes_daily_settlement_income_records(self):
        self.client.force_login(self.user)
        IncomeRecord.objects.create(
            user=self.user,
            title="Manual Income",
            source="Counter Sale",
            category="Retail",
            amount=Decimal("250.00"),
        )
        DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=date.today(),
            opening_balance=Decimal("0.00"),
            gpay_settled=Decimal("700.00"),
            cash_settled=Decimal("300.00"),
            expense_amount=Decimal("50.00"),
            closing_balance=Decimal("100.00"),
        )

        response = self.client.get(reverse("income-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Daily Settlement Income")
        self.assertContains(response, "Cash Rs. 450.00 | GPay Rs. 700.00")
        self.assertContains(response, "Auto added from Daily Settlement")
        self.assertEqual(response.context["page_count"], 2)
        self.assertEqual(response.context["page_total"], Decimal("1400.00"))

    def test_expense_list_paginates_in_batches_of_50(self):
        self.client.force_login(self.user)

        for index in range(53):
            ExpenseRecord.objects.create(
                user=self.user,
                title=f"Expense {index}",
                vendor=f"Vendor {index}",
                category="General",
                amount=Decimal("50.00"),
            )

        first_page = self.client.get(reverse("expense-list"))
        second_page = self.client.get(reverse("expense-list"), {"page": 2})

        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(first_page.context["page_obj"].paginator.per_page, 50)
        self.assertTrue(first_page.context["is_paginated"])
        self.assertEqual(len(first_page.context["records"]), 50)
        self.assertEqual(first_page.context["next_page_url"], f"{reverse('expense-list')}?page=2")
        self.assertEqual(len(second_page.context["records"]), 3)

    def test_daily_settlement_page_prefills_from_previous_closing_and_today_records(self):
        self.client.force_login(self.user)
        yesterday = date.today() - timedelta(days=1)
        DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=yesterday,
            opening_balance=Decimal("500.00"),
            gpay_settled=Decimal("0.00"),
            cash_settled=Decimal("0.00"),
            expense_amount=Decimal("0.00"),
            closing_balance=Decimal("741.00"),
        )
        IncomeRecord.objects.create(
            user=self.user,
            title="UPI Sales",
            source="Store",
            category="Retail",
            amount=Decimal("900.00"),
            transaction_date=date.today(),
            payment_method=PaymentMethod.UPI,
        )
        IncomeRecord.objects.create(
            user=self.user,
            title="Cash Sales",
            source="Store",
            category="Retail",
            amount=Decimal("600.00"),
            transaction_date=date.today(),
            payment_method=PaymentMethod.CASH,
        )
        ExpenseRecord.objects.create(
            user=self.user,
            title="Today Expense",
            vendor="Vendor A",
            category=COUNTER_EXPENSE_CATEGORY,
            amount=Decimal("125.00"),
            transaction_date=date.today(),
        )

        response = self.client.get(reverse("daily-settlement"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "summary-sections-grid")
        self.assertEqual(
            response.context["autofill_summary"]["opening_balance"],
            Decimal("741.00"),
        )
        self.assertEqual(
            response.context["autofill_summary"]["gpay_settled"],
            Decimal("0.00"),
        )
        self.assertEqual(
            response.context["autofill_summary"]["sales_ledger_cash"],
            Decimal("0.00"),
        )
        self.assertEqual(
            response.context["autofill_summary"]["expense_amount"],
            Decimal("125.00"),
        )
        self.assertEqual(
            response.context["settlement_preview"]["cash_in_hand"],
            Decimal("616.00"),
        )
        self.assertEqual(
            response.context["settlement_preview"]["closing_balance"],
            Decimal("616.00"),
        )
        self.assertContains(response, "Daily Cash Settlement")
        self.assertNotContains(response, 'name="opening_balance"', html=False)
        self.assertNotContains(response, 'name="expense_amount"', html=False)

    def test_daily_settlement_allows_opening_balance_edit_for_admin_only(self):
        self.client.force_login(self.admin_user)
        yesterday = date.today() - timedelta(days=1)
        DailyCashSettlement.objects.create(
            user=self.admin_user,
            settlement_date=yesterday,
            opening_balance=Decimal("500.00"),
            gpay_settled=Decimal("0.00"),
            cash_settled=Decimal("0.00"),
            expense_amount=Decimal("0.00"),
            closing_balance=Decimal("741.00"),
        )

        response = self.client.get(reverse("daily-settlement"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="opening_balance"', html=False)
        self.assertTrue(response.context["can_edit_opening_balance"])

        today_value = date.today().isoformat()
        response = self.client.post(
            reverse("daily-settlement"),
            {
                "settlement_date": today_value,
                "opening_balance": "900.00",
                "gpay_settled": "0.00",
                "cash_settled": "200.00",
                "cash_denominations": "",
                "cash_settled_to": "Admin Counter",
                "closing_balance": "0.00",
                "notes": "Adjusted opening balance",
                "selected_date": today_value,
            },
        )

        self.assertEqual(response.status_code, 302)
        settlement = DailyCashSettlement.objects.get(
            user=self.admin_user,
            settlement_date=date.today(),
        )
        self.assertEqual(settlement.opening_balance, Decimal("900.00"))
        self.assertEqual(settlement.closing_balance, Decimal("700.00"))
        self.assertEqual(settlement.actual_sales, Decimal("0.00"))
        self.assertEqual(settlement.notes, "Adjusted opening balance")

    def test_daily_settlement_prefills_from_sales_ledger_for_selected_date(self):
        self.client.force_login(self.user)
        yesterday = date.today() - timedelta(days=1)
        DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=yesterday,
            opening_balance=Decimal("500.00"),
            gpay_settled=Decimal("0.00"),
            cash_settled=Decimal("0.00"),
            expense_amount=Decimal("0.00"),
            closing_balance=Decimal("700.00"),
        )
        SalesLedgerRecord.objects.create(
            source_sale_no=4001,
            bill_no="SAL-4001",
            sale_date=date.today(),
            customer_name="Card Customer",
            net_amount=Decimal("200.00"),
            received_amount=Decimal("200.00"),
            balance_amount=Decimal("0.00"),
            payment_mode=SalesPaymentMode.CARD,
        )
        SalesLedgerRecord.objects.create(
            source_sale_no=4002,
            bill_no="SAL-4002",
            sale_date=date.today(),
            customer_name="Split Customer",
            net_amount=Decimal("450.00"),
            received_amount=Decimal("450.00"),
            balance_amount=Decimal("0.00"),
            split_cash_amount=Decimal("300.00"),
            split_card_amount=Decimal("150.00"),
            payment_mode=SalesPaymentMode.CASH,
        )
        SalesLedgerRecord.objects.create(
            source_sale_no=4003,
            bill_no="SAL-4003",
            sale_date=date.today(),
            customer_name="Cash Customer",
            net_amount=Decimal("100.00"),
            received_amount=Decimal("120.00"),
            balance_amount=Decimal("0.00"),
            payment_mode=SalesPaymentMode.CASH,
        )
        ExpenseRecord.objects.create(
            user=self.user,
            title="Today Expense",
            vendor="Vendor A",
            category=COUNTER_EXPENSE_CATEGORY,
            amount=Decimal("50.00"),
            transaction_date=date.today(),
        )

        response = self.client.get(reverse("daily-settlement"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["autofill_summary"]["settlement_source"], "sales")
        self.assertEqual(response.context["autofill_summary"]["sales_count"], 3)
        self.assertEqual(response.context["autofill_summary"]["manual_split_count"], 1)
        self.assertEqual(response.context["autofill_summary"]["gpay_settled"], Decimal("350.00"))
        self.assertEqual(response.context["autofill_summary"]["sales_ledger_cash"], Decimal("400.00"))
        self.assertEqual(response.context["settlement_preview"]["cash_in_hand"], Decimal("1050.00"))
        self.assertEqual(response.context["settlement_preview"]["actual_sales"], Decimal("750.00"))
        self.assertContains(response, "Sales Ledger Cash")
        self.assertContains(response, "Cash Denomination Total")
        self.assertContains(response, "Cash Denomination")

    def test_daily_settlement_shows_credit_bill_list_for_selected_date(self):
        self.client.force_login(self.user)
        SalesLedgerRecord.objects.create(
            source_sale_no=4501,
            bill_no="CREDIT-4501",
            sale_date=date.today(),
            customer_name="Credit Party",
            net_amount=Decimal("1500.00"),
            received_amount=Decimal("300.00"),
            balance_amount=Decimal("1200.00"),
            payment_mode=SalesPaymentMode.CREDIT,
        )
        SalesLedgerRecord.objects.create(
            source_sale_no=4502,
            bill_no="CASH-4502",
            sale_date=date.today(),
            customer_name="Cash Party",
            net_amount=Decimal("500.00"),
            received_amount=Decimal("500.00"),
            balance_amount=Decimal("0.00"),
            payment_mode=SalesPaymentMode.CASH,
        )

        response = self.client.get(reverse("daily-settlement"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Notes & Credit Bills")
        self.assertContains(response, "CREDIT-4501")
        self.assertContains(response, "Credit Party")
        self.assertEqual(response.context["credit_bill_count"], 1)
        self.assertEqual(response.context["credit_bill_total"], Decimal("1200.00"))

    def test_daily_settlement_hides_split_paid_credit_bill(self):
        self.client.force_login(self.user)
        SalesLedgerRecord.objects.create(
            source_sale_no=4503,
            bill_no="SPLIT-4503",
            sale_date=date.today(),
            customer_name="Split Paid Party",
            net_amount=Decimal("1560.00"),
            received_amount=Decimal("300.00"),
            balance_amount=Decimal("1260.00"),
            split_cash_amount=Decimal("1560.00"),
            split_card_amount=Decimal("0.00"),
            payment_mode=SalesPaymentMode.CREDIT,
        )

        response = self.client.get(reverse("daily-settlement"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "SPLIT-4503")
        self.assertEqual(response.context["credit_bill_count"], 0)
        self.assertEqual(response.context["credit_bill_total"], Decimal("0.00"))

    def test_daily_settlement_post_creates_record_and_computes_totals(self):
        self.client.force_login(self.user)
        yesterday = date.today() - timedelta(days=1)
        DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=yesterday,
            opening_balance=Decimal("500.00"),
            gpay_settled=Decimal("0.00"),
            cash_settled=Decimal("0.00"),
            expense_amount=Decimal("0.00"),
            closing_balance=Decimal("741.00"),
        )
        ExpenseRecord.objects.create(
            user=self.user,
            title="Today Expense",
            vendor="Vendor A",
            category=COUNTER_EXPENSE_CATEGORY,
            amount=Decimal("200.00"),
            transaction_date=date.today(),
        )
        today_value = date.today().isoformat()

        response = self.client.post(
            reverse("daily-settlement"),
            {
                "settlement_date": today_value,
                "opening_balance": "9999.00",
                "gpay_settled": "1000.00",
                "cash_settled": "500.00",
                "cash_settled_to": "Admin Counter",
                "expense_amount": "9999.00",
                "closing_balance": "300.00",
                "notes": "Shift handover complete",
                "selected_date": today_value,
            },
        )

        self.assertEqual(response.status_code, 302)
        settlement = DailyCashSettlement.objects.get(
            user=self.user,
            settlement_date=date.today(),
        )
        self.assertEqual(settlement.cash_settled_to, "Admin Counter")
        self.assertEqual(settlement.opening_balance, Decimal("741.00"))
        self.assertEqual(settlement.expense_amount, Decimal("200.00"))
        self.assertEqual(settlement.closing_balance, Decimal("41.00"))
        self.assertEqual(settlement.total_amount, Decimal("1741.00"))
        self.assertEqual(settlement.actual_sales, Decimal("1000.00"))
        self.assertEqual(settlement.notes, "Shift handover complete")

    def test_daily_settlement_post_uses_cash_denomination_total(self):
        self.client.force_login(self.user)
        yesterday = date.today() - timedelta(days=1)
        DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=yesterday,
            opening_balance=Decimal("500.00"),
            gpay_settled=Decimal("0.00"),
            cash_settled=Decimal("0.00"),
            expense_amount=Decimal("0.00"),
            closing_balance=Decimal("741.00"),
        )
        ExpenseRecord.objects.create(
            user=self.user,
            title="Today Expense",
            vendor="Vendor A",
            category=COUNTER_EXPENSE_CATEGORY,
            amount=Decimal("200.00"),
            transaction_date=date.today(),
        )
        today_value = date.today().isoformat()

        response = self.client.post(
            reverse("daily-settlement"),
            {
                "settlement_date": today_value,
                "gpay_settled": "1000.00",
                "cash_settled": "1.00",
                "cash_denominations": json.dumps({"500": 1, "200": 2, "50": 1}),
                "cash_settled_to": "Admin Counter",
                "closing_balance": "300.00",
                "notes": "Saved with denomination popup",
                "selected_date": today_value,
            },
        )

        self.assertEqual(response.status_code, 302)
        settlement = DailyCashSettlement.objects.get(
            user=self.user,
            settlement_date=date.today(),
        )
        self.assertEqual(settlement.cash_settled, Decimal("1.00"))
        self.assertEqual(
            settlement.cash_denominations,
            {"500": 1, "200": 2, "50": 1},
        )
        self.assertEqual(settlement.cash_denomination_total, Decimal("950.00"))
        self.assertEqual(settlement.cash_in_hand, Decimal("541.00"))
        self.assertEqual(settlement.cash_difference, Decimal("409.00"))
        self.assertEqual(settlement.closing_balance, Decimal("540.00"))
        self.assertEqual(settlement.total_amount, Decimal("1741.00"))

    def test_daily_settlement_post_keeps_gpay_equal_to_sales_ledger_value(self):
        self.client.force_login(self.user)
        yesterday = date.today() - timedelta(days=1)
        DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=yesterday,
            opening_balance=Decimal("500.00"),
            gpay_settled=Decimal("0.00"),
            cash_settled=Decimal("0.00"),
            expense_amount=Decimal("0.00"),
            closing_balance=Decimal("741.00"),
        )
        SalesLedgerRecord.objects.create(
            source_sale_no=5001,
            bill_no="SAL-5001",
            sale_date=date.today(),
            customer_name="Card Customer",
            net_amount=Decimal("275.00"),
            received_amount=Decimal("275.00"),
            balance_amount=Decimal("0.00"),
            payment_mode=SalesPaymentMode.CARD,
        )

        today_value = date.today().isoformat()
        response = self.client.post(
            reverse("daily-settlement"),
            {
                "settlement_date": today_value,
                "gpay_settled": "9999.00",
                "cash_settled": "0.00",
                "cash_denominations": "",
                "cash_settled_to": "Admin Counter",
                "closing_balance": "100.00",
                "notes": "Should use sales ledger gpay",
                "selected_date": today_value,
            },
        )

        self.assertEqual(response.status_code, 302)
        settlement = DailyCashSettlement.objects.get(
            user=self.user,
            settlement_date=date.today(),
        )
        self.assertEqual(settlement.gpay_settled, Decimal("275.00"))

    def test_daily_settlement_refreshes_sales_values_after_split_edit(self):
        self.client.force_login(self.user)
        target_date = date.today()
        sales_record = SalesLedgerRecord.objects.create(
            source_sale_no=5002,
            bill_no="SAL-5002",
            sale_date=target_date,
            customer_name="Split Update Customer",
            net_amount=Decimal("450.00"),
            received_amount=Decimal("450.00"),
            balance_amount=Decimal("0.00"),
            payment_mode=SalesPaymentMode.CARD,
        )
        settlement = DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=target_date,
            opening_balance=Decimal("100.00"),
            gpay_settled=Decimal("450.00"),
            cash_settled=Decimal("50.00"),
            cash_settled_to="Counter A",
            expense_amount=Decimal("0.00"),
            closing_balance=Decimal("50.00"),
            notes="Saved before sales edit",
        )

        sales_record.split_cash_amount = Decimal("450.00")
        sales_record.split_card_amount = Decimal("0.00")
        sales_record.save(update_fields=["split_cash_amount", "split_card_amount"])

        response = self.client.get(
            reverse("daily-settlement"),
            {
                "selected_date": target_date.isoformat(),
                "entry_date": target_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_settlement"].pk, settlement.pk)
        self.assertEqual(
            response.context["settlement_preview"]["sales_ledger_cash"],
            Decimal("450.00"),
        )
        self.assertEqual(
            response.context["settlement_preview"]["gpay_settled"],
            Decimal("0.00"),
        )
        self.assertEqual(
            response.context["settlement_preview"]["closing_balance"],
            Decimal("500.00"),
        )
        self.assertContains(response, "Saved before sales edit")

        response = self.client.post(
            reverse("daily-settlement"),
            {
                "settlement_date": target_date.isoformat(),
                "gpay_settled": "0.00",
                "cash_settled": "50.00",
                "cash_denominations": "",
                "cash_settled_to": "Counter A",
                "closing_balance": "0.00",
                "notes": "Updated after sales split edit",
                "selected_date": target_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 302)
        settlement.refresh_from_db()
        self.assertEqual(settlement.gpay_settled, Decimal("0.00"))
        self.assertEqual(settlement.closing_balance, Decimal("500.00"))
        self.assertEqual(settlement.actual_sales, Decimal("450.00"))
        self.assertEqual(settlement.notes, "Updated after sales split edit")

    def test_daily_settlement_single_date_filter_shows_only_matching_date(self):
        self.client.force_login(self.user)
        target_date = date.today() - timedelta(days=2)
        other_date = date.today()
        DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=target_date,
            opening_balance=Decimal("100.00"),
            gpay_settled=Decimal("300.00"),
            cash_settled=Decimal("200.00"),
            cash_settled_to="Target Counter",
            expense_amount=Decimal("50.00"),
            closing_balance=Decimal("150.00"),
        )
        DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=other_date,
            opening_balance=Decimal("200.00"),
            gpay_settled=Decimal("400.00"),
            cash_settled=Decimal("250.00"),
            cash_settled_to="Other Counter",
            expense_amount=Decimal("60.00"),
            closing_balance=Decimal("180.00"),
        )

        response = self.client.get(
            reverse("daily-settlement"),
            {
                "selected_date": target_date.isoformat(),
                "entry_date": target_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["settlement_count"], 1)
        self.assertContains(response, target_date.strftime("%d-%m-%Y"))
        self.assertContains(response, "Target Counter")
        self.assertNotContains(response, "Other Counter")

    def test_daily_settlement_filter_restores_saved_counts_and_snapshot(self):
        self.client.force_login(self.user)
        target_date = date.today() - timedelta(days=1)
        settlement = DailyCashSettlement.objects.create(
            user=self.user,
            settlement_date=target_date,
            opening_balance=Decimal("100.00"),
            gpay_settled=Decimal("250.00"),
            cash_settled=Decimal("200.00"),
            cash_denominations={"500": 1, "200": 2},
            cash_settled_to="Night Admin",
            expense_amount=Decimal("50.00"),
            closing_balance=Decimal("650.00"),
            notes="Saved settlement snapshot",
        )
        ExpenseRecord.objects.create(
            user=self.user,
            title="Late Expense",
            vendor="Vendor A",
            category="General",
            amount=Decimal("25.00"),
            transaction_date=target_date,
        )

        response = self.client.get(
            reverse("daily-settlement"),
            {
                "selected_date": target_date.isoformat(),
                "entry_date": target_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["settlement_preview"]["cash_in_hand"],
            Decimal("900.00"),
        )
        self.assertEqual(
            response.context["settlement_preview"]["expense_amount"],
            Decimal("0.00"),
        )
        self.assertEqual(response.context["cash_denomination_total"], Decimal("900.00"))
        self.assertEqual(response.context["cash_difference"], Decimal("0.00"))
        self.assertEqual(response.context["selected_settlement"].pk, settlement.pk)
        denomination_counts = {
            row["value"]: row["count"]
            for row in response.context["cash_denomination_rows"]
        }
        self.assertEqual(denomination_counts[500], 1)
        self.assertEqual(denomination_counts[200], 2)
        self.assertContains(response, "Night Admin")
        self.assertContains(response, "Saved settlement snapshot")

    def test_regular_user_cannot_open_user_management_page(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("user-list"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))

    def test_admin_can_create_and_edit_managed_user(self):
        self.client.force_login(self.admin_user)

        create_response = self.client.post(
            reverse("user-add"),
            {
                "username": "StoreAdminOne",
                "master_name": "STOREADMINONE",
                "role": "store_admin",
                "status": "active",
                "password1": "TempPass123!",
                "password2": "TempPass123!",
            },
        )

        self.assertEqual(create_response.status_code, 302)
        managed_user = get_user_model().objects.get(username="StoreAdminOne")
        self.assertTrue(managed_user.is_staff)
        self.assertFalse(managed_user.is_superuser)
        self.assertTrue(managed_user.is_active)
        self.assertEqual(managed_user.account_profile.master_name, "STOREADMINONE")

        edit_response = self.client.post(
            reverse("user-edit", args=[managed_user.pk]),
            {
                "username": "StoreAdminOne",
                "master_name": "STOREADMINONE",
                "role": "staff",
                "status": "inactive",
                "password1": "",
                "password2": "",
            },
        )

        self.assertEqual(edit_response.status_code, 302)
        managed_user.refresh_from_db()
        managed_profile = UserAccountProfile.objects.get(user=managed_user)
        self.assertFalse(managed_user.is_staff)
        self.assertFalse(managed_user.is_superuser)
        self.assertFalse(managed_user.is_active)
        self.assertEqual(managed_profile.master_name, "STOREADMINONE")

    @patch("tracker.views.sync_users_from_sqlserver")
    def test_admin_can_trigger_user_sync_from_user_list(self, sync_mock):
        self.client.force_login(self.admin_user)
        sync_mock.return_value = UserSyncStats(
            fetched_count=4,
            inserted_count=2,
            refreshed_count=0,
            skipped_count=2,
        )

        response = self.client.post(reverse("user-list"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("user-list"))
        sync_mock.assert_called_once_with()

    def test_user_list_shows_sync_button_for_admin(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("user-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sync Users From SQL Server")

    def test_user_list_includes_mobile_friendly_table_labels(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("user-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "user-data-table")
        self.assertContains(response, 'data-label="User Name"', html=False)
        self.assertContains(response, 'data-label="Action"', html=False)

    def test_permission_settings_page_renders_for_admin(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("permission-settings"),
            {"user": self.user.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Permission Settings")
        self.assertContains(response, "Sidebar And Page Permissions")
        self.assertContains(response, "Dashboard")
        self.assertContains(response, "Reports")
        self.assertTrue(
            UserModulePermission.objects.filter(user=self.user).exists()
        )

    def test_navigation_shows_separate_reconciliation_when_reports_are_allowed(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Separate Reconciliation")
        self.assertContains(response, reverse("reconciliation"))

    def test_admin_can_update_user_module_permission_from_settings_page(self):
        permission_record = UserModulePermission.objects.create(user=self.user)
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("permission-settings"),
            data=json.dumps(
                {
                    "user": self.user.pk,
                    "field": "allow_sales",
                    "value": False,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ok"], True)
        permission_record.refresh_from_db()
        self.assertFalse(permission_record.allow_sales)

    def test_dashboard_hides_restricted_module_links_and_actions(self):
        UserModulePermission.objects.update_or_create(
            user=self.user,
            defaults={
                "allow_dashboard": True,
                "allow_sales": False,
                "allow_daily_settlement": False,
                "allow_income": False,
                "allow_purchases": False,
                "allow_suppliers": False,
                "allow_expenses": False,
                "allow_reports": False,
            },
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("sales-list"))
        self.assertNotContains(response, reverse("income-add"))
        self.assertNotContains(response, reverse("daily-settlement"))
        self.assertNotContains(response, reverse("expense-add"))
        self.assertNotContains(response, reverse("supplier-add"))
        self.assertNotContains(response, reverse("reports"))
        self.assertNotContains(response, reverse("reconciliation"))
        self.assertContains(response, "No quick actions are enabled for this user.")

    def test_blocked_module_redirects_to_first_accessible_page(self):
        UserModulePermission.objects.update_or_create(
            user=self.user,
            defaults={
                "allow_dashboard": True,
                "allow_sales": False,
                "allow_daily_settlement": False,
                "allow_income": False,
                "allow_purchases": False,
                "allow_suppliers": False,
                "allow_expenses": False,
                "allow_reports": False,
            },
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("sales-list"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))

    def test_home_redirect_uses_first_enabled_module_for_restricted_user(self):
        UserModulePermission.objects.update_or_create(
            user=self.user,
            defaults={
                "allow_dashboard": False,
                "allow_sales": False,
                "allow_daily_settlement": True,
                "allow_income": False,
                "allow_purchases": False,
                "allow_suppliers": False,
                "allow_expenses": False,
                "allow_reports": False,
            },
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("daily-settlement"))

    def test_blocked_module_redirects_to_access_denied_when_no_pages_are_enabled(self):
        UserModulePermission.objects.update_or_create(
            user=self.user,
            defaults={
                "allow_dashboard": False,
                "allow_sales": False,
                "allow_daily_settlement": False,
                "allow_income": False,
                "allow_purchases": False,
                "allow_suppliers": False,
                "allow_expenses": False,
                "allow_reports": False,
            },
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("sales-list"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("access-denied"))

        denied_response = self.client.get(reverse("access-denied"))
        self.assertEqual(denied_response.status_code, 200)
        self.assertContains(denied_response, "Access Denied")

    def test_purchase_record_pending_amount_is_calculated(self):
        self.client.force_login(self.user)
        supplier = Supplier.objects.create(
            user=self.user,
            name="Purchase Supplier",
            contact_person="Bala",
            phone_number="9000000001",
        )

        purchase = PurchaseRecord.objects.create(
            user=self.user,
            supplier=supplier,
            supplier_name="",
            purchase_type="Type 2",
            invoice_number="INV-1001",
            total_amount=Decimal("1000.00"),
            paid_amount=Decimal("250.00"),
        )

        self.assertEqual(purchase.supplier_name, "Purchase Supplier")
        self.assertEqual(purchase.pending_amount, Decimal("750.00"))

    def test_purchase_list_shows_pending_amount(self):
        self.client.force_login(self.user)
        PurchaseRecord.objects.create(
            user=self.user,
            supplier_name="Supplier A",
            purchase_type="Type 1",
            invoice_number="INV-2001",
            total_amount=Decimal("500.00"),
            paid_amount=Decimal("200.00"),
            pending_amount=Decimal("300.00"),
        )

        response = self.client.get(reverse("purchase-list"))

        self.assertContains(response, "Supplier A")
        self.assertContains(response, "INV-2001")
        self.assertContains(response, "300.00")
        self.assertContains(response, self.user.username)

    def test_purchase_create_page_saves_manual_purchase_with_file(self):
        self.client.force_login(self.user)
        media_root = tempfile.mkdtemp()

        try:
            with override_settings(MEDIA_ROOT=media_root):
                response = self.client.post(
                    reverse("purchase-add"),
                    {
                        "supplier": "",
                        "supplier_name": "Manual Supplier",
                        "purchase_type": "Local",
                        "invoice_number": "INV-9001",
                        "total_amount": "1000.00",
                        "paid_amount": "400.00",
                        "notes": "Manual purchase note",
                        "attachment": SimpleUploadedFile(
                            "invoice.txt",
                            b"purchase invoice file",
                            content_type="text/plain",
                        ),
                    },
                )
        finally:
            shutil.rmtree(media_root, ignore_errors=True)

        self.assertEqual(response.status_code, 302)
        purchase = PurchaseRecord.objects.get(invoice_number="INV-9001")
        expense = ExpenseRecord.objects.get(
            user=self.user,
            source_reference=f"PURCHASE:{purchase.source_reference}",
        )
        self.assertEqual(purchase.supplier_name, "Manual Supplier")
        self.assertEqual(purchase.transaction_date, date.today())
        self.assertEqual(purchase.pending_amount, Decimal("600.00"))
        self.assertTrue(purchase.source_reference.startswith("MANUAL:"))
        self.assertIn("invoice", purchase.attachment.name)
        self.assertEqual(purchase.payments.count(), 1)
        self.assertEqual(purchase.payments.first().amount, Decimal("400.00"))
        self.assertEqual(expense.title, "Purchase - INV-9001")
        self.assertEqual(expense.vendor, "Manual Supplier")
        self.assertIsNone(expense.supplier)
        self.assertEqual(expense.category, "Purchase")
        self.assertEqual(expense.amount, Decimal("1000.00"))
        self.assertEqual(expense.transaction_date, date.today())
        self.assertEqual(expense.payment_method, PaymentMethod.OTHER)
        self.assertIn("Invoice No: INV-9001", expense.notes)
        self.assertIn("Purchase Notes: Manual purchase note", expense.notes)

    def test_purchase_create_can_return_invoice_pdf_immediately(self):
        self.client.force_login(self.user)
        media_root = tempfile.mkdtemp()

        try:
            with override_settings(MEDIA_ROOT=media_root):
                response = self.client.post(
                    reverse("purchase-add"),
                    {
                        "supplier": "",
                        "supplier_name": "Immediate Supplier",
                        "purchase_type": "Wholesale",
                        "invoice_number": "INV-9100",
                        "total_amount": "1200.00",
                        "paid_amount": "300.00",
                        "notes": "Invoice download test",
                        "_download_invoice": "1",
                    },
                )
        finally:
            shutil.rmtree(media_root, ignore_errors=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        purchase = PurchaseRecord.objects.get(invoice_number="INV-9100")
        self.assertEqual(purchase.payments.count(), 1)
        expense = ExpenseRecord.objects.get(
            user=self.user,
            source_reference=f"PURCHASE:{purchase.source_reference}",
        )
        self.assertEqual(expense.title, "Purchase - INV-9100")
        self.assertEqual(expense.amount, Decimal("1200.00"))
        self.assertEqual(expense.vendor, "Immediate Supplier")
        self.assertIn(b"Mahilmart Purchase Invoice", response.content)
        self.assertIn(b"Prepared By", response.content)
        self.assertIn(self.user.username.encode(), response.content)

    def test_purchase_add_page_does_not_show_manual_date_field(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("purchase-add"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="id_transaction_date"', html=False)
        self.assertContains(response, "Purchase date and time are saved automatically")

    def test_purchase_list_hides_non_manual_rows(self):
        self.client.force_login(self.user)
        PurchaseRecord.objects.create(
            user=self.user,
            supplier_name="Manual Supplier",
            purchase_type="Type A",
            invoice_number="MANUAL-1",
            total_amount=Decimal("500.00"),
            paid_amount=Decimal("100.00"),
        )
        PurchaseRecord.objects.create(
            user=self.user,
            supplier_name="Imported Supplier",
            purchase_type="Type B",
            invoice_number="IMPORT-1",
            total_amount=Decimal("700.00"),
            paid_amount=Decimal("200.00"),
            source_reference="LEGACY:1",
        )

        response = self.client.get(reverse("purchase-list"))

        self.assertContains(response, "MANUAL-1")
        self.assertNotContains(response, "IMPORT-1")

    def test_purchase_invoice_download_returns_pdf_for_saved_record(self):
        self.client.force_login(self.user)
        purchase = PurchaseRecord.objects.create(
            user=self.user,
            supplier_name="Invoice Supplier",
            purchase_type="Retail",
            invoice_number="INV-2200",
            total_amount=Decimal("800.00"),
            paid_amount=Decimal("300.00"),
            notes="Saved purchase invoice",
        )
        payment = PurchasePayment.objects.create(
            purchase=purchase,
            user=self.admin_user,
            amount=Decimal("100.00"),
            notes="Counter payment",
        )
        payment_time = timezone.now().replace(hour=10, minute=15, second=0, microsecond=0)
        PurchasePayment.objects.filter(pk=payment.pk).update(created_at=payment_time)

        response = self.client.get(reverse("purchase-invoice", args=[purchase.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("INV-2200_invoice.pdf", response["Content-Disposition"])
        self.assertIn(b"Supplier Signature", response.content)
        self.assertIn(b"Authorized Sign", response.content)
        self.assertIn(self.user.username.encode(), response.content)
        self.assertIn(b"Payment Tracking", response.content)
        self.assertIn(b"Payment Entry", response.content)
        self.assertIn(b"Opening Paid Amount", response.content)
        self.assertIn(self.admin_user.username.encode(), response.content)
        self.assertIn(b"10:15 AM", response.content)
        self.assertIn(b"Counter payment", response.content)

    def test_purchase_detail_endpoint_returns_payment_history_and_actions(self):
        self.client.force_login(self.user)
        purchase = PurchaseRecord.objects.create(
            user=self.user,
            supplier_name="Popup Supplier",
            purchase_type="Wholesale",
            invoice_number="POP-100",
            total_amount=Decimal("950.00"),
            paid_amount=Decimal("250.00"),
            notes="Needs follow-up payment",
        )

        response = self.client.get(reverse("purchase-detail", args=[purchase.pk]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["invoice_number"], "POP-100")
        self.assertEqual(payload["pending_amount"], "700.00")
        self.assertEqual(payload["pay_url"], reverse("purchase-pay", args=[purchase.pk]))
        self.assertEqual(payload["invoice_url"], reverse("purchase-invoice", args=[purchase.pk]))
        self.assertTrue(payload["can_pay"])
        self.assertEqual(payload["last_payment_date"], purchase.transaction_date.strftime("%d-%m-%Y"))
        self.assertEqual(len(payload["payment_history"]), 2)
        self.assertEqual(payload["payment_history"][0]["label"], "Purchase Created")
        self.assertEqual(payload["payment_history"][0]["running_pending"], "950.00")
        self.assertEqual(payload["payment_history"][1]["label"], "Opening Paid Amount")
        self.assertEqual(payload["payment_history"][1]["amount"], "250.00")
        self.assertEqual(payload["payment_history"][1]["running_paid"], "250.00")

    def test_purchase_payment_post_updates_purchase_totals(self):
        self.client.force_login(self.user)
        purchase = PurchaseRecord.objects.create(
            user=self.user,
            supplier_name="Balance Supplier",
            purchase_type="Retail",
            invoice_number="PAY-100",
            total_amount=Decimal("1000.00"),
            paid_amount=Decimal("300.00"),
        )

        response = self.client.post(
            reverse("purchase-pay", args=[purchase.pk]),
            {
                "amount": "250.00",
                "notes": "Second payment today",
            },
        )

        self.assertEqual(response.status_code, 302)
        purchase.refresh_from_db()
        payment = PurchasePayment.objects.get(purchase=purchase)
        expense = ExpenseRecord.objects.get(
            user=self.user,
            source_reference=f"PURCHASE:{purchase.source_reference}",
        )
        self.assertEqual(payment.amount, Decimal("250.00"))
        self.assertEqual(payment.payment_date, date.today())
        self.assertEqual(payment.notes, "Second payment today")
        self.assertEqual(purchase.paid_amount, Decimal("550.00"))
        self.assertEqual(purchase.pending_amount, Decimal("450.00"))
        self.assertEqual(expense.amount, Decimal("1000.00"))
        self.assertIn("Paid Amount: 550.00", expense.notes)
        self.assertIn("Pending Amount: 450.00", expense.notes)

    def test_purchase_list_shows_pay_action_for_pending_purchase(self):
        self.client.force_login(self.user)
        PurchaseRecord.objects.create(
            user=self.user,
            supplier_name="Table Supplier",
            purchase_type="Retail",
            invoice_number="PAY-LINK-1",
            total_amount=Decimal("600.00"),
            paid_amount=Decimal("100.00"),
        )

        response = self.client.get(reverse("purchase-list"))

        self.assertContains(response, "Download Invoice")
        self.assertContains(response, "Pay")

    def test_purchase_list_defaults_to_today_records_only(self):
        self.client.force_login(self.user)
        yesterday = date.today() - timedelta(days=1)
        PurchaseRecord.objects.create(
            user=self.user,
            supplier_name="Yesterday Supplier",
            purchase_type="Retail",
            invoice_number="YEST-100",
            total_amount=Decimal("900.00"),
            paid_amount=Decimal("300.00"),
            transaction_date=yesterday,
        )
        PurchaseRecord.objects.create(
            user=self.user,
            supplier_name="Today Supplier",
            purchase_type="Retail",
            invoice_number="TODAY-100",
            total_amount=Decimal("500.00"),
            paid_amount=Decimal("200.00"),
            transaction_date=date.today(),
        )

        response = self.client.get(reverse("purchase-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TODAY-100")
        self.assertNotContains(response, "YEST-100")
        self.assertEqual(response.context["page_count"], 1)
        self.assertEqual(response.context["page_total"], Decimal("500.00"))
        self.assertEqual(response.context["paid_total"], Decimal("200.00"))
        self.assertEqual(response.context["pending_total"], Decimal("300.00"))
        self.assertEqual(response.context["purchase_filters"]["date_from"], date.today().isoformat())
        self.assertEqual(response.context["purchase_filters"]["date_to"], date.today().isoformat())

    def test_purchase_list_filters_by_requested_fields(self):
        peer_staff = get_user_model().objects.create_user(
            username="peer_staff_purchase",
            password="PeerPass123!",
        )
        self.client.force_login(self.user)
        target_date = date.today() - timedelta(days=3)
        PurchaseRecord.objects.create(
            user=peer_staff,
            supplier_name="Filter Supplier",
            purchase_type="Wholesale",
            invoice_number="FILTER-100",
            total_amount=Decimal("1000.00"),
            paid_amount=Decimal("200.00"),
            transaction_date=target_date,
        )
        PurchaseRecord.objects.create(
            user=self.admin_user,
            supplier_name="Other Supplier",
            purchase_type="Retail",
            invoice_number="OTHER-200",
            total_amount=Decimal("900.00"),
            paid_amount=Decimal("900.00"),
            transaction_date=date.today(),
        )

        response = self.client.get(
            reverse("purchase-list"),
            {
                "supplier_name": "Filter",
                "invoice_number": "100",
                "saved_by": peer_staff.username,
                "pending_amount": "800.00",
                "date_from": target_date.isoformat(),
                "date_to": target_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "FILTER-100")
        self.assertNotContains(response, "OTHER-200")
        self.assertEqual(response.context["page_count"], 1)

    def test_sales_list_shows_synced_sales_columns(self):
        self.client.force_login(self.user)
        SalesLedgerRecord.objects.create(
            source_sale_no=101,
            bill_no="BILL-101",
            sale_date=date.today(),
            customer_name="Surendar",
            net_amount=Decimal("525.00"),
            received_amount=Decimal("525.00"),
            balance_amount=Decimal("0.00"),
            payment_mode=SalesPaymentMode.CASH,
        )
        SalesLedgerRecord.objects.create(
            source_sale_no=102,
            bill_no="BILL-102",
            sale_date=date.today(),
            customer_name="Mani",
            net_amount=Decimal("900.00"),
            received_amount=Decimal("0.00"),
            balance_amount=Decimal("900.00"),
            payment_mode=SalesPaymentMode.CREDIT,
        )

        response = self.client.get(reverse("sales-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sales Ledger")
        self.assertContains(response, "BILL-101")
        self.assertContains(response, "Surendar")
        self.assertContains(response, "Cash")
        self.assertContains(response, "Save Split")
        self.assertEqual(response.context["page_count"], 2)
        self.assertEqual(response.context["page_total"], Decimal("1425.00"))
        self.assertEqual(response.context["received_total"], Decimal("525.00"))
        self.assertEqual(response.context["balance_total"], Decimal("900.00"))

    def test_sales_list_received_total_uses_synced_received_amount(self):
        self.client.force_login(self.user)
        SalesLedgerRecord.objects.create(
            source_sale_no=103,
            bill_no="CARD-103",
            sale_date=date.today(),
            customer_name="Card Customer",
            net_amount=Decimal("600.00"),
            received_amount=Decimal("0.00"),
            balance_amount=Decimal("600.00"),
            payment_mode=SalesPaymentMode.CARD,
        )

        response = self.client.get(reverse("sales-list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["received_total"], Decimal("0.00"))
        self.assertContains(response, "CARD-103")

    def test_sales_list_shows_credit_bill_list_from_sales_records(self):
        self.client.force_login(self.user)
        SalesLedgerRecord.objects.create(
            source_sale_no=111,
            bill_no="CREDIT-111",
            sale_date=date.today(),
            customer_name="Credit Customer",
            net_amount=Decimal("900.00"),
            received_amount=Decimal("0.00"),
            balance_amount=Decimal("900.00"),
            payment_mode=SalesPaymentMode.CREDIT,
        )
        SalesLedgerRecord.objects.create(
            source_sale_no=112,
            bill_no="CASH-112",
            sale_date=date.today(),
            customer_name="Cash Customer",
            net_amount=Decimal("200.00"),
            received_amount=Decimal("200.00"),
            balance_amount=Decimal("0.00"),
            payment_mode=SalesPaymentMode.CASH,
        )

        response = self.client.get(reverse("sales-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Credit Bills")
        self.assertContains(response, "CREDIT-111")
        self.assertNotContains(response, "No credit bills in the current sales filter.")
        self.assertEqual(response.context["credit_bill_count"], 1)
        self.assertEqual(response.context["credit_bill_total"], Decimal("900.00"))

    def test_sales_list_hides_split_paid_bill_from_credit_history(self):
        self.client.force_login(self.user)
        SalesLedgerRecord.objects.create(
            source_sale_no=113,
            bill_no="SPLIT-113",
            sale_date=date.today(),
            customer_name="Split Paid Customer",
            net_amount=Decimal("1560.00"),
            received_amount=Decimal("300.00"),
            balance_amount=Decimal("1260.00"),
            split_cash_amount=Decimal("1560.00"),
            split_card_amount=Decimal("0.00"),
            payment_mode=SalesPaymentMode.CREDIT,
        )

        response = self.client.get(reverse("sales-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SPLIT-113")
        self.assertContains(response, "Split Paid Customer")
        self.assertContains(response, "Rs. 1560.00")
        self.assertContains(response, "Rs. 0.00")
        self.assertEqual(response.context["credit_bill_count"], 0)
        self.assertEqual(response.context["credit_bill_total"], Decimal("0.00"))

    def test_sales_list_defaults_to_today_when_no_filters_are_provided(self):
        self.client.force_login(self.user)
        SalesLedgerRecord.objects.create(
            source_sale_no=150,
            bill_no="YESTERDAY-150",
            sale_date=date.today() - timedelta(days=1),
            customer_name="Old Customer",
            net_amount=Decimal("150.00"),
            received_amount=Decimal("150.00"),
            balance_amount=Decimal("0.00"),
            payment_mode=SalesPaymentMode.CASH,
        )
        SalesLedgerRecord.objects.create(
            source_sale_no=151,
            bill_no="TODAY-151",
            sale_date=date.today(),
            customer_name="Today Customer",
            net_amount=Decimal("225.00"),
            received_amount=Decimal("225.00"),
            balance_amount=Decimal("0.00"),
            payment_mode=SalesPaymentMode.CASH,
        )

        response = self.client.get(reverse("sales-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TODAY-151")
        self.assertNotContains(response, "YESTERDAY-150")
        self.assertEqual(response.context["page_count"], 1)
        self.assertEqual(response.context["sales_filters"]["date_from"], date.today().isoformat())
        self.assertEqual(response.context["sales_filters"]["date_to"], date.today().isoformat())
        self.assertTrue(response.context["is_default_today_view"])

    def test_sales_list_filters_customer_and_payment_mode(self):
        self.client.force_login(self.user)
        SalesLedgerRecord.objects.create(
            source_sale_no=201,
            bill_no="BILL-201",
            sale_date=date.today() - timedelta(days=1),
            customer_name="Saravanan",
            net_amount=Decimal("450.00"),
            received_amount=Decimal("450.00"),
            balance_amount=Decimal("0.00"),
            payment_mode=SalesPaymentMode.CASH,
        )
        SalesLedgerRecord.objects.create(
            source_sale_no=202,
            bill_no="BILL-202",
            sale_date=date.today(),
            customer_name="Saravanan",
            net_amount=Decimal("750.00"),
            received_amount=Decimal("0.00"),
            balance_amount=Decimal("750.00"),
            payment_mode=SalesPaymentMode.CREDIT,
        )
        SalesLedgerRecord.objects.create(
            source_sale_no=203,
            bill_no="BILL-203",
            sale_date=date.today(),
            customer_name="Other Customer",
            net_amount=Decimal("300.00"),
            received_amount=Decimal("300.00"),
            balance_amount=Decimal("0.00"),
            payment_mode=SalesPaymentMode.CASH,
        )

        response = self.client.get(
            reverse("sales-list"),
            {
                "customer_name": "Sara",
                "payment_mode": SalesPaymentMode.CREDIT,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BILL-202")
        self.assertNotContains(response, "BILL-201")
        self.assertNotContains(response, "BILL-203")
        self.assertEqual(response.context["page_count"], 1)
        self.assertEqual(response.context["page_total"], Decimal("750.00"))

    def test_sales_list_can_save_cash_and_card_split_for_bill(self):
        self.client.force_login(self.user)
        record = SalesLedgerRecord.objects.create(
            source_sale_no=301,
            bill_no="BILL-301",
            sale_date=date.today(),
            customer_name="Ganesh",
            net_amount=Decimal("800.00"),
            received_amount=Decimal("800.00"),
            balance_amount=Decimal("0.00"),
            payment_mode=SalesPaymentMode.CASH,
        )

        response = self.client.post(
            reverse("sales-list"),
            {
                "action": "save_split",
                "record_id": str(record.pk),
                "customer_name": "Ganesh",
                "split_cash_amount": "300.00",
                "split_card_amount": "500.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.split_cash_amount, Decimal("300.00"))
        self.assertEqual(record.split_card_amount, Decimal("500.00"))
        self.assertEqual(record.display_payment_mode, "Cash + Card")

    def test_sales_list_can_update_existing_split_bill_to_full_cash_or_card(self):
        self.client.force_login(self.user)
        record = SalesLedgerRecord.objects.create(
            source_sale_no=304,
            bill_no="BILL-304",
            sale_date=date.today(),
            customer_name="Split Customer",
            net_amount=Decimal("450.00"),
            received_amount=Decimal("450.00"),
            balance_amount=Decimal("0.00"),
            split_cash_amount=Decimal("200.00"),
            split_card_amount=Decimal("250.00"),
            payment_mode=SalesPaymentMode.CARD,
        )

        response = self.client.post(
            reverse("sales-list"),
            {
                "action": "save_split",
                "record_id": str(record.pk),
                "split_cash_amount": "450.00",
                "split_card_amount": "0.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.split_cash_amount, Decimal("450.00"))
        self.assertEqual(record.split_card_amount, Decimal("0.00"))
        self.assertEqual(record.display_payment_mode, "Cash")

        response = self.client.post(
            reverse("sales-list"),
            {
                "action": "save_split",
                "record_id": str(record.pk),
                "split_cash_amount": "0.00",
                "split_card_amount": "450.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.split_cash_amount, Decimal("0.00"))
        self.assertEqual(record.split_card_amount, Decimal("450.00"))
        self.assertEqual(record.display_payment_mode, "Card")

    def test_sales_list_split_modal_shows_full_cash_and_card_shortcuts(self):
        self.client.force_login(self.user)
        SalesLedgerRecord.objects.create(
            source_sale_no=305,
            bill_no="BILL-305",
            sale_date=date.today(),
            customer_name="Shortcut Customer",
            net_amount=Decimal("300.00"),
            received_amount=Decimal("300.00"),
            balance_amount=Decimal("0.00"),
            payment_mode=SalesPaymentMode.CASH,
        )

        response = self.client.get(reverse("sales-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Full Cash")
        self.assertContains(response, "Full Card")
        self.assertContains(response, "Clear Split")

    def test_sales_list_rejects_split_total_greater_than_bill_amount(self):
        self.client.force_login(self.user)
        record = SalesLedgerRecord.objects.create(
            source_sale_no=302,
            bill_no="BILL-302",
            sale_date=date.today(),
            customer_name="Mohan",
            net_amount=Decimal("600.00"),
            received_amount=Decimal("600.00"),
            balance_amount=Decimal("0.00"),
            payment_mode=SalesPaymentMode.CASH,
        )

        response = self.client.post(
            reverse("sales-list"),
            {
                "action": "save_split",
                "record_id": str(record.pk),
                "split_cash_amount": "400.00",
                "split_card_amount": "300.00",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        record.refresh_from_db()
        self.assertEqual(record.split_cash_amount, Decimal("0.00"))
        self.assertEqual(record.split_card_amount, Decimal("0.00"))
        self.assertContains(response, "cannot exceed the net amount")

    def test_sales_record_with_card_payment_mode_displays_as_card_and_not_credit(self):
        record = SalesLedgerRecord.objects.create(
            source_sale_no=303,
            bill_no="CARD-303",
            sale_date=date.today(),
            customer_name="Mohan",
            net_amount=Decimal("600.00"),
            received_amount=Decimal("0.00"),
            balance_amount=Decimal("600.00"),
            payment_mode=SalesPaymentMode.CARD,
        )

        self.assertTrue(record.is_card_payment)
        self.assertEqual(record.display_payment_mode, "Card")
        self.assertEqual(record.effective_received_amount, Decimal("600.00"))
        self.assertEqual(record.effective_balance_amount, Decimal("0.00"))

    @patch("tracker.views.sync_sales_from_sqlserver")
    def test_sales_list_sync_post_runs_sync_and_preserves_filters(self, sync_mock):
        self.client.force_login(self.user)
        sync_mock.return_value = SalesSyncStats(
            fetched_count=4,
            inserted_count=2,
            refreshed_count=2,
        )

        response = self.client.post(
            reverse("sales-list"),
            {
                "bill_no": "522",
                "customer_name": "Surendar",
                "payment_mode": SalesPaymentMode.CASH,
                "date_from": "2025-11-01",
                "date_to": "2025-11-09",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            (
                f"{reverse('sales-list')}?bill_no=522&customer_name=Surendar"
                "&payment_mode=Cash&date_from=2025-11-01&date_to=2025-11-09"
            ),
        )
        sync_mock.assert_called_once_with(
            date_from=date(2025, 11, 1),
            date_to=date(2025, 11, 9),
        )

    @patch("tracker.views.sync_sales_from_sqlserver")
    def test_sales_list_sync_post_defaults_to_today_when_dates_are_missing(self, sync_mock):
        self.client.force_login(self.user)
        sync_mock.return_value = SalesSyncStats(
            fetched_count=5,
            inserted_count=5,
            refreshed_count=0,
        )

        response = self.client.post(reverse("sales-list"), {})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            (
                f"{reverse('sales-list')}?date_from={date.today().isoformat()}"
                f"&date_to={date.today().isoformat()}"
            ),
        )
        sync_mock.assert_called_once_with(
            date_from=date.today(),
            date_to=date.today(),
        )


class SalesSyncUnitTests(TestCase):
    @patch("tracker.sales_sync.get_sqlserver_driver", return_value="ODBC Driver 18 for SQL Server")
    @patch.dict("os.environ", {"SQLSERVER_HOST": ""}, clear=False)
    def test_build_sqlserver_connection_string_requires_host_from_env(self, _driver_mock):
        with self.assertRaisesMessage(
            SalesSyncError,
            "SQLSERVER_HOST is not set. Add it to the .env file before syncing sales.",
        ):
            build_sqlserver_connection_string()

    def test_build_sales_sync_query_defaults_to_today(self):
        query, params = build_sales_sync_query()

        self.assertIn(
            f"CAST(SalMas_Date AS date) >= {{d '{timezone.localdate().isoformat()}'}}",
            query,
        )
        self.assertIn(
            f"CAST(SalMas_Date AS date) <= {{d '{timezone.localdate().isoformat()}'}}",
            query,
        )
        self.assertEqual(params, [])

    def test_build_sales_sync_query_respects_custom_date_range(self):
        query, params = build_sales_sync_query(
            date_from=date(2025, 11, 1),
            date_to=date(2025, 11, 9),
        )

        self.assertIn("ORDER BY SalMas_SNo", query)
        self.assertIn("CAST(SalMas_Date AS date) >= {d '2025-11-01'}", query)
        self.assertIn("CAST(SalMas_Date AS date) <= {d '2025-11-09'}", query)
        self.assertEqual(params, [])

    def test_classify_sales_payment_mode_uses_source_type_for_card(self):
        self.assertEqual(
            classify_sales_payment_mode(
                source_sale_type=next(
                    key
                    for key, value in SOURCE_SALE_TYPE_PAYMENT_MODE_MAP.items()
                    if value == SalesPaymentMode.CARD
                ),
                received_amount=Decimal("0.00"),
                balance_amount=Decimal("900.00"),
                card_number="",
            ),
            SalesPaymentMode.CARD,
        )

    def test_classify_sales_payment_mode_uses_source_type_for_credit(self):
        self.assertEqual(
            classify_sales_payment_mode(
                source_sale_type=next(
                    key
                    for key, value in SOURCE_SALE_TYPE_PAYMENT_MODE_MAP.items()
                    if value == SalesPaymentMode.CREDIT
                ),
                received_amount=Decimal("0.00"),
                balance_amount=Decimal("900.00"),
                card_number="8904057301672",
            ),
            SalesPaymentMode.CREDIT,
        )

    def test_classify_sales_payment_mode_uses_source_type_for_cash(self):
        self.assertEqual(
            classify_sales_payment_mode(
                source_sale_type=next(
                    key
                    for key, value in SOURCE_SALE_TYPE_PAYMENT_MODE_MAP.items()
                    if value == SalesPaymentMode.CASH
                ),
                received_amount=Decimal("0.00"),
                balance_amount=Decimal("900.00"),
                card_number="8904057301672",
            ),
            SalesPaymentMode.CASH,
        )

    def test_classify_sales_payment_mode_falls_back_to_cash_without_source_type(self):
        self.assertEqual(
            classify_sales_payment_mode(
                received_amount=Decimal("250.00"),
                balance_amount=Decimal("0.00"),
                card_number="",
            ),
            SalesPaymentMode.CASH,
        )

    def test_classify_sales_payment_mode_falls_back_to_credit_without_source_type(self):
        self.assertEqual(
            classify_sales_payment_mode(
                received_amount=Decimal("0.00"),
                balance_amount=Decimal("900.00"),
                card_number="",
            ),
            SalesPaymentMode.CREDIT,
        )

    def test_classify_sales_payment_mode_marks_unknown_when_unpaid_state_is_ambiguous(self):
        self.assertEqual(
            classify_sales_payment_mode(
                received_amount=Decimal("0.00"),
                balance_amount=Decimal("0.00"),
                card_number="",
            ),
            SalesPaymentMode.UNKNOWN,
        )

    def test_sanitize_sales_amounts_resets_barcode_like_values(self):
        net_amount, received_amount, balance_amount, had_amount_anomaly = (
            sanitize_sales_amounts(
                net_amount=Decimal("224.00"),
                received_amount=Decimal("8904057300859.00"),
                balance_amount=Decimal("-8904057300635.00"),
            )
        )

        self.assertEqual(net_amount, Decimal("224.00"))
        self.assertEqual(received_amount, Decimal("0.00"))
        self.assertEqual(balance_amount, Decimal("224.00"))
        self.assertTrue(had_amount_anomaly)


class UserSyncUnitTests(TestCase):
    def test_sync_users_from_rows_creates_login_user_and_profile(self):
        stats = sync_users_from_rows(
            [
                SimpleNamespace(
                    User_SNo=12,
                    User_Name="StoreAdminSync",
                    User_MtName="STOREADMINSYNC",
                    User_Passwrd="SyncPass123!",
                    User_CPasswrd="SyncPass123!",
                )
            ]
        )

        synced_user = get_user_model().objects.get(username="StoreAdminSync")

        self.assertEqual(stats.fetched_count, 1)
        self.assertEqual(stats.inserted_count, 1)
        self.assertEqual(stats.refreshed_count, 0)
        self.assertEqual(stats.skipped_count, 0)
        self.assertTrue(synced_user.is_staff)
        self.assertFalse(synced_user.is_superuser)
        self.assertTrue(synced_user.check_password("SyncPass123!"))
        self.assertEqual(synced_user.account_profile.master_name, "STOREADMINSYNC")
        self.assertEqual(synced_user.account_profile.source_user_no, 12)
        self.assertEqual(
            synced_user.account_profile.source_reference,
            USER_SYNC_SOURCE_REFERENCE,
        )
        self.assertTrue(
            UserModulePermission.objects.filter(user=synced_user).exists()
        )

    def test_sync_users_from_rows_skips_existing_user_matched_by_username(self):
        existing_user = get_user_model().objects.create_user(
            username="Ramu",
            password="OldPass123!",
        )

        stats = sync_users_from_rows(
            [
                SimpleNamespace(
                    User_SNo=44,
                    User_Name="Ramu",
                    User_MtName="RAMU",
                    User_Passwrd="NewPass123!",
                    User_CPasswrd="NewPass123!",
                )
            ]
        )

        existing_user.refresh_from_db()

        self.assertEqual(stats.fetched_count, 1)
        self.assertEqual(stats.inserted_count, 0)
        self.assertEqual(stats.refreshed_count, 0)
        self.assertEqual(stats.skipped_count, 1)
        self.assertTrue(existing_user.check_password("OldPass123!"))
        self.assertFalse(
            UserAccountProfile.objects.filter(user=existing_user).exists()
        )
