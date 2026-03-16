from datetime import date
from uuid import uuid4

from django.conf import settings
from django.db import models
from decimal import Decimal, InvalidOperation

from .user_roles import filter_queryset_by_role


def _normalize_short_text(value, max_length):
    return " ".join(str(value or "").strip().split())[:max_length]


def _normalize_cash_denomination_count(value):
    try:
        count = int(str(value or "0").strip())
    except (TypeError, ValueError):
        return 0
    return max(count, 0)


def _get_cash_denomination_total(denominations):
    if not isinstance(denominations, dict):
        return Decimal("0.00")

    total = Decimal("0.00")
    for raw_denomination, raw_count in denominations.items():
        count = _normalize_cash_denomination_count(raw_count)
        if not count:
            continue
        try:
            denomination = Decimal(str(raw_denomination))
        except (InvalidOperation, TypeError, ValueError):
            continue
        total += denomination * Decimal(str(count))
    return total


class PaymentMethod(models.TextChoices):
    CASH = "Cash", "Cash"
    CARD = "Card", "Card"
    BANK_TRANSFER = "Bank Transfer", "Bank Transfer"
    UPI = "UPI", "UPI"
    OTHER = "Other", "Other"


class SalesPaymentMode(models.TextChoices):
    CASH = "Cash", "Cash"
    CARD = "Card", "Card"
    CREDIT = "Credit", "Credit"
    UNKNOWN = "Unknown", "Unknown"


class SupplierStatus(models.TextChoices):
    ACTIVE = "Active", "Active"
    INACTIVE = "Inactive", "Inactive"


class BaseRecord(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="%(class)ss",
    )
    title = models.CharField(max_length=120)
    category = models.CharField(max_length=80)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    transaction_date = models.DateField(default=date.today)
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        default=PaymentMethod.BANK_TRANSFER,
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-transaction_date", "-created_at"]

    def __str__(self):
        return f"{self.title} - {self.amount}"


class IncomeRecord(BaseRecord):
    source = models.CharField(max_length=120)
    source_reference = models.CharField(max_length=60, blank=True, db_index=True)

    class Meta(BaseRecord.Meta):
        verbose_name = "Income Record"
        verbose_name_plural = "Income Records"


class Supplier(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="suppliers",
    )
    source_supplier_no = models.IntegerField(null=True, blank=True, db_index=True)
    supplier_code = models.CharField(max_length=20, unique=True, null=True, blank=True)
    name = models.CharField(max_length=120)
    contact_person = models.CharField(max_length=120)
    phone_number = models.CharField(max_length=20)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    gstin_number = models.CharField(max_length=30, blank=True)
    fssai_number = models.CharField(max_length=30, blank=True)
    pan_number = models.CharField(max_length=20, blank=True)
    credit_terms = models.CharField(max_length=120, blank=True)
    opening_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    bank_name = models.CharField(max_length=120, blank=True)
    account_number = models.CharField(max_length=40, blank=True)
    ifsc_code = models.CharField(max_length=20, blank=True)
    status = models.CharField(
        max_length=10,
        choices=SupplierStatus.choices,
        default=SupplierStatus.ACTIVE,
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "created_at"]
        unique_together = ("user", "name")

    def __str__(self):
        return f"{self.supplier_code or 'Pending'} - {self.name}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.supplier_code:
            generated_code = f"SUP-{self.pk:04d}"
            Supplier.objects.filter(pk=self.pk).update(supplier_code=generated_code)
            self.supplier_code = generated_code


class ExpenseCategory(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="expense_categories",
    )
    name = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "created_at"]
        unique_together = ("user", "name")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = _normalize_short_text(self.name, 80)
        super().save(*args, **kwargs)


class UserAccountProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="account_profile",
    )
    master_name = models.CharField(max_length=150, blank=True)
    source_user_no = models.IntegerField(null=True, blank=True, unique=True, db_index=True)
    source_reference = models.CharField(max_length=60, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__username"]

    def __str__(self):
        return f"{self.user.username} profile"


class PurchaseRecord(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="purchase_records",
    )
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name="purchase_records",
        null=True,
        blank=True,
    )
    supplier_name = models.CharField(max_length=120)
    purchase_type = models.CharField(max_length=40)
    invoice_number = models.CharField(max_length=120, db_index=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    pending_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    transaction_date = models.DateField(null=True, blank=True)
    attachment = models.FileField(upload_to="purchase_files/", blank=True)
    notes = models.TextField(blank=True)
    source_reference = models.CharField(max_length=60, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-transaction_date", "-created_at"]
        unique_together = ("user", "source_reference")

    def __str__(self):
        return f"{self.invoice_number} - {self.total_amount}"

    def save(self, *args, **kwargs):
        if self.supplier_id:
            self.supplier_name = self.supplier.name
        if not self.source_reference:
            self.source_reference = f"MANUAL:{uuid4().hex}"
        if not self.transaction_date:
            self.transaction_date = date.today()
        self.pending_amount = self.total_amount - self.paid_amount
        super().save(*args, **kwargs)


class PurchasePayment(models.Model):
    purchase = models.ForeignKey(
        PurchaseRecord,
        on_delete=models.CASCADE,
        related_name="payments",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="purchase_payments",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField(default=date.today, db_index=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-payment_date", "-created_at", "-pk"]

    def __str__(self):
        return f"{self.purchase.invoice_number} payment - {self.amount}"


class DailyCashSettlement(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="daily_cash_settlements",
    )
    settlement_date = models.DateField(default=date.today, db_index=True)
    opening_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    gpay_settled = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_settled = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_denominations = models.JSONField(default=dict, blank=True)
    cash_denomination_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_in_hand = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_difference = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_settled_to = models.CharField(max_length=120, blank=True)
    expense_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    closing_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    actual_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-settlement_date", "-updated_at", "-pk"]
        unique_together = ("user", "settlement_date")

    def __str__(self):
        return f"{self.settlement_date} settlement - {self.user}"

    def save(self, *args, **kwargs):
        self.cash_denominations = (
            self.cash_denominations if isinstance(self.cash_denominations, dict) else {}
        )
        self.cash_denomination_total = _get_cash_denomination_total(
            self.cash_denominations
        )
        self.cash_in_hand = self.cash_settled + self.closing_balance
        self.total_amount = (
            self.gpay_settled
            + self.cash_settled
            + self.expense_amount
            + self.closing_balance
        )
        self.actual_sales = self.total_amount - self.opening_balance
        self.cash_difference = self.cash_denomination_total - self.cash_in_hand
        super().save(*args, **kwargs)


class SalesLedgerRecord(models.Model):
    source_sale_no = models.IntegerField(unique=True)
    bill_no = models.CharField(max_length=60, db_index=True)
    sale_date = models.DateField(null=True, blank=True, db_index=True)
    customer_name = models.CharField(max_length=120, blank=True, db_index=True)
    net_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    received_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    balance_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    split_cash_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    split_card_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    payment_mode = models.CharField(
        max_length=20,
        choices=SalesPaymentMode.choices,
        default=SalesPaymentMode.UNKNOWN,
        db_index=True,
    )
    source_card_no = models.CharField(max_length=120, blank=True)
    is_cancelled = models.BooleanField(default=False, db_index=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-sale_date", "-source_sale_no"]
        indexes = [
            models.Index(fields=["sale_date", "bill_no"]),
            models.Index(fields=["customer_name", "payment_mode"]),
        ]

    def __str__(self):
        return f"{self.bill_no} - {self.customer_name or 'Unknown Customer'}"

    @property
    def has_manual_split(self):
        return self.split_cash_amount > 0 or self.split_card_amount > 0

    @property
    def split_total_amount(self):
        return self.split_cash_amount + self.split_card_amount

    @property
    def effective_received_amount(self):
        if self.has_manual_split:
            return min(self.net_amount, self.split_total_amount)
        if self.received_amount > 0:
            return min(self.net_amount, self.received_amount)
        return Decimal("0.00")

    @property
    def effective_balance_amount(self):
        if self.has_manual_split:
            return max(self.net_amount - self.effective_received_amount, Decimal("0.00"))
        if self.balance_amount > 0:
            return min(self.net_amount, self.balance_amount)
        return max(self.net_amount - self.effective_received_amount, Decimal("0.00"))

    @property
    def display_payment_mode(self):
        if self.split_cash_amount > 0 and self.split_card_amount > 0:
            return "Cash + Card"
        if self.split_cash_amount > 0 and self.split_card_amount == 0:
            return "Cash"
        if self.split_card_amount > 0 and self.split_cash_amount == 0:
            return "Card"
        return self.payment_mode


class ExpenseRecord(BaseRecord):
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name="expenses",
        null=True,
        blank=True,
    )
    source_reference = models.CharField(max_length=60, blank=True, db_index=True)
    vendor = models.CharField(max_length=120, blank=True)

    class Meta(BaseRecord.Meta):
        verbose_name = "Expense Record"
        verbose_name_plural = "Expense Records"

    @property
    def supplier_display(self):
        if self.supplier_id:
            return self.supplier.name
        if self.vendor:
            return self.vendor
        return "General Expense"

    def save(self, *args, **kwargs):
        self.title = _normalize_short_text(self.title, 120)
        self.category = _normalize_short_text(self.category, 80)
        self.vendor = _normalize_short_text(self.vendor, 120)
        if self.supplier_id:
            self.vendor = self.supplier.name
        super().save(*args, **kwargs)
        if self.user_id and self.category:
            existing_category = (
                filter_queryset_by_role(ExpenseCategory.objects.all(), self.user)
                .filter(name__iexact=self.category)
                .first()
            )
            if existing_category is None:
                ExpenseCategory.objects.get_or_create(
                    user=self.user,
                    name=self.category,
                )
