from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from .models import (
    DailyCashSettlement,
    ExpenseCategory,
    ExpensePurpose,
    ExpenseRecord,
    IncomeRecord,
    IncomePurpose,
    PaymentMethod,
    PurchaseRecord,
    ReconciliationExpenseEntry,
    ReconciliationIncomeEntry,
    ReconciliationOpeningBalance,
    Supplier,
    UserAccountProfile,
    UserModulePermission,
)
from .expense_categories import COUNTER_EXPENSE_CATEGORY
from .expense_categories import get_expense_category_options as get_saved_expense_category_options
from .expense_categories import normalize_expense_category_name, normalize_expense_purpose_name
from .income_categories import (
    DEFAULT_INCOME_CATEGORY,
    INCOME_CATEGORY_CHOICES,
    normalize_income_category_name,
)
from .income_purposes import normalize_income_purpose_name
from .user_roles import (
    USER_ROLE_CHOICES,
    USER_ROLE_STAFF,
    apply_user_role,
    filter_queryset_by_role,
    get_user_role,
)

User = get_user_model()


class StyledAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        placeholders = {
            "username": "Enter your username",
            "password": "Enter your password",
        }
        for name, field in self.fields.items():
            field.widget.attrs.update(
                {
                    "class": "input-control",
                    "placeholder": placeholders.get(name, field.label),
                }
            )


class StyledModelForm(forms.ModelForm):
    date_widget = forms.DateInput(attrs={"type": "date", "class": "input-control"})
    text_widget = forms.TextInput(attrs={"class": "input-control"})
    money_widget = forms.NumberInput(
        attrs={"class": "input-control", "step": "0.01", "min": "0"}
    )
    select_widget = forms.Select(attrs={"class": "input-control"})
    note_widget = forms.Textarea(
        attrs={"class": "input-control textarea-control", "rows": 4}
    )


class IncomeForm(StyledModelForm):
    category = forms.ChoiceField(
        choices=INCOME_CATEGORY_CHOICES,
        initial=DEFAULT_INCOME_CATEGORY,
        label="Income category",
        help_text="Counter Income is used in Daily Settlement. Office Income stays separate.",
        widget=forms.RadioSelect,
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].initial = DEFAULT_INCOME_CATEGORY
        if not self.is_bound and not self.initial.get("payment_method") and not self.instance.pk:
            self.fields["payment_method"].initial = PaymentMethod.CASH
        self.fields["title"].widget.attrs["data-purpose-input"] = "income-form"
        self.fields["title"].widget.attrs["autocomplete"] = "off"

    def clean_category(self):
        return normalize_income_category_name(self.cleaned_data["category"])

    def clean_title(self):
        value = normalize_income_purpose_name(self.cleaned_data.get("title"))
        if not value:
            raise ValidationError("Purpose is required.")
        return value

    class Meta:
        model = IncomeRecord
        fields = [
            "title",
            "source",
            "category",
            "amount",
            "transaction_date",
            "payment_method",
            "notes",
        ]
        widgets = {
            "title": StyledModelForm.text_widget,
            "source": StyledModelForm.text_widget,
            "amount": StyledModelForm.money_widget,
            "transaction_date": StyledModelForm.date_widget,
            "payment_method": StyledModelForm.select_widget,
            "notes": StyledModelForm.note_widget,
        }
        labels = {
            "title": "Purpose",
            "source": "Source / Reference",
        }
        help_texts = {
            "source": "Use customer name, order channel, office note, or reference details.",
            "notes": "Optional. Add handover notes, offer details, or collection remarks.",
        }


class IncomePurposeForm(StyledModelForm):
    def clean_category(self):
        value = normalize_income_category_name(self.cleaned_data.get("category"))
        if not value:
            raise ValidationError("Category is required.")
        return value

    def clean_name(self):
        value = normalize_income_purpose_name(self.cleaned_data.get("name"))
        if not value:
            raise ValidationError("Purpose name is required.")
        return value

    class Meta:
        model = IncomePurpose
        fields = ["category", "name"]
        widgets = {
            "category": forms.HiddenInput(),
            "name": StyledModelForm.text_widget,
        }
        labels = {
            "name": "Purpose Name",
        }
        help_texts = {
            "name": "Create a reusable purpose for the selected income category.",
        }


class ExpenseForm(StyledModelForm):
    category = forms.ChoiceField(
        choices=(),
        widget=forms.Select(attrs={"class": "input-control"}),
    )

    def __init__(self, *args, user=None, counter_only_category=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and not self.initial.get("payment_method") and not self.instance.pk:
            self.fields["payment_method"].initial = PaymentMethod.CASH
        queryset = Supplier.objects.none()
        category_options = []
        if user is not None:
            queryset = filter_queryset_by_role(Supplier.objects.all(), user)
            saved_category_options = get_saved_expense_category_options(user)
            category_options = (
                [COUNTER_EXPENSE_CATEGORY]
                if counter_only_category
                else saved_category_options
            )
        self.fields["supplier"].queryset = queryset
        self.fields["supplier"].empty_label = "No saved supplier"
        self.fields["supplier"].required = False
        self.fields["category"].widget.attrs["data-purpose-category"] = "expense-form"
        self.fields["title"].widget.attrs["data-purpose-input"] = "expense-form"
        self.fields["title"].widget.attrs["autocomplete"] = "off"
        current_category = (
            (self.data.get(self.add_prefix("category")) if self.is_bound else "")
            or self.initial.get("category")
            or getattr(self.instance, "category", "")
        ).strip()
        if (
            current_category
            and current_category not in category_options
            and not counter_only_category
        ):
            category_options.append(current_category)
        self.fields["category"].choices = [
            ("", "Select Category"),
            *[(option, option) for option in category_options],
        ]

    class Meta:
        model = ExpenseRecord
        fields = [
            "category",
            "title",
            "supplier",
            "vendor",
            "amount",
            "transaction_date",
            "payment_method",
            "notes",
        ]
        widgets = {
            "title": StyledModelForm.text_widget,
            "supplier": StyledModelForm.select_widget,
            "vendor": StyledModelForm.text_widget,
            "amount": StyledModelForm.money_widget,
            "transaction_date": StyledModelForm.date_widget,
            "payment_method": StyledModelForm.select_widget,
            "notes": StyledModelForm.note_widget,
        }
        labels = {
            "title": "Purpose",
            "supplier": "Saved Supplier",
            "vendor": "Paid To / Reference",
        }
        help_texts = {
            "supplier": "Optional. Choose this only for supplier-related purchases.",
            "vendor": "Optional. Use this for electricity, salary, rent, fuel, courier, or any general expense.",
        }


class ExpenseCategoryForm(StyledModelForm):
    def clean_name(self):
        value = " ".join((self.cleaned_data.get("name") or "").strip().split())
        if not value:
            raise ValidationError("Category name is required.")
        return value

    class Meta:
        model = ExpenseCategory
        fields = ["name"]
        widgets = {
            "name": StyledModelForm.text_widget,
        }
        labels = {
            "name": "Category Name",
        }
        help_texts = {
            "name": "Create a reusable expense category for your account.",
        }


class ExpensePurposeForm(StyledModelForm):
    def clean_category(self):
        value = normalize_expense_category_name(self.cleaned_data.get("category"))
        if not value:
            raise ValidationError("Category is required.")
        return value

    def clean_name(self):
        value = normalize_expense_purpose_name(self.cleaned_data.get("name"))
        if not value:
            raise ValidationError("Purpose name is required.")
        return value

    class Meta:
        model = ExpensePurpose
        fields = ["category", "name"]
        widgets = {
            "category": forms.HiddenInput(),
            "name": StyledModelForm.text_widget,
        }
        labels = {
            "name": "Purpose Name",
        }
        help_texts = {
            "name": "Create a reusable purpose for the selected expense category.",
        }


class DailyCashSettlementForm(StyledModelForm):
    class Meta:
        model = DailyCashSettlement
        fields = [
            "settlement_date",
            "opening_balance",
            "gpay_settled",
            "cash_settled",
            "cash_settled_to",
            "closing_balance",
            "notes",
        ]
        widgets = {
            "settlement_date": StyledModelForm.date_widget,
            "opening_balance": StyledModelForm.money_widget,
            "gpay_settled": StyledModelForm.money_widget,
            "cash_settled": StyledModelForm.money_widget,
            "cash_settled_to": StyledModelForm.text_widget,
            "closing_balance": StyledModelForm.money_widget,
            "notes": StyledModelForm.note_widget,
        }
        labels = {
            "settlement_date": "Date",
            "opening_balance": "Opening Balance",
            "gpay_settled": "Card Bill / Split-Card Settled",
            "cash_settled": "Cash Settled",
            "cash_settled_to": "Cash Settled To",
            "expense_amount": "Expense",
            "closing_balance": "Closing Balance",
        }
        help_texts = {
            "cash_settled_to": "Optional. Enter staff, counter name, or recipient reference.",
            "notes": "Optional. Add handover notes or settlement remarks.",
        }

    def clean(self):
        cleaned_data = super().clean()
        numeric_fields = (
            "opening_balance",
            "gpay_settled",
            "cash_settled",
            "closing_balance",
        )
        for field_name in numeric_fields:
            amount = cleaned_data.get(field_name)
            if amount is not None and amount < 0:
                self.add_error(field_name, "Amount cannot be less than zero.")
        return cleaned_data


class ReconciliationIncomeForm(StyledModelForm):
    class Meta:
        model = ReconciliationIncomeEntry
        fields = [
            "title",
            "source",
            "category",
            "amount",
            "transaction_date",
            "payment_method",
            "notes",
        ]
        widgets = {
            "title": StyledModelForm.text_widget,
            "source": StyledModelForm.text_widget,
            "category": StyledModelForm.text_widget,
            "amount": StyledModelForm.money_widget,
            "transaction_date": StyledModelForm.date_widget,
            "payment_method": StyledModelForm.select_widget,
            "notes": StyledModelForm.note_widget,
        }
        labels = {
            "title": "Income Title",
            "source": "Source / Reference",
        }


class ReconciliationExpenseForm(StyledModelForm):
    category = forms.ChoiceField(
        choices=(),
        widget=forms.Select(attrs={"class": "input-control"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        category_options = get_saved_expense_category_options(user) if user is not None else []
        self.fields["category"].widget.attrs["data-purpose-category"] = "reconciliation-expense"
        self.fields["title"].widget.attrs["data-purpose-input"] = "reconciliation-expense"
        self.fields["title"].widget.attrs["autocomplete"] = "off"
        current_category = (
            (self.data.get(self.add_prefix("category")) if self.is_bound else "")
            or self.initial.get("category")
            or getattr(self.instance, "category", "")
        ).strip()
        if current_category and current_category not in category_options:
            category_options.append(current_category)
        self.fields["category"].choices = [
            ("", "Select Category"),
            *[(option, option) for option in category_options],
        ]

    class Meta:
        model = ReconciliationExpenseEntry
        fields = [
            "title",
            "vendor",
            "category",
            "amount",
            "transaction_date",
            "payment_method",
            "notes",
        ]
        widgets = {
            "title": StyledModelForm.text_widget,
            "vendor": StyledModelForm.text_widget,
            "category": StyledModelForm.text_widget,
            "amount": StyledModelForm.money_widget,
            "transaction_date": StyledModelForm.date_widget,
            "payment_method": StyledModelForm.select_widget,
            "notes": StyledModelForm.note_widget,
        }
        labels = {
            "title": "Purpose",
            "vendor": "Vendor / Paid To",
        }


class ReconciliationOpeningBalanceForm(StyledModelForm):
    class Meta:
        model = ReconciliationOpeningBalance
        fields = ["balance_date", "amount"]
        widgets = {
            "balance_date": forms.HiddenInput(),
            "amount": StyledModelForm.money_widget,
        }
        labels = {
            "amount": "Opening Balance",
        }

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is not None and amount < 0:
            raise ValidationError("Amount cannot be less than zero.")
        return amount


class PurchaseForm(StyledModelForm):
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Supplier.objects.none()
        if user is not None:
            queryset = filter_queryset_by_role(Supplier.objects.all(), user)
        self.fields["supplier"].queryset = queryset
        self.fields["supplier"].required = False
        self.fields["supplier_name"].required = False
        self.fields["invoice_number"].required = True
        self.fields["purchase_type"].required = True
        self.fields["total_amount"].required = True
        self.fields["paid_amount"].required = True
        current_purchase_type = (
            (self.data.get(self.add_prefix("purchase_type")) if self.is_bound else "")
            or self.initial.get("purchase_type")
            or getattr(self.instance, "purchase_type", "")
        ).strip()
        purchase_type_choices = [
            ("", "Select Type"),
            ("Cash", "Cash"),
            ("Card", "Card"),
        ]
        if current_purchase_type and current_purchase_type not in {
            value for value, _label in purchase_type_choices
        }:
            purchase_type_choices.append((current_purchase_type, current_purchase_type))
        self.fields["purchase_type"].widget.choices = purchase_type_choices
        self.fields["purchase_type"].help_text = "Choose Cash or Card."

    class Meta:
        model = PurchaseRecord
        fields = [
            "supplier",
            "supplier_name",
            "purchase_type",
            "invoice_number",
            "total_amount",
            "paid_amount",
            "attachment",
            "notes",
        ]
        widgets = {
            "supplier": StyledModelForm.select_widget,
            "supplier_name": StyledModelForm.text_widget,
            "purchase_type": StyledModelForm.select_widget,
            "invoice_number": StyledModelForm.text_widget,
            "total_amount": StyledModelForm.money_widget,
            "paid_amount": StyledModelForm.money_widget,
            "attachment": forms.ClearableFileInput(attrs={"class": "input-control"}),
            "notes": StyledModelForm.note_widget,
        }
        labels = {
            "supplier": "Saved Supplier",
            "supplier_name": "Supplier Name",
            "purchase_type": "Type",
            "invoice_number": "Invoice Number",
            "total_amount": "Total Amount",
            "paid_amount": "Paid Amount",
            "attachment": "Purchase File",
        }
        help_texts = {
            "supplier": "Optional. Select a saved supplier if this purchase belongs to one.",
            "supplier_name": "Required if you do not choose a saved supplier.",
            "paid_amount": "Enter the amount already paid for this invoice. Pending is calculated automatically.",
            "attachment": "Upload bill copy, invoice PDF, image, or other purchase file.",
        }

    def clean(self):
        cleaned_data = super().clean()
        supplier = cleaned_data.get("supplier")
        supplier_name = (cleaned_data.get("supplier_name") or "").strip()
        total_amount = cleaned_data.get("total_amount")
        paid_amount = cleaned_data.get("paid_amount")

        if not supplier and not supplier_name:
            self.add_error("supplier_name", "Supplier name is required.")

        if total_amount is not None and paid_amount is not None and paid_amount > total_amount:
            self.add_error("paid_amount", "Paid amount cannot be greater than total amount.")

        return cleaned_data

    def save(self, commit=True):
        purchase = super().save(commit=False)
        if purchase.supplier_id:
            purchase.supplier_name = purchase.supplier.name
        else:
            purchase.supplier_name = self.cleaned_data["supplier_name"].strip()

        if commit:
            purchase.save()
        return purchase


class PurchasePaymentForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={
                "class": "input-control",
                "step": "0.01",
                "min": "0.01",
                "placeholder": "Enter payment amount",
            }
        ),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "input-control textarea-control",
                "rows": 3,
                "placeholder": "Optional note for this payment",
            }
        ),
    )

    def __init__(self, *args, purchase=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.purchase = purchase

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise ValidationError("Payment amount must be greater than zero.")
        return amount

    def clean(self):
        cleaned_data = super().clean()
        amount = cleaned_data.get("amount")

        if self.purchase is None or amount is None:
            return cleaned_data

        if self.purchase.pending_amount <= 0:
            raise ValidationError("This purchase is already fully paid.")

        if amount > self.purchase.pending_amount:
            self.add_error(
                "amount",
                f"Payment amount cannot be greater than the pending amount of Rs. {self.purchase.pending_amount}.",
            )

        return cleaned_data


class SupplierForm(StyledModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].required = True
        self.fields["contact_person"].required = True
        self.fields["phone_number"].required = True

    class Meta:
        model = Supplier
        fields = [
            "name",
            "contact_person",
            "phone_number",
            "email",
            "address",
            "gstin_number",
            "fssai_number",
            "pan_number",
            "credit_terms",
            "opening_balance",
            "bank_name",
            "account_number",
            "ifsc_code",
            "status",
            "notes",
        ]
        widgets = {
            "name": StyledModelForm.text_widget,
            "contact_person": StyledModelForm.text_widget,
            "phone_number": StyledModelForm.text_widget,
            "email": forms.EmailInput(attrs={"class": "input-control"}),
            "address": StyledModelForm.note_widget,
            "gstin_number": StyledModelForm.text_widget,
            "fssai_number": StyledModelForm.text_widget,
            "pan_number": StyledModelForm.text_widget,
            "credit_terms": StyledModelForm.text_widget,
            "opening_balance": StyledModelForm.money_widget,
            "bank_name": StyledModelForm.text_widget,
            "account_number": StyledModelForm.text_widget,
            "ifsc_code": StyledModelForm.text_widget,
            "status": StyledModelForm.select_widget,
            "notes": StyledModelForm.note_widget,
        }
        labels = {
            "name": "Supplier Name",
            "phone_number": "Phone Number",
            "gstin_number": "GSTIN Number",
            "fssai_number": "FSSAI Number",
            "pan_number": "PAN Number",
            "ifsc_code": "IFSC Code",
        }


class UserManagementForm(forms.ModelForm):
    role = forms.ChoiceField(
        choices=USER_ROLE_CHOICES,
        widget=forms.Select(attrs={"class": "input-control"}),
    )
    master_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"class": "input-control"}),
        label="Master Name",
    )
    status = forms.ChoiceField(
        choices=[("active", "Active"), ("inactive", "Inactive")],
        widget=forms.Select(attrs={"class": "input-control"}),
    )
    password1 = forms.CharField(
        required=False,
        label="Password",
        widget=forms.PasswordInput(
            attrs={"class": "input-control", "placeholder": "Enter password"}
        ),
    )
    password2 = forms.CharField(
        required=False,
        label="Confirm Password",
        widget=forms.PasswordInput(
            attrs={"class": "input-control", "placeholder": "Confirm password"}
        ),
    )

    class Meta:
        model = User
        fields = ["username"]
        widgets = {
            "username": forms.TextInput(attrs={"class": "input-control"}),
        }
        labels = {
            "username": "User Name",
        }

    def __init__(self, *args, require_password=True, current_user=None, **kwargs):
        self.require_password = require_password
        self.current_user = current_user
        super().__init__(*args, **kwargs)
        self.fields["username"].required = True
        self.fields["master_name"].required = True
        self.fields["role"].initial = USER_ROLE_STAFF
        self.fields["status"].initial = "active"

        if not require_password:
            self.fields["password1"].help_text = "Leave blank to keep the current password."
            self.fields["password2"].help_text = "Leave blank to keep the current password."

        if self.instance and self.instance.pk:
            profile = getattr(self.instance, "account_profile", None)
            self.fields["master_name"].initial = (
                profile.master_name if profile and profile.master_name else ""
            )
            self.fields["role"].initial = get_user_role(self.instance)
            self.fields["status"].initial = "active" if self.instance.is_active else "inactive"

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if not username:
            raise ValidationError("User name is required.")

        existing_users = User.objects.filter(username__iexact=username)
        if self.instance.pk:
            existing_users = existing_users.exclude(pk=self.instance.pk)
        if existing_users.exists():
            raise ValidationError("This user name already exists.")
        return username

    def clean_master_name(self):
        master_name = self.cleaned_data["master_name"].strip()
        if not master_name:
            raise ValidationError("Master name is required.")
        return master_name

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password1", "")
        confirm_password = cleaned_data.get("password2", "")

        if self.require_password or password or confirm_password:
            if not password:
                self.add_error("password1", "Password is required.")
            if not confirm_password:
                self.add_error("password2", "Confirm password is required.")
            if password and confirm_password and password != confirm_password:
                self.add_error("password2", "Passwords do not match.")

        if (
            self.instance.pk
            and self.current_user
            and self.instance.pk == self.current_user.pk
        ):
            if cleaned_data.get("status") == "inactive":
                self.add_error("status", "You cannot deactivate your own account.")
            if cleaned_data.get("role") != "admin":
                self.add_error("role", "You cannot remove your own admin access.")

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data["username"]
        user.is_active = self.cleaned_data["status"] == "active"
        apply_user_role(user, self.cleaned_data["role"])

        password = self.cleaned_data.get("password1")
        if password:
            user.set_password(password)

        if commit:
            user.save()
            profile, _ = UserAccountProfile.objects.get_or_create(user=user)
            profile.master_name = self.cleaned_data["master_name"]
            profile.save()
            UserModulePermission.objects.get_or_create(user=user)

        return user
