import json
import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

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
    UserAccountProfile,
)
from .sales_sync import (
    SalesSyncError,
    SalesSyncStats,
    build_sales_sync_query,
    build_sqlserver_connection_string,
    classify_sales_payment_mode,
    sanitize_sales_amounts,
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
        self.assertContains(response, "Shared role workspace")

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
                "start_date": today_value,
                "end_date": today_value,
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
                "category": "Utilities",
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
            category="General",
            amount=Decimal("125.00"),
            transaction_date=date.today(),
        )

        response = self.client.get(reverse("daily-settlement"))

        self.assertEqual(response.status_code, 200)
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
            category="General",
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
            category="General",
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
                "start_date": today_value,
                "end_date": today_value,
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
            category="General",
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
                "start_date": today_value,
                "end_date": today_value,
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
                "start_date": today_value,
                "end_date": today_value,
            },
        )

        self.assertEqual(response.status_code, 302)
        settlement = DailyCashSettlement.objects.get(
            user=self.user,
            settlement_date=date.today(),
        )
        self.assertEqual(settlement.gpay_settled, Decimal("275.00"))

    def test_daily_settlement_filter_range_shows_only_matching_dates(self):
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
                "start_date": target_date.isoformat(),
                "end_date": target_date.isoformat(),
                "entry_date": target_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["settlement_count"], 1)
        self.assertContains(response, target_date.strftime("%d-%m-%Y"))
        self.assertContains(response, "Target Counter")
        self.assertNotContains(response, "Other Counter")

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

        self.assertIn("CAST(SalMas_Date AS date) >= ?", query)
        self.assertIn("CAST(SalMas_Date AS date) <= ?", query)
        self.assertEqual(params, [timezone.localdate(), timezone.localdate()])

    def test_build_sales_sync_query_respects_custom_date_range(self):
        query, params = build_sales_sync_query(
            date_from=date(2025, 11, 1),
            date_to=date(2025, 11, 9),
        )

        self.assertIn("ORDER BY SalMas_SNo", query)
        self.assertEqual(params, [date(2025, 11, 1), date(2025, 11, 9)])

    def test_classify_sales_payment_mode_marks_credit_first(self):
        self.assertEqual(
            classify_sales_payment_mode(
                received_amount=Decimal("0.00"),
                balance_amount=Decimal("900.00"),
                card_number="8904057301672",
            ),
            SalesPaymentMode.CREDIT,
        )

    def test_classify_sales_payment_mode_marks_card_for_meaningful_card_number(self):
        self.assertEqual(
            classify_sales_payment_mode(
                received_amount=Decimal("250.00"),
                balance_amount=Decimal("0.00"),
                card_number="8904057301672",
            ),
            SalesPaymentMode.CARD,
        )

    def test_classify_sales_payment_mode_marks_cash_without_card_number(self):
        self.assertEqual(
            classify_sales_payment_mode(
                received_amount=Decimal("250.00"),
                balance_amount=Decimal("0.00"),
                card_number="",
            ),
            SalesPaymentMode.CASH,
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
