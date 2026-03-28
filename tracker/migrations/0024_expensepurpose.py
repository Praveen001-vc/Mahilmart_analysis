from django.conf import settings
from django.db import migrations, models


COUNTER_EXPENSE_CATEGORY = "Counter Expense"
DEFAULT_EXPENSE_CATEGORY_PURPOSES = {
    "Purchase / Inventory Expenses": [
        "Grocery Purchase (Rice, oil, spices, etc.)",
        "Fruits & Vegetables",
        "Dairy Products",
        "Beverages",
        "Frozen Items",
        "Bakery Items",
        "Household Products (detergents, cleaners)",
        "Personal Care Items",
    ],
    "Utility Expenses": [
        "Electricity Bill",
        "Water Bill",
        "Internet / Wi-Fi",
        "Gas",
    ],
    "Staff & Salary Expenses": [
        "Employee Salaries",
        "Wages (Daily Workers)",
        "Overtime Pay",
        "Staff Incentives / Bonus",
    ],
    "Shop Maintenance": [
        "Cleaning Supplies",
        "Repairs & Maintenance",
        "AC Service",
        "Equipment Maintenance",
    ],
    "Transportation & Logistics": [
        "Goods Transport Charges",
        "Fuel Expenses",
        "Delivery Charges",
        "Loading / Unloading Charges",
    ],
    "Packaging & Supplies": [
        "Carry Bags (Plastic / Paper)",
        "Packaging Materials",
        "Labels / Stickers",
        "Billing Paper Rolls",
    ],
    "Marketing & Promotions": [
        "Advertisement (Online / Offline)",
        "Banner / Flex Printing",
        "Offer Promotions",
        "Social Media Marketing",
    ],
    "Financial Expenses": [
        "Bank Charges",
        "POS Machine Charges",
        "Loan EMI",
        "GST / Taxes",
    ],
    "Software & System": [
        "Billing Software Subscription",
        "POS System Maintenance",
        "Hardware (Scanner, Printer)",
        "Cloud / Hosting Charges",
    ],
    "Miscellaneous Expenses": [
        "Security (Guard / CCTV)",
        "Stationery",
        "Small Misc Expenses",
        "Emergency Expenses",
    ],
}


def normalize_short_text(value, max_length):
    return " ".join(str(value or "").strip().split())[:max_length]


def build_default_purpose_map():
    counter_purposes = []
    seen_counter_names = set()

    for purpose_options in DEFAULT_EXPENSE_CATEGORY_PURPOSES.values():
        for raw_name in purpose_options:
            normalized_name = normalize_short_text(raw_name, 120)
            normalized_key = normalized_name.casefold()
            if not normalized_name or normalized_key in seen_counter_names:
                continue
            seen_counter_names.add(normalized_key)
            counter_purposes.append(normalized_name)

    return {
        COUNTER_EXPENSE_CATEGORY: counter_purposes,
        **{
            normalize_short_text(category_name, 80): [
                normalize_short_text(purpose_name, 120)
                for purpose_name in purpose_options
                if normalize_short_text(purpose_name, 120)
            ]
            for category_name, purpose_options in DEFAULT_EXPENSE_CATEGORY_PURPOSES.items()
        },
    }


def populate_expense_purposes(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    ExpensePurpose = apps.get_model("tracker", "ExpensePurpose")
    ExpenseRecord = apps.get_model("tracker", "ExpenseRecord")

    purpose_rows_to_create = []
    seen_keys = set()
    default_purpose_map = build_default_purpose_map()

    for user_id in User.objects.values_list("id", flat=True):
        for category_name, purpose_options in default_purpose_map.items():
            for purpose_name in purpose_options:
                purpose_key = (
                    user_id,
                    category_name.casefold(),
                    purpose_name.casefold(),
                )
                if purpose_key in seen_keys:
                    continue
                seen_keys.add(purpose_key)
                purpose_rows_to_create.append(
                    ExpensePurpose(
                        user_id=user_id,
                        category=category_name,
                        name=purpose_name,
                    )
                )

    for row in ExpenseRecord.objects.exclude(category="").exclude(title="").values(
        "user_id",
        "category",
        "title",
    ):
        normalized_category = normalize_short_text(row["category"], 80)
        normalized_name = normalize_short_text(row["title"], 120)
        if not normalized_category or not normalized_name:
            continue
        purpose_key = (
            row["user_id"],
            normalized_category.casefold(),
            normalized_name.casefold(),
        )
        if purpose_key in seen_keys:
            continue
        seen_keys.add(purpose_key)
        purpose_rows_to_create.append(
            ExpensePurpose(
                user_id=row["user_id"],
                category=normalized_category,
                name=normalized_name,
            )
        )

    ExpensePurpose.objects.bulk_create(purpose_rows_to_create, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tracker", "0023_reconciliationopeningbalance"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExpensePurpose",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("category", models.CharField(db_index=True, max_length=80)),
                ("name", models.CharField(max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="expense_purposes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["category", "name", "created_at"],
                "unique_together": {("user", "category", "name")},
            },
        ),
        migrations.AddIndex(
            model_name="expensepurpose",
            index=models.Index(fields=["user", "category"], name="tracker_exp_user_id_2ad299_idx"),
        ),
        migrations.RunPython(populate_expense_purposes, migrations.RunPython.noop),
    ]
