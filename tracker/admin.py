from django.contrib import admin

from .models import (
    DailyCashSettlement,
    ExpenseCategory,
    ExpenseRecord,
    IncomeRecord,
    PurchasePayment,
    PurchaseRecord,
    ReconciliationExpenseEntry,
    ReconciliationIncomeEntry,
    SalesLedgerRecord,
    Supplier,
    UserAccountProfile,
    UserModulePermission,
)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = (
        "supplier_code",
        "name",
        "contact_person",
        "phone_number",
        "status",
        "user",
    )
    search_fields = (
        "supplier_code",
        "name",
        "contact_person",
        "phone_number",
        "email",
        "gstin_number",
        "pan_number",
    )


@admin.register(IncomeRecord)
class IncomeRecordAdmin(admin.ModelAdmin):
    list_display = ("title", "source", "category", "amount", "transaction_date", "user")
    list_filter = ("transaction_date", "category", "payment_method")
    search_fields = ("title", "source", "category", "notes")


@admin.register(ExpenseRecord)
class ExpenseRecordAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "supplier_display",
        "category",
        "amount",
        "transaction_date",
        "user",
    )
    list_filter = ("transaction_date", "category", "payment_method")
    search_fields = ("title", "vendor", "supplier__name", "category", "notes")


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "created_at")
    search_fields = ("name", "user__username")


@admin.register(PurchaseRecord)
class PurchaseRecordAdmin(admin.ModelAdmin):
    list_display = (
        "invoice_number",
        "supplier_name",
        "purchase_type",
        "total_amount",
        "paid_amount",
        "pending_amount",
        "transaction_date",
    )
    list_filter = ("purchase_type", "transaction_date")
    search_fields = ("invoice_number", "supplier_name", "source_reference")


@admin.register(PurchasePayment)
class PurchasePaymentAdmin(admin.ModelAdmin):
    list_display = ("purchase", "amount", "payment_date", "user", "created_at")
    list_filter = ("payment_date",)
    search_fields = ("purchase__invoice_number", "purchase__supplier_name", "notes")


@admin.register(SalesLedgerRecord)
class SalesLedgerRecordAdmin(admin.ModelAdmin):
    list_display = (
        "bill_no",
        "sale_date",
        "customer_name",
        "net_amount",
        "received_amount",
        "balance_amount",
        "split_cash_amount",
        "split_card_amount",
        "payment_mode",
        "is_cancelled",
    )
    list_filter = ("sale_date", "payment_mode", "is_cancelled")
    search_fields = ("bill_no", "customer_name", "source_sale_no")


@admin.register(DailyCashSettlement)
class DailyCashSettlementAdmin(admin.ModelAdmin):
    list_display = (
        "settlement_date",
        "user",
        "opening_balance",
        "gpay_settled",
        "cash_settled",
        "expense_amount",
        "closing_balance",
        "actual_sales",
    )
    list_filter = ("settlement_date",)
    search_fields = ("user__username", "cash_settled_to", "notes")


@admin.register(UserAccountProfile)
class UserAccountProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "master_name", "source_user_no")
    search_fields = ("user__username", "master_name", "source_reference")


@admin.register(UserModulePermission)
class UserModulePermissionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "allow_dashboard",
        "allow_sales",
        "allow_daily_settlement",
        "allow_income",
        "allow_purchases",
        "allow_suppliers",
        "allow_expenses",
        "allow_reports",
    )
    search_fields = ("user__username",)


@admin.register(ReconciliationIncomeEntry)
class ReconciliationIncomeEntryAdmin(admin.ModelAdmin):
    list_display = ("title", "source", "category", "amount", "transaction_date", "user")
    list_filter = ("transaction_date", "payment_method")
    search_fields = ("title", "source", "category", "notes")


@admin.register(ReconciliationExpenseEntry)
class ReconciliationExpenseEntryAdmin(admin.ModelAdmin):
    list_display = ("title", "vendor", "category", "amount", "transaction_date", "user")
    list_filter = ("transaction_date", "payment_method")
    search_fields = ("title", "vendor", "category", "notes")
