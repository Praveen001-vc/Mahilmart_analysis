# Calculation Documentation

## Scope

This document explains only the calculation logic used in the project.

Main sources:

- `tracker/models.py`
- `tracker/views.py`
- `tracker/forms.py`
- `templates/tracker/purchase_form.html`
- `templates/tracker/sales_list.html`
- `templates/tracker/daily_settlement.html`

All saved money fields use Django `DecimalField(..., decimal_places=2)`.
Frontend previews use JavaScript numbers for live display, but backend `Decimal` values are the source of truth.

## 1. Shared Calculation Helpers

### `_sum_amount(queryset)`

Formula:

```text
sum(amount) or 0.00
```

Explanation:

- Used for `IncomeRecord.amount`, `ExpenseRecord.amount`, and similar manual totals.
- If the queryset is empty, the function returns `0.00` instead of `None`.

### `_sum_settlement_income(queryset)`

Formula:

```text
sum(actual_sales) or 0.00
```

Explanation:

- Used when the system wants settlement-based sales totals.
- Important: this sums `DailyCashSettlement.actual_sales`, not `cash_settled + gpay_settled`.

### `parse_money_value(value)`

Rule:

```text
blank / invalid -> 0.00
valid number -> Decimal(value)
```

Explanation:

- Protects calculations from empty strings, invalid text, and `None`.

### `normalize_count_value(value)` and `_normalize_cash_denomination_count(value)`

Rule:

```text
count = int(value)
count cannot go below 0
```

Explanation:

- Used for cash denomination counts.
- Negative or invalid counts are converted to `0`.

## 2. Purchase Calculations

### `PurchaseRecord.save()`

Formula:

```text
pending_amount = total_amount - paid_amount
```

Explanation:

- `pending_amount` is always recalculated when the purchase record is saved.
- The user does not directly control the stored pending value.

Example:

```text
total_amount = 1000.00
paid_amount = 250.00
pending_amount = 750.00
```

### `PurchaseForm.clean()`

Validation rule:

```text
paid_amount must be <= total_amount
```

Explanation:

- Prevents saving a purchase where paid money is greater than the invoice total.

### `PurchasePaymentForm.clean()`

Validation rules:

```text
amount > 0
amount <= purchase.pending_amount
```

Explanation:

- Blocks zero, negative, and overpayment entries.

### `create_purchase_payment(...)`

Formula:

```text
purchase.paid_amount = purchase.paid_amount + payment_amount
purchase.pending_amount = total_amount - new_paid_amount
```

Explanation:

- The helper creates a `PurchasePayment` row.
- Then it increases `paid_amount`.
- `pending_amount` is recalculated by `PurchaseRecord.save()`.

### `build_purchase_payment_history(purchase)`

Running formulas:

```text
running_paid = sum(all payment entries so far)
running_pending = purchase.total_amount - running_paid
legacy_balance = purchase.paid_amount - tracked_total
```

Explanation:

- Builds a date-wise running payment history.
- If old data exists in `paid_amount` but not in `PurchasePayment` rows, that difference becomes an "Opening Paid Amount" entry.

### Purchase form live preview

Source: `templates/tracker/purchase_form.html`

Formula:

```text
pending_preview = max(total_amount - paid_amount, 0)
```

Explanation:

- This is only a UI preview.
- The real saved value still comes from `PurchaseRecord.save()`.

## 3. Sales Ledger Calculations

### `SalesLedgerRecord.split_total_amount`

Formula:

```text
split_total_amount = split_cash_amount + split_card_amount
```

### `SalesLedgerRecord.effective_received_amount`

Rules:

```text
if manual split:
    effective_received_amount = min(net_amount, split_total_amount)
elif payment_mode == Card:
    effective_received_amount = net_amount
elif received_amount > 0:
    effective_received_amount = min(net_amount, received_amount)
else:
    effective_received_amount = 0.00
```

Explanation:

- Manual split overrides normal payment mode logic.
- Card bills are treated as fully received.
- Received amount never exceeds the bill's `net_amount`.

### `SalesLedgerRecord.effective_balance_amount`

Rules:

```text
if manual split:
    effective_balance_amount = max(net_amount - effective_received_amount, 0.00)
elif payment_mode == Card:
    effective_balance_amount = 0.00
elif balance_amount > 0:
    effective_balance_amount = min(net_amount, balance_amount)
else:
    effective_balance_amount = max(net_amount - effective_received_amount, 0.00)
```

Explanation:

- Card bills are always treated as cleared.
- Manual split calculates pending from bill total minus split total.
- Raw `balance_amount` is capped at `net_amount`.

Example:

```text
net_amount = 450.00
split_cash_amount = 300.00
split_card_amount = 150.00
effective_received_amount = 450.00
effective_balance_amount = 0.00
```

### `get_effective_sales_payment_amount(record)`

Formula:

```text
returns record.effective_received_amount
```

### `get_effective_sales_balance_amount(record)`

Formula:

```text
returns record.effective_balance_amount
```

These are wrapper helpers used throughout views.

### Sales split modal live preview

Source: `templates/tracker/sales_list.html`

Formulas:

```text
split_total = cash_amount + card_amount
save disabled if split_total > net_amount
```

Explanation:

- The split cannot exceed the bill total.
- `0` means "clear manual split".
- Full cash and full card are just shortcuts where one side equals `net_amount`.

## 4. Cash Denomination Calculations

### `get_cash_denominations_total(denominations)`

Formula:

```text
total = sum(denomination * count for each allowed denomination)
```

Allowed denominations:

```text
2000, 500, 200, 100, 50, 20, 10, 5, 2, 1
```

Example:

```text
{"500": 1, "200": 2, "50": 1}
= 500 + 400 + 50
= 950.00
```

### `get_cash_difference_amount(cash_denomination_total, cash_in_hand)`

Formula:

```text
cash_difference = cash_denomination_total - cash_in_hand
```

Explanation:

- Positive value means counted cash is more than expected cash in hand.
- Negative value means shortage.

### `build_cash_denomination_rows(denominations)`

Per row formula:

```text
row_total = denomination * count
```

Explanation:

- Used only for display in the denomination popup and summary list.

## 5. Daily Settlement Calculations

## 5.1 Key meaning of settlement fields

These names are easy to confuse:

- `sales_ledger_cash`: cash sales collected from the sales ledger for the day
- `gpay_settled`: card/UPI amount counted as settled sales for the day
- `cash_settled`: cash physically handed over during settlement
- `cash_in_hand`: cash remaining before handover, after expenses
- `closing_balance`: cash kept after handover and shortage adjustment
- `actual_sales`: day sales value used for sales reporting

### `get_sales_cash_from_settlement(settlement)`

Formula:

```text
sales_cash = actual_sales - gpay_settled
```

Explanation:

- A saved settlement does not store `sales_ledger_cash` directly.
- This helper reconstructs it from `actual_sales`.

### `get_cash_in_hand_amount(opening_balance, sales_ledger_cash, expense_amount)`

Formula:

```text
cash_in_hand = opening_balance + sales_ledger_cash - expense_amount
```

Explanation:

- This is the expected cash available before the user enters how much cash was handed over.

### `get_settlement_closing_balance(...)`

Formulas:

```text
cash_in_hand = opening_balance + sales_ledger_cash - expense_amount
cash_difference = cash_denomination_total - cash_in_hand
shortage_adjustment = min(cash_difference, 0.00)
closing_balance = (cash_in_hand - cash_settled) + shortage_adjustment
```

Explanation:

- If counted cash is short, closing balance is reduced by that shortage.
- If counted cash is extra, the extra does not increase closing balance.
- In other words, shortage affects closing balance, overcount only appears in `cash_difference`.

Example:

```text
opening_balance = 741.00
sales_ledger_cash = 0.00
expense_amount = 200.00
cash_in_hand = 541.00
cash_settled = 500.00
cash_denomination_total = 541.00
cash_difference = 0.00
closing_balance = 41.00
```

### `build_sales_settlement_summary(settlement_date)`

Algorithm:

```text
for each non-cancelled sales record on the date:
    if manual split exists:
        cash_total += split_cash_amount
        gpay_total += split_card_amount
    else:
        effective_amount = effective_received_amount
        if payment_mode == Card:
            gpay_total += effective_amount
        elif payment_mode == Cash:
            cash_total += effective_amount
```

Returned totals:

```text
gpay_settled
cash_settled
sales_count
manual_split_count
```

Explanation:

- Manual split bills use the entered split amounts.
- Card bills add to digital settlement.
- Cash bills add to cash settlement.
- Credit/unknown bills without split do not add to settled totals.

### `get_default_settlement_opening_balance(user, settlement_date)`

Rules:

```text
if previous settlement exists:
    opening_balance = previous_settlement.closing_balance
else:
    opening_balance = sum(manual income before date) - sum(expense before date)
```

Explanation:

- Previous settlement closing balance has first priority.
- If no previous settlement exists, the system derives a starting balance from earlier manual records.

### `build_settlement_autofill_summary(user, settlement_date)`

Main formulas:

```text
opening_balance = previous_closing_balance or default_opening_balance
sales_ledger_cash = sales_summary.cash_settled
gpay_settled = sales_summary.gpay_settled if sales exist else 0.00
expense_amount = sum(counter expenses on settlement date)
cash_in_hand = opening_balance + sales_ledger_cash - expense_amount
```

Explanation:

- Only expenses in the `Counter Expense` category are included here.
- If sales ledger data exists for the date, the settlement source becomes `sales`.
- If not, the screen falls back to manual entry for `gpay_settled`.

### `build_settlement_preview(values, cash_denomination_total)`

Formulas:

```text
cash_in_hand = opening_balance + sales_ledger_cash - expense_amount
closing_balance = get_settlement_closing_balance(...)
total_amount = opening_balance + sales_ledger_cash + gpay_settled
actual_sales = total_amount - opening_balance
```

Simplified:

```text
actual_sales = sales_ledger_cash + gpay_settled
```

Explanation:

- `total_amount` is the running balance after adding the day's sales.
- `actual_sales` removes the opening balance and leaves only the day's sales value.

### `DailyCashSettlement.save()`

Default model formulas:

```text
cash_denomination_total = sum(denomination * count)

if no expected values are injected:
    cash_in_hand = cash_settled + closing_balance
    total_amount = gpay_settled + cash_settled + expense_amount + closing_balance
    actual_sales = total_amount - opening_balance

cash_difference = cash_denomination_total - cash_in_hand
```

Important behavior:

- `DailySettlementView.post()` injects `_expected_cash_in_hand`, `_expected_total_amount`, and `_expected_actual_sales` before saving.
- That keeps the stored values aligned with the preview formulas shown on the page.

### Daily settlement page live preview

Source: `templates/tracker/daily_settlement.html`

Formulas:

```text
total = opening_balance + salesCash + gpay
cashInHand = opening_balance + salesCash - expense
closing = (cashInHand - cashSettled) + min(denominationTotal - cashInHand, 0)
actualSales = total - opening_balance
diff = denominationTotal - cashInHand
```

Explanation:

- This mirrors the backend settlement preview logic.

## 6. Reconciliation Calculations

## 6.1 Reconciliation income side

### `build_reconciliation_income_entries(user, filter_values)`

Summary formulas:

```text
manual_income_total = sum(manual income amount)
manual_cash_income_total = sum(manual income where payment_method == Cash)
settlement_income_total = sum(cash_settled + gpay_settled for settlement rows)
settlement_cash_total = sum(cash_settled)
settlement_card_total = sum(gpay_settled)
```

Explanation:

- Reconciliation does not use `actual_sales` here.
- It uses `cash_settled` and `gpay_settled` because this screen focuses on money movement and carry-forward balance.

## 6.2 Reconciliation expense side

### `get_reconciliation_purchase_payment_method(purchase_type)`

Rules:

```text
blank or unknown purchase_type -> Cash
exact Cash -> Cash
exact Card / UPI / Bank Transfer / Other -> same value
```

Explanation:

- Used to decide whether a purchase reduces cash balance or non-cash balance.

### `build_reconciliation_expense_entries(user, filter_values)`

Summary formulas:

```text
manual_expense_total = sum(manual expense amount)
manual_cash_expense_total = sum(manual expenses paid by Cash)
manual_non_cash_expense_total = sum(manual expenses paid by non-cash methods)
purchase_cash_total = sum(purchase.paid_amount for cash purchases)
purchase_non_cash_total = sum(purchase.paid_amount for non-cash purchases)
purchase_total = sum(purchase.paid_amount)
```

Explanation:

- Reconciliation uses `paid_amount`, not `total_amount`, for purchases.
- This is because reconciliation tracks what has actually been paid out.

## 6.3 Opening, closing, and carry-forward balance

### `get_reconciliation_opening_balance_for_date(user, target_date)`

Priority order:

```text
1. saved ReconciliationOpeningBalance.amount
2. first ReconciliationIncomeEntry.opening_balance on that date if > 0
3. 0.00 if this is before or on the first activity date
4. previous day's reconciliation closing balance
```

Explanation:

- The function falls back to previous-day carry-forward when no direct opening balance is stored.

### `get_reconciliation_closing_balance_for_date(user, target_date)`

Formula:

```text
closing_balance =
    opening_balance
    + manual_income_total
    + settlement_income_total
    - manual_cash_expense_total
    - purchase_cash_total
```

Explanation:

- Only cash expenses reduce the reconciliation closing cash balance.
- Non-cash expense values are tracked separately, not removed from the cash carry-forward.

### `get_reconciliation_split_card_balance_for_date(user, target_date)`

Formula:

```text
split_card_balance_total =
    cumulative settlement gpay_settled
    + cumulative manual non-cash income
    - cumulative manual non-cash expense
```

Explanation:

- This represents the running non-cash side of the reconciliation summary.

## 6.4 Reconciliation page totals

Source: `ReconciliationWorkspaceView.get_context_data()`

Formulas:

```text
income_total =
    reconciliation_opening_balance
    + manual_income_total
    + settlement_income_total

cash_expense_total =
    manual_cash_expense_total
    + purchase_cash_total

non_cash_expense_total =
    manual_non_cash_expense_total
    + purchase_non_cash_total

expense_total =
    manual_expense_total
    + purchase_total

reconciliation_closing_balance =
    income_total - cash_expense_total

next_day_opening_balance =
    reconciliation_closing_balance

net_total =
    income_total - expense_total
```

Explanation:

- `reconciliation_closing_balance` is cash-focused.
- `net_total` includes both cash and non-cash expenses.
- Because of that, `reconciliation_closing_balance` and `net_total` are not always the same.

Example:

```text
opening_balance = 0.00
settlement_cash_total = 400.00
settlement_card_total = 250.00
manual_cash_expense_total = 120.00

income_total = 650.00
cash_expense_total = 120.00
reconciliation_closing_balance = 530.00
```

## 7. Dashboard Calculations

Source: `DashboardView.get_context_data()`

### Main dashboard totals

Formulas:

```text
income_total = sum(settlement.actual_sales up to selected_date)
expense_total = sum(expense.amount up to selected_date)
balance = income_total - expense_total
```

### Selected date totals

Formulas:

```text
selected_income = sum(settlement.actual_sales on selected_date)
selected_expense = sum(expense.amount on selected_date)
selected_day_balance = settlement.actual_sales on selected_date if settlement exists else 0.00
```

### Opening and closing balance

Rules:

```text
if settlement exists for selected date:
    opening_balance = settlement.opening_balance
    closing_balance = settlement.closing_balance
else:
    opening_balance = default settlement opening balance
    closing_balance = 0.00
```

### Monthly totals

Formulas:

```text
current_month_income = sum(settlement.actual_sales in selected month up to selected_date)
current_month_expense = sum(expense.amount in selected month up to selected_date)
```

### Savings rate

Formula:

```text
savings_rate = (balance / income_total) * 100
```

Rule:

```text
if income_total == 0:
    savings_rate = 0
```

Display clamp:

```text
savings_rate_meter = min(max(savings_rate, 0), 100)
```

### `build_monthly_overview(user, months=6, anchor_date=None)`

Per month formulas:

```text
income = sum(manual IncomeRecord.amount for the month)
expense = sum(ExpenseRecord.amount for the month)
balance = income - expense
income_width = (income / highest_total) * 100
expense_width = (expense / highest_total) * 100
```

Important note:

- This monthly helper uses manual `IncomeRecord` values only.
- It does not include `DailyCashSettlement.actual_sales`.
- So the dashboard monthly chart and the dashboard headline income total are based on different data sources in the current implementation.

## 8. Reports Calculations

Source: `ReportsView.get_context_data()` and `build_reports_excel_response(...)`

### Report totals

Formulas:

```text
total_sales = sum(manual income amount) + sum(settlement.actual_sales)
total_expenses = sum(expense.amount)
net_profit = total_sales - total_expenses
total_purchases = sum(purchase.total_amount)
overall_profit_margin = (net_profit / total_sales) * 100
```

Rule:

```text
if total_sales == 0:
    overall_profit_margin = 0.0
```

Explanation:

- Reports combine manual income with settlement sales.
- Purchases are reported using `total_amount`, not `paid_amount`.

### `build_reporting_monthly_overview(user, months=6, anchor_date=None)`

Per month formulas:

```text
income = sum(manual income amount) + sum(settlement.actual_sales)
expense = sum(expense.amount)
balance = income - expense
income_width = (income / highest_total) * 100
expense_width = (expense / highest_total) * 100
```

Explanation:

- Unlike the dashboard monthly helper, this version includes settlement sales.

### `build_reporting_income_category_overview(income_queryset, settlement_queryset)`

Formulas:

```text
category_total = sum(manual income amount per category)
Daily Settlement total = sum(settlement.actual_sales)
```

Explanation:

- Settlement income is added under the category name `Daily Settlement`.
- The result is then ranked by `build_ranked_category_overview(...)`.

### `build_ranked_category_overview(rows)`

Per category formulas:

```text
width = (category_total / highest_total) * 100
share = (category_total / aggregate_total) * 100
```

Rules:

```text
if highest_total == 0:
    use 1.00 to avoid division by zero

if aggregate_total == 0:
    share = 0.0
```

Explanation:

- `width` is a relative bar size.
- `share` is the percentage contribution inside the returned category group.

## 9. Screen-Level Summary Totals

These are smaller totals calculated for list pages.

### Income list

Formula:

```text
page_total = sum(entry.amount for displayed ledger entries)
```

Note:

- Settlement ledger entries contribute `actual_sales`.

### Expense list

Formulas:

```text
page_total = sum(filtered expense.amount)
month_total = sum(current month expense.amount)
today_total = sum(today expense.amount)
```

### Purchase list

Formulas:

```text
page_total = sum(total_amount)
paid_total = sum(paid_amount)
pending_total = sum(pending_amount)
```

### Sales list

Formulas:

```text
page_total = sum(net_amount)
received_total = sum(received_amount)
balance_total = sum(effective_balance_amount)
split_cash_total = sum(split_cash_amount)
split_card_total = sum(split_card_amount)
credit_bill_total = sum(effective_balance_amount for credit records)
```

Important note:

- `received_total` uses raw `received_amount`.
- `balance_total` uses calculated `effective_balance_amount`.

### Daily settlement page summary

Formulas:

```text
gpay_total = sum(gpay_settled for filtered settlement rows)
cash_total = sum(cash_settled for filtered settlement rows)
expense_total = sum(expense_amount for filtered settlement rows)
actual_sales_total = sum(actual_sales for filtered settlement rows)
closing_total = sum(closing_balance for filtered settlement rows)
credit_bill_total = sum(effective_balance_amount for credit bill rows)
```

## 10. Most Important Differences Between Screens

The same business data is intentionally calculated in different ways depending on the screen:

- Dashboard income uses `DailyCashSettlement.actual_sales`.
- Reports total sales use `manual income + DailyCashSettlement.actual_sales`.
- Reconciliation settlement income uses `cash_settled + gpay_settled`.
- Purchase reporting totals use `PurchaseRecord.total_amount`.
- Reconciliation purchase totals use `PurchaseRecord.paid_amount`.

This means totals from Dashboard, Reports, Purchases, and Reconciliation should not be expected to match exactly unless the underlying business situation makes them equal.
