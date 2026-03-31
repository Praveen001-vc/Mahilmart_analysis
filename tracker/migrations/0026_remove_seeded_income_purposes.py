from django.db import migrations


SEEDED_INCOME_PURPOSES = (
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


def remove_seeded_income_purposes(apps, schema_editor):
    IncomePurpose = apps.get_model("tracker", "IncomePurpose")
    IncomePurpose.objects.filter(name__in=SEEDED_INCOME_PURPOSES).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0025_incomepurpose"),
    ]

    operations = [
        migrations.RunPython(
            remove_seeded_income_purposes,
            migrations.RunPython.noop,
        ),
    ]
