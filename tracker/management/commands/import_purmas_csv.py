import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tracker.models import ExpenseRecord, PaymentMethod, Supplier, SupplierStatus


def clean_text(value):
    text = (value or "").strip()
    if text in {"", '""'}:
        return ""
    return text


def parse_date(value):
    text = clean_text(value)
    if not text or text.startswith("1900-01-01"):
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_decimal(value):
    text = clean_text(value)
    if not text:
        return Decimal("0.00")
    try:
        return Decimal(text)
    except InvalidOperation:
        return Decimal("0.00")


def get_target_user(username):
    user_model = get_user_model()
    if username:
        try:
            return user_model.objects.get(username=username)
        except user_model.DoesNotExist as exc:
            raise CommandError(f"User '{username}' does not exist.") from exc

    users = list(user_model.objects.all())
    if len(users) == 1:
        return users[0]
    if not users:
        raise CommandError("No users found. Create a user first or pass --username.")
    raise CommandError("Multiple users found. Pass --username to choose the import owner.")


class Command(BaseCommand):
    help = "Import supplier and purchase data from a PurMas purchase master CSV."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", type=str, help="Path to PurMas CSV file")
        parser.add_argument(
            "--username",
            type=str,
            default="",
            help="Django username that should own the imported data",
        )

    def handle(self, *args, **options):
        csv_path = Path(options["csv_path"])
        if not csv_path.exists():
            raise CommandError(f"CSV file not found: {csv_path}")

        user = get_target_user(options["username"])

        created_suppliers = 0
        updated_suppliers = 0
        created_expenses = 0
        skipped_rows = 0

        self.stdout.write(
            self.style.WARNING(
                "No explicit remaining/due amount column was found in this CSV. "
                "Importing PurMas_NetAmt as the expense amount."
            )
        )

        with csv_path.open(newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)

            for row in reader:
                if clean_text(row.get("PurMas_Cancel")) == "1":
                    skipped_rows += 1
                    continue

                supplier_name = clean_text(row.get("PurMas_Add1")) or clean_text(
                    row.get("PurMas_Party")
                )
                if not supplier_name:
                    skipped_rows += 1
                    continue

                address_parts = [
                    clean_text(row.get("PurMas_Add2")),
                    clean_text(row.get("PurMas_Add3")),
                    clean_text(row.get("PurMas_Add4")),
                    clean_text(row.get("PurMas_Add5")),
                    clean_text(row.get("PurMas_Pincode")),
                ]
                address = ", ".join(part for part in address_parts if part)
                phone_number = clean_text(row.get("PurMas_Cell")) or "Not Provided"
                supplier_defaults = {
                    "contact_person": supplier_name,
                    "phone_number": phone_number,
                    "email": clean_text(row.get("PurMas_EMail")),
                    "address": address,
                    "gstin_number": clean_text(row.get("PurMas_GST")),
                    "status": SupplierStatus.ACTIVE,
                }

                supplier, supplier_created = Supplier.objects.get_or_create(
                    user=user,
                    name=supplier_name,
                    defaults=supplier_defaults,
                )
                if supplier_created:
                    created_suppliers += 1
                else:
                    changed = False
                    for field_name, field_value in supplier_defaults.items():
                        if field_value and not getattr(supplier, field_name):
                            setattr(supplier, field_name, field_value)
                            changed = True
                    if changed:
                        supplier.save()
                        updated_suppliers += 1

                amount = parse_decimal(row.get("PurMas_NetAmt"))
                if amount <= 0:
                    skipped_rows += 1
                    continue

                transaction_date = parse_date(row.get("PurMas_VouDate")) or parse_date(
                    row.get("PurMas_Date")
                )
                if transaction_date is None:
                    skipped_rows += 1
                    continue

                bill_no = clean_text(row.get("PurMas_BillNo"))
                voucher_no = clean_text(row.get("PurMas_VouNo"))
                remarks = clean_text(row.get("PurMas_Remarks"))
                party_reference = clean_text(row.get("PurMas_Party"))
                title = f"Purchase - {bill_no}" if bill_no else f"Purchase - {supplier_name}"
                note_parts = [
                    "Imported from PurMas CSV",
                    f"Bill No: {bill_no}" if bill_no else "",
                    f"Voucher No: {voucher_no}" if voucher_no else "",
                    f"Party Ref: {party_reference}" if party_reference else "",
                    f"GST: {supplier.gstin_number}" if supplier.gstin_number else "",
                    f"Remarks: {remarks}" if remarks else "",
                ]
                notes = " | ".join(part for part in note_parts if part)

                source_reference = f"PurMasCSV:{clean_text(row.get('PurMas_SNo'))}"
                expense_exists = ExpenseRecord.objects.filter(
                    user=user,
                    source_reference=source_reference,
                ).exists()
                if expense_exists:
                    skipped_rows += 1
                    continue

                ExpenseRecord.objects.create(
                    user=user,
                    title=title,
                    supplier=supplier,
                    source_reference=source_reference,
                    vendor=supplier.name,
                    category="Purchase",
                    amount=amount,
                    transaction_date=transaction_date,
                    payment_method=PaymentMethod.OTHER,
                    notes=notes,
                )
                created_expenses += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Import completed: "
                f"{created_suppliers} suppliers created, "
                f"{updated_suppliers} suppliers updated, "
                f"{created_expenses} expenses created, "
                f"{skipped_rows} rows skipped."
            )
        )
