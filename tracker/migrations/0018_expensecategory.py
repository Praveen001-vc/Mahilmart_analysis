from django.conf import settings
from django.db import migrations, models


def populate_expense_categories(apps, schema_editor):
    ExpenseCategory = apps.get_model("tracker", "ExpenseCategory")
    ExpenseRecord = apps.get_model("tracker", "ExpenseRecord")

    categories_to_create = []
    seen = set()

    for row in ExpenseRecord.objects.exclude(category="").values("user_id", "category").distinct():
        normalized_name = " ".join(str(row["category"] or "").strip().split())[:80]
        if not normalized_name:
            continue
        key = (row["user_id"], normalized_name.casefold())
        if key in seen:
            continue
        seen.add(key)
        categories_to_create.append(
            ExpenseCategory(user_id=row["user_id"], name=normalized_name)
        )

    ExpenseCategory.objects.bulk_create(categories_to_create, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tracker", "0017_dailycashsettlement_cash_denominations"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExpenseCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=80)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="expense_categories",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["name", "created_at"],
                "unique_together": {("user", "name")},
            },
        ),
        migrations.RunPython(populate_expense_categories, migrations.RunPython.noop),
    ]
