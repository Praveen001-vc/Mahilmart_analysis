from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0026_remove_seeded_income_purposes"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchasepayment",
            name="payment_method",
            field=models.CharField(
                choices=[
                    ("Cash", "Cash"),
                    ("Card", "Card"),
                    ("Bank Transfer", "Bank Transfer"),
                    ("UPI", "UPI"),
                    ("Other", "Other"),
                ],
                default="Cash",
                max_length=20,
            ),
        ),
    ]
