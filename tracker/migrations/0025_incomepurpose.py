from django.conf import settings
from django.db import migrations, models


INCOME_CATEGORY_COUNTER = "Counter Income"
INCOME_CATEGORY_OFFICE = "Office Income"
DEFAULT_INCOME_PURPOSES = (
    "Product Sales",
    "Wholesale Sales",
    "Online Orders Income",
    "Home Delivery Charges",
    "Supplier Discounts Received",
    "Purchase Returns Income",
    "Commission Income",
    "Rental Income (if any space rented)",
    "Scrap Sales (damaged/old items)",
    "Cashback / Offers Received",
    "Service Charges",
    "Office Income",
    "Other Income",
)


def normalize_short_text(value, max_length):
    return " ".join(str(value or "").strip().split())[:max_length]


def normalize_income_category_name(value):
    normalized = normalize_short_text(value, 80)
    normalized_casefold = normalized.casefold()
    if normalized_casefold == INCOME_CATEGORY_COUNTER.casefold():
        return INCOME_CATEGORY_COUNTER
    if normalized_casefold == INCOME_CATEGORY_OFFICE.casefold():
        return INCOME_CATEGORY_OFFICE
    return normalized


def normalize_income_purpose_name(value):
    return normalize_short_text(value, 120)


def build_default_purpose_map():
    return {
        INCOME_CATEGORY_COUNTER: [
            normalize_income_purpose_name(purpose_name)
            for purpose_name in DEFAULT_INCOME_PURPOSES
            if normalize_income_purpose_name(purpose_name)
        ],
        INCOME_CATEGORY_OFFICE: [
            normalize_income_purpose_name(purpose_name)
            for purpose_name in DEFAULT_INCOME_PURPOSES
            if normalize_income_purpose_name(purpose_name)
        ],
    }


def populate_income_purposes(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    IncomePurpose = apps.get_model("tracker", "IncomePurpose")
    IncomeRecord = apps.get_model("tracker", "IncomeRecord")

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
                    IncomePurpose(
                        user_id=user_id,
                        category=category_name,
                        name=purpose_name,
                    )
                )

    for row in IncomeRecord.objects.exclude(title="").values(
        "user_id",
        "category",
        "title",
    ):
        normalized_category = normalize_income_category_name(row["category"])
        if normalized_category not in (
            INCOME_CATEGORY_COUNTER,
            INCOME_CATEGORY_OFFICE,
        ):
            normalized_category = INCOME_CATEGORY_COUNTER
        normalized_name = normalize_income_purpose_name(row["title"])
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
            IncomePurpose(
                user_id=row["user_id"],
                category=normalized_category,
                name=normalized_name,
            )
        )

    IncomePurpose.objects.bulk_create(purpose_rows_to_create, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tracker", "0024_expensepurpose"),
    ]

    operations = [
        migrations.CreateModel(
            name="IncomePurpose",
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
                        related_name="income_purposes",
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
            model_name="incomepurpose",
            index=models.Index(fields=["user", "category"], name="tracker_inc_user_cat_idx"),
        ),
        migrations.RunPython(populate_income_purposes, migrations.RunPython.noop),
    ]
