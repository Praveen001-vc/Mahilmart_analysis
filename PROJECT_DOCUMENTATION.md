# Mahilmart Project Documentation

## 1. Overview

Mahilmart is a Django-based finance and operations application for store workflows. The system manages:

- income
- expenses
- suppliers
- purchases and purchase payments
- daily cash settlement
- sales ledger sync data
- reconciliation-only entries
- reports and Excel export
- user accounts and module permissions

The codebase contains one Django project, `mahilmart_project`, and one main app, `tracker`.
k= 0
y
## 2. High-Level Architecture

The request flow is:

1. `manage.py` starts Django and normalizes `runserver` to port `8081`.
2. `mahilmart_project/settings.py` loads `.env`, configures the database, templates, static files, and auth redirects.
3. `mahilmart_project/urls.py` routes admin URLs to Django admin and all app URLs to `tracker.urls`.
4. `tracker/urls.py` maps routes to class-based views.
5. `tracker/views.py` performs permission checks, queryset loading, calculations, form processing, and response generation.
6. `tracker/forms.py` defines validation and shared widget styling.
7. `tracker/models.py` stores business data and model-level save rules.
8. templates in `templates/` render server-side HTML.
9. static files in `static/tracker/` handle styling and a small amount of UI behavior.

Data enters the system from three directions:

- native app forms for income, expenses, purchases, settlement, suppliers, users, and reconciliation
- optional SQL Server sync for sales, suppliers, and users
- optional CSV import for PurMas purchase-master style data

## 3. Runtime and Dependencies

Main stack:

- Python 3
- Django 5.2.4
- PostgreSQL via `psycopg`
- optional SQL Server access via `pyodbc`
- Excel export via `openpyxl`
- PDF purchase invoice generation via `reportlab` imports in `tracker/views.py`

The current `requirements.txt` includes:

- `Django==5.2.4`
- `psycopg[binary]==3.2.9`
- `pyodbc==5.3.0`
- `python-dotenv==1.0.1`
- `openpyxl==3.1.5`

Operational note:

- `tracker/views.py` imports `reportlab`. If it is not already installed in the runtime environment, purchase invoice PDF download will fail.

## 4. Environment and Startup

### 4.1 Important Environment Variables

Core Django and database variables:

- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`
- `DB_ENGINE`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_HOST`
- `POSTGRES_PORT`

Optional SQL Server sync variables:

- `SQLSERVER_DRIVER`
- `SQLSERVER_HOST`
- `SQLSERVER_PORT`
- `SQLSERVER_DATABASE`
- `SQLSERVER_USER`
- `SQLSERVER_PASSWORD`

### 4.2 Database Behavior

`mahilmart_project/settings.py` supports:

- `postgres` as the default backend
- `sqlite` for local fallback development

### 4.3 Startup Flow

Typical setup:

1. create `.env`
2. run `python database\create_postgres_database.py`
3. run `python manage.py migrate`
4. run `python manage.py createsuperuser`
5. run `python manage.py runserver`

### 4.4 Port Normalization

`manage.py` rewrites `runserver` defaults to port `8081`.

Examples:

- `python manage.py runserver` -> `127.0.0.1:8081`
- `python manage.py runserver 8000` -> `8081`
- `python manage.py runserver 192.168.0.50:8000` -> `192.168.0.50:8081`

## 5. Access Model

Access control has two layers:

- role level from Django flags
- module-level permissions from `UserModulePermission`

Roles from `tracker/user_roles.py`:

- `Admin`: `is_superuser=True`
- `Store Admin`: `is_staff=True`, `is_superuser=False`
- `Staff`: `is_staff=False`, `is_superuser=False`

Module permissions from `tracker/access_control.py`:

- dashboard
- sales
- daily settlement
- income
- purchases
- suppliers
- expenses
- reports

Important design rule:

- many querysets are filtered by role family using `filter_queryset_by_role(...)`, not by the exact logged-in user only.

## 6. URL Map

Main routes from `tracker/urls.py`:

| Route | View | Purpose |
| --- | --- | --- |
| `/` | `HomeRedirectView` | Redirect logged-in users to first allowed module |
| `/login/` | Django `LoginView` | Login page |
| `/logout/` | Django `LogoutView` | Logout |
| `/access-denied/` | `AccessDeniedView` | No-access fallback page |
| `/dashboard/` | `DashboardView` | Main financial dashboard |
| `/sales/` | `SalesListView` | Sales ledger and split handling |
| `/income/` | `IncomeListView` | Income history |
| `/income/add/` | `IncomeCreateView` | Add income |
| `/purchases/` | `PurchaseListView` | Purchase list and SQL Server purchase sync |
| `/purchases/add/` | `PurchaseCreateView` | Add purchase |
| `/purchases/<id>/details/` | `PurchaseDetailView` | JSON detail endpoint |
| `/purchases/<id>/invoice/` | `PurchaseInvoiceDownloadView` | PDF invoice download |
| `/purchases/<id>/pay/` | `PurchasePaymentCreateView` | Add payment to purchase |
| `/suppliers/` | `SupplierListView` | Supplier list and sync |
| `/suppliers/add/` | `SupplierCreateView` | Add supplier |
| `/users/` | `UserListView` | Admin-only user list and sync |
| `/users/permissions/` | `PermissionSettingsView` | Admin-only permission settings |
| `/users/add/` | `UserCreateView` | Create user |
| `/users/<id>/edit/` | `UserUpdateView` | Edit user |
| `/expenses/` | `ExpenseListView` | Expense ledger |
| `/expenses/add/` | `ExpenseCreateView` | Add counter expense |
| `/expenses/categories/` | `ExpenseCategoryListView` | Category management |
| `/expenses/settlement/` | `DailySettlementView` | Daily cash settlement |
| `/reports/reconciliation/` | `ReconciliationView` | Separate reconciliation workspace |
| `/reports/` | `ReportsView` | Reports and Excel export |

## 7. Data Model Reference

### 7.1 Shared and Enum Models

`tracker/models.py` defines:

- `PaymentMethod`
- `SalesPaymentMode`
- `SupplierStatus`
- `BaseRecord` as the abstract parent for `IncomeRecord` and `ExpenseRecord`
- `ReconciliationBaseEntry` as the abstract parent for reconciliation entries

### 7.2 Core Models

`IncomeRecord`

- regular income entries
- extra fields: `source`, `source_reference`

`ExpenseRecord`

- regular expense entries
- extra fields: `supplier`, `source_reference`, `vendor`
- if a supplier is selected, `vendor` is replaced with supplier name
- save logic also auto-creates missing `ExpenseCategory` rows

`Supplier`

- supplier master data
- auto-generates `supplier_code` after first save
- unique by `(user, name)`

`ExpenseCategory`

- reusable expense category master
- unique by `(user, name)`

`UserAccountProfile`

- extra user metadata such as `master_name`, `source_user_no`, and `source_reference`

`UserModulePermission`

- per-user module access toggles

`PurchaseRecord`

- purchase invoice-level data
- includes manual entries and SQL Server `PurMas_Table` sync rows
- keeps `supplier_name`, `total_amount`, `paid_amount`, `pending_amount`, attachment, and notes
- auto-fills supplier name, date, pending amount, and source reference on save

`PurchasePayment`

- payment history rows for a purchase

`DailyCashSettlement`

- final daily settlement snapshot
- stores opening balance, settled cash/card, denomination JSON, expense amount, closing balance, total amount, and actual sales

`SalesLedgerRecord`

- synced sales ledger data from SQL Server
- supports split cash and split card amounts
- contains helper properties for effective received and balance amounts

`ReconciliationOpeningBalance`

- stored opening balance by date for reconciliation workspace

`ReconciliationIncomeEntry`

- manual income entries used only in reconciliation
- database still has `opening_balance` for backward compatibility
- the active UI no longer asks for opening balance in the reconciliation income form

`ReconciliationExpenseEntry`

- manual expense entries used only in reconciliation

## 8. Forms and Validation

Main form classes in `tracker/forms.py`:

- `StyledAuthenticationForm`: styles login inputs
- `StyledModelForm`: shared widgets for text, money, date, select, and notes
- `IncomeForm`: regular income entry
- `ExpenseForm`: regular expense form with dynamic category choices and optional supplier
- `ExpenseCategoryForm`: create a reusable category
- `DailyCashSettlementForm`: settlement validation for money fields
- `ReconciliationIncomeForm`: reconciliation income form
- `ReconciliationExpenseForm`: reconciliation expense form
- `ReconciliationOpeningBalanceForm`: popup form for reconciliation opening balance
- `PurchaseForm`: validates supplier/manual name logic and prevents overpayment at create time
- `PurchasePaymentForm`: validates positive payment and prevents payment above pending amount
- `SupplierForm`: supplier master entry
- `UserManagementForm`: user creation and update, role assignment, password handling, active status, and profile update

## 9. Workflow by Module

### 9.1 Authentication and Shell

Files:

- `tracker/urls.py`
- `templates/auth/login.html`
- `templates/base.html`
- `tracker/context_processors.py`

Behavior:

- login uses Django auth with custom field styling
- logout is a POST action in `base.html`
- navigation is permission-aware through `perm.*`
- flash messages auto-dismiss in the base layout

### 9.2 Dashboard

Files:

- `tracker/views.py` -> `DashboardView`
- `templates/tracker/dashboard.html`
- `static/tracker/dashboard.js`

Behavior:

- loads the selected date from query params
- calculates opening balance, closing balance, selected-day totals, monthly totals, savings rate, and recent records
- uses settlements as the main source of income totals
- date input auto-submits through JS

### 9.3 Sales

Files:

- `tracker/views.py` -> `SalesListView`
- `tracker/sales_sync.py`
- `templates/tracker/sales_list.html`

Behavior:

- lists synced sales rows
- filters by date and payment mode
- allows SQL Server sales sync from the page
- allows manual save of split cash/card amounts per bill
- builds a credit-bill summary from effective balance amounts

### 9.4 Income

Files:

- `tracker/views.py` -> `IncomeListView`, `IncomeCreateView`
- `templates/tracker/income_list.html`
- `templates/tracker/income_form.html`

Behavior:

- create page writes regular `IncomeRecord`
- list page uses helper-built ledger rows instead of only raw model rows

### 9.5 Expenses

Files:

- `tracker/views.py` -> `ExpenseListView`, `ExpenseCreateView`, `ExpenseCategoryListView`
- `tracker/expense_categories.py`
- `templates/tracker/expense_list.html`
- `templates/tracker/expense_form.html`
- `templates/tracker/expense_category_list.html`

Behavior:

- add page is focused on counter expense entry
- list page supports inline save, update, and delete
- incomplete inline rows are skipped, not fatal
- categories come from defaults plus user-created values
- AJAX category creation is supported

### 9.6 Daily Settlement

Files:

- `tracker/views.py` -> `DailySettlementView`
- `templates/tracker/daily_settlement.html`

Behavior:

- builds an autofill summary from sales, expenses, and previous balances
- shows denomination counting and credit bill summaries
- calculates preview values before save
- superusers can edit opening balance directly
- non-superusers inherit opening balance as readonly

### 9.7 Purchases

Files:

- `tracker/views.py` -> purchase views
- `templates/tracker/purchase_list.html`
- `templates/tracker/purchase_form.html`

Behavior:

- create purchase records
- create payment history rows
- generate PDF invoice download
- sync purchase status into expenses through `sync_purchase_to_expense(...)`

### 9.8 Suppliers

Files:

- `tracker/views.py` -> `SupplierListView`, `SupplierCreateView`
- `tracker/supplier_sync.py`
- `templates/tracker/supplier_list.html`
- `templates/tracker/supplier_form.html`

Behavior:

- manage supplier master data
- preview next supplier code
- sync suppliers from SQL Server

### 9.9 Users and Permissions

Files:

- `tracker/views.py` -> `UserListView`, `UserCreateView`, `UserUpdateView`, `PermissionSettingsView`
- `tracker/user_sync.py`
- `tracker/access_control.py`
- `templates/tracker/user_list.html`
- `templates/tracker/user_form.html`
- `templates/tracker/permission_settings.html`

Behavior:

- only superusers can manage users
- supports SQL Server user sync
- stores roles through Django flags and stores module access through `UserModulePermission`
- permission updates are handled by JSON POST requests

### 9.10 Reconciliation

Files:

- `tracker/views.py` -> reconciliation helpers and `ReconciliationView`
- `templates/tracker/reconciliation.html`

Behavior:

- runs as a separate workspace from regular income and expenses
- supports manual reconciliation income and expense entries
- includes automatic settlement income in summary calculations
- stores opening balance through a separate opening-balance control
- includes purchase totals in reconciliation expense summary

Important current rule:

- reconciliation income form no longer asks for opening balance in the UI
- opening balance is handled separately through `ReconciliationOpeningBalanceForm`
- old stored `ReconciliationIncomeEntry.opening_balance` values are still supported for fallback lookup

### 9.11 Reports

Files:

- `tracker/views.py` -> report helpers and `ReportsView`
- `templates/tracker/reports.html`

Behavior:

- aggregates sales, expenses, purchases, suppliers, and monthly overview data
- builds top income and expense categories
- exports Excel when `?export=excel`

## 10. Calculation and Helper Map

The largest concentration of business logic lives in `tracker/views.py`.

Helper groups:

- income and ledger helpers:
  - `_sum_amount`
  - `_sum_settlement_income`
  - `build_income_ledger_entries_for_period`
  - `build_income_ledger_entries`
  - `build_monthly_overview`
- reports helpers:
  - `get_reporting_income_total`
  - `build_reporting_monthly_overview`
  - `build_reporting_income_category_overview`
  - `build_reports_excel_response`
- reconciliation helpers:
  - `get_reconciliation_filter_values`
  - `build_reconciliation_redirect_url`
  - `build_reconciliation_income_entries`
  - `get_reconciliation_closing_balance_for_date`
  - `get_reconciliation_opening_balance_for_date`
  - `get_reconciliation_split_card_balance_for_date`
  - `save_reconciliation_opening_balance_for_date`
  - `build_reconciliation_expense_entries`
- filtering and redirect helpers:
  - purchase filter helpers
  - sales filter helpers
  - expense filter helpers
  - pagination URL helpers
- settlement helpers:
  - `parse_money_value`
  - `parse_cash_denominations_payload`
  - `build_cash_denominations_payload`
  - `get_cash_denominations_total`
  - `get_cash_difference_amount`
  - `build_sales_settlement_summary`
  - `get_default_settlement_opening_balance`
  - `build_settlement_autofill_summary`
  - `build_settlement_preview`
- purchase helpers:
  - `build_invoice_filename`
  - `build_purchase_invoice_response`
  - `format_money`
  - `sync_purchase_to_expense`
  - `create_purchase_payment`
  - `build_purchase_payment_history`
  - `build_purchase_detail_payload`

## 11. Support Modules

`tracker/access_control.py`

- defines `TRACKER_PERMISSION_ITEMS`
- resolves permission records
- provides `ModulePermissionRequiredMixin`
- decides fallback route if the current page is not allowed

`tracker/user_roles.py`

- converts Django flags to role labels
- filters querysets by role family
- applies role values back onto a `User`
- infers source role during sync

`tracker/expense_categories.py`

- defines default category names
- defines category-to-purpose suggestions
- ensures category rows exist in the database

`tracker/context_processors.py`

- injects `perm` into templates

## 12. Sync and Import Workflows

### 12.1 Sales Sync

Files:

- `tracker/sales_sync.py`
- `tracker/management/commands/sync_sqlserver_sales.py`

Flow:

1. validate SQL Server configuration and driver
2. build SQL query by date range
3. fetch rows in batches
4. normalize amounts, dates, and payment mode
5. bulk upsert into `SalesLedgerRecord`

Important protection:

- implausible money values are sanitized before save

### 12.2 Supplier Sync

Files:

- `tracker/supplier_sync.py`

Flow:

1. query ranked supplier rows from SQL Server purchase master data
2. normalize supplier number and name
3. match by source supplier number or normalized name
4. create or selectively update supplier data

### 12.3 User Sync

Files:

- `tracker/user_sync.py`

Flow:

1. read SQL Server `User_Table`
2. normalize username, master name, and password
3. infer role
4. create Django user, profile, and permission row

### 12.4 PurMas CSV Import

Files:

- `tracker/management/commands/import_purmas_csv.py`

Flow:

1. choose a target Django user
2. parse CSV rows
3. create or enrich `Supplier`
4. create `ExpenseRecord` rows from purchase master data

Important note:

- this import writes expense entries, not `PurchaseRecord` rows

### 12.5 SQL Server Purchase Inspection

Files:

- `tracker/purchase_inspector.py`
- `tracker/management/commands/inspect_sqlserver_purchases.py`

Flow:

1. query filtered preview rows from SQL Server `dbo.PurMas_Table`
2. search `INFORMATION_SCHEMA.COLUMNS` for payment-related table and column names
3. optionally preview a candidate table such as a payment master table

Important note:

- use this command when purchase details exist in `PurMas_Table` but the payment source table is still unknown

### 12.6 SQL Server Purchase Sync

Files:

- `tracker/purchase_sync.py`
- `tracker/purchase_helpers.py`
- `templates/tracker/purchase_list.html`

Flow:

1. post from `/purchases/` using the current supplier, invoice, and date filters
2. query SQL Server `dbo.PurMas_Table`
3. upsert matching rows into `PurchaseRecord`
4. update linked `ExpenseRecord` rows for synced purchases

Important note:

- synced purchase rows use the `SQLPURMAS:` source reference prefix and now appear directly in the purchase ledger

## 13. Templates and Static Assets

Shared templates:

- `templates/base.html`
- `templates/auth/login.html`

Tracker templates:

- `templates/tracker/access_denied.html`
- `templates/tracker/dashboard.html`
- `templates/tracker/sales_list.html`
- `templates/tracker/income_list.html`
- `templates/tracker/income_form.html`
- `templates/tracker/expense_list.html`
- `templates/tracker/expense_form.html`
- `templates/tracker/expense_category_list.html`
- `templates/tracker/daily_settlement.html`
- `templates/tracker/purchase_list.html`
- `templates/tracker/purchase_form.html`
- `templates/tracker/reports.html`
- `templates/tracker/reconciliation.html`
- `templates/tracker/supplier_list.html`
- `templates/tracker/supplier_form.html`
- `templates/tracker/user_list.html`
- `templates/tracker/user_form.html`
- `templates/tracker/permission_settings.html`

Static files:

- `static/tracker/styles.css`: main UI styling
- `static/tracker/auto_load.js`: automatic pagination for list pages
- `static/tracker/dashboard.js`: dashboard interactions
- `static/tracker/mountain-background.svg`: decorative asset

## 14. Admin, Database, and Migrations

`tracker/admin.py` registers:

- `Supplier`
- `IncomeRecord`
- `ExpenseRecord`
- `ExpenseCategory`
- `PurchaseRecord`
- `PurchasePayment`
- `SalesLedgerRecord`
- `DailyCashSettlement`
- `UserAccountProfile`
- `UserModulePermission`
- `ReconciliationIncomeEntry`
- `ReconciliationExpenseEntry`

Database helper files:

- `database/create_postgres_database.py`
- `database/create_database.sql`

Migration history in `tracker/migrations/` shows the major feature growth:

- initial supplier, income, and expense models
- user profile support
- purchase and payment support
- daily settlement and sales-ledger support
- expense categories
- module permissions
- reconciliation models and opening-balance support

## 15. File-by-File Code Map

Root files:

- `manage.py`: Django CLI entry and runserver port normalization
- `README.md`: quick-start overview
- `PROJECT_DOCUMENTATION.md`: full project guide
- `requirements.txt`: dependency pins

Django project package:

- `mahilmart_project/settings.py`: configuration
- `mahilmart_project/urls.py`: root routing
- `mahilmart_project/wsgi.py`: WSGI entry
- `mahilmart_project/asgi.py`: ASGI entry

Tracker app:

- `tracker/models.py`: data structures and save logic
- `tracker/forms.py`: form validation and widget setup
- `tracker/views.py`: all major workflows and calculations
- `tracker/urls.py`: application routes
- `tracker/admin.py`: admin registrations
- `tracker/access_control.py`: module permissions
- `tracker/user_roles.py`: role filtering logic
- `tracker/context_processors.py`: template permission context
- `tracker/expense_categories.py`: default categories and suggestions
- `tracker/sales_sync.py`: SQL Server sales sync
- `tracker/supplier_sync.py`: SQL Server supplier sync
- `tracker/user_sync.py`: SQL Server user sync
- `tracker/tests.py`: automated test coverage

Main symbol inventory:

- `tracker/models.py`
  - enums: `PaymentMethod`, `SalesPaymentMode`, `SupplierStatus`
  - abstract models: `BaseRecord`, `ReconciliationBaseEntry`
  - concrete models: `IncomeRecord`, `Supplier`, `ExpenseCategory`, `UserAccountProfile`, `UserModulePermission`, `PurchaseRecord`, `PurchasePayment`, `DailyCashSettlement`, `SalesLedgerRecord`, `ExpenseRecord`, `ReconciliationOpeningBalance`, `ReconciliationIncomeEntry`, `ReconciliationExpenseEntry`
- `tracker/forms.py`
  - `StyledAuthenticationForm`
  - `StyledModelForm`
  - `IncomeForm`
  - `ExpenseForm`
  - `ExpenseCategoryForm`
  - `DailyCashSettlementForm`
  - `ReconciliationIncomeForm`
  - `ReconciliationExpenseForm`
  - `ReconciliationOpeningBalanceForm`
  - `PurchaseForm`
  - `PurchasePaymentForm`
  - `SupplierForm`
  - `UserManagementForm`
- `tracker/views.py`
  - helper groups: ledger, reporting, reconciliation, filtering, settlement, purchase, invoice, export
  - views: `HomeRedirectView`, `AccessDeniedView`, `DashboardView`, `AutoLoadPaginatedListView`, `AdminRequiredMixin`, `IncomeListView`, `IncomeCreateView`, `ExpenseListView`, `ExpenseCreateView`, `ExpenseCategoryListView`, `DailySettlementView`, `PurchaseListView`, `SalesListView`, `PurchaseDetailView`, `PurchaseCreateView`, `PurchaseInvoiceDownloadView`, `PurchasePaymentCreateView`, `SupplierListView`, `SupplierCreateView`, `UserListView`, `UserCreateView`, `UserUpdateView`, `PermissionSettingsView`, `ReconciliationView`, `ReportsView`
- `tracker/access_control.py`
  - permission constants, resolution helpers, fallback route helpers, `ModulePermissionRequiredMixin`
- `tracker/user_roles.py`
  - role constants, role labels, queryset filter builder, role applier, source-role inference
- `tracker/expense_categories.py`
  - category normalization, default category catalog, category-purpose map, category seeding helpers
- sync modules
  - `tracker/sales_sync.py`: SQL Server sales normalization and bulk upsert
  - `tracker/supplier_sync.py`: supplier sync and merge logic
  - `tracker/user_sync.py`: user sync and role inference

Management commands:

- `tracker/management/commands/sync_sqlserver_sales.py`
- `tracker/management/commands/import_purmas_csv.py`

## 16. Testing and Recommended Development Workflow

Main test file:

- `tracker/tests.py`

It covers core workflows such as dashboard totals, settlement logic, reconciliation, purchases, permissions, and CRUD behavior.

Recommended workflow:

1. configure `.env`
2. create the database
3. run migrations
4. create a superuser
5. start the server on `8081`
6. verify access rules from login to reports
7. run `python manage.py test tracker.tests --keepdb` after workflow changes

## 17. Key Business Rules Summary

- permissions are module-based, not only role-based
- queryset visibility is often role-scoped, not only user-scoped
- settlement data is a major input for dashboard income totals
- purchases also affect expense reporting through sync logic
- reconciliation is intentionally separate from the normal income and expense modules
- superusers have extra control over user management and settlement opening balance
- SQL Server integration is optional, but it is still active in the codebase for sales, suppliers, and users
