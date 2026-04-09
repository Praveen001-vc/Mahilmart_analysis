from .models import ExpenseRecord, PaymentMethod


def sync_purchase_to_expense(purchase):
    latest_payment = purchase.payments.order_by("-payment_date", "-created_at", "-pk").first()
    expense_payment_method = (
        latest_payment.payment_method if latest_payment else PaymentMethod.OTHER
    )
    extra_notes = purchase.notes.strip()
    notes = [
        "Auto-created from purchase entry",
        f"Invoice No: {purchase.invoice_number}",
        f"Purchase Type: {purchase.purchase_type}",
        f"Paid Amount: {purchase.paid_amount}",
        f"Pending Amount: {purchase.pending_amount}",
    ]
    if latest_payment:
        notes.append(f"Last Payment Method: {latest_payment.payment_method}")
    if extra_notes:
        notes.append(f"Purchase Notes: {extra_notes}")

    ExpenseRecord.objects.update_or_create(
        user=purchase.user,
        source_reference=f"PURCHASE:{purchase.source_reference}",
        defaults={
            "title": f"Purchase - {purchase.invoice_number}",
            "supplier": purchase.supplier,
            "vendor": purchase.supplier_name,
            "category": "Purchase",
            "amount": purchase.total_amount,
            "transaction_date": purchase.transaction_date,
            "payment_method": expense_payment_method,
            "notes": " | ".join(notes),
        },
    )
