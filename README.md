# Mahilmart Expense Tracking

Mahilmart Expense Tracking is a Django-based finance application for managing income, expenses, suppliers, purchases, reports, and user access in one premium-style web interface.

This project now runs separately on Django + PostgreSQL only. Microsoft SQL Server integration has been removed from the application flow.

## Tech Stack

- Python 3
- Django 5.2.4
- PostgreSQL with `psycopg`
- HTML templates + custom CSS
- ReportLab for purchase invoice PDF generation

## Main Features

- Secure login and logout
- Dashboard with income, expense, balance, and savings overview
- Income entry and income history
- Expense entry for both supplier and general expenses
- Supplier master with mandatory supplier details
- Manual purchase module stored in PostgreSQL
- Premium invoice PDF download for purchase records
- Admin-only user management with create, edit, active, and inactive control
- Reports page with monthly overview and top categories
- Auto-load list pages in batches of 50 rows

## Project Structure

- `manage.py`: Django entry point
- `mahilmart_project/settings.py`: Django settings and database configuration
- `mahilmart_project/urls.py`: Root URL routing and media serving in debug mode
- `tracker/models.py`: Core database models
- `tracker/forms.py`: Form validation and field styling
- `tracker/views.py`: Dashboard, CRUD pages, reports, and invoice PDF generation
- `tracker/urls.py`: App URL routes
- `templates/`: All HTML templates
- `static/tracker/styles.css`: Premium UI styling
- `static/tracker/auto_load.js`: Infinite/auto-load behavior for list pages
- `database/create_postgres_database.py`: PostgreSQL database creation helper

## Database Setup

The main database is PostgreSQL.

### Environment Variables

Create a `.env` file using `.env.example`:

```env
DJANGO_SECRET_KEY=replace-with-a-secure-secret
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
DJANGO_CSRF_TRUSTED_ORIGINS=http://127.0.0.1,http://localhost
DB_ENGINE=postgres
POSTGRES_DB=mahilmart_tracking
POSTGRES_USER=postgres
POSTGRES_PASSWORD=admin@123
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
```

To open the app from another machine or by IP address, update only the `.env` file and restart Django. No Python code change is needed.

Example:

```env
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost,192.168.0.113
DJANGO_CSRF_TRUSTED_ORIGINS=http://127.0.0.1,http://localhost,http://192.168.0.113
```

### Create the PostgreSQL Database

```powershell
python database\create_postgres_database.py
```

### Run Migrations

```powershell
python manage.py makemigrations
python manage.py migrate
```

### Create the First Admin User

```powershell
python manage.py createsuperuser
```

### Start the Server

```powershell
python manage.py runserver
```

## Optional Local Development with SQLite

If PostgreSQL is not available temporarily, you can use SQLite for local testing:

```powershell
$env:DB_ENGINE="sqlite"
python manage.py migrate
python manage.py runserver
```

## Installed Packages

From `requirements.txt`:

- `Django==5.2.4`
- `psycopg[binary]==3.2.9`
- `python-dotenv==1.0.1`

## URL Pages

| Route | Purpose |
| --- | --- |
| `/login/` | Login page |
| `/logout/` | Logout |
| `/dashboard/` | Main financial dashboard |
| `/income/` | Income list |
| `/income/add/` | Add income record |
| `/expenses/` | Expense list |
| `/expenses/add/` | Add expense record |
| `/purchases/` | Purchase list |
| `/purchases/add/` | Add purchase record |
| `/purchases/<id>/invoice/` | Download purchase invoice PDF |
| `/suppliers/` | Supplier list |
| `/suppliers/add/` | Add supplier |
| `/users/` | User management list |
| `/users/add/` | Create user |
| `/users/<id>/edit/` | Edit user |
| `/reports/` | Report summary page |
| `/admin/` | Django admin |

## Authentication and Access Rules

- All main pages require login.
- Only superusers can open the user-management pages.
- Admin users can create and edit other users.
- A logged-in admin cannot deactivate their own account from the user-management form.
- A logged-in admin cannot remove their own admin access from the user-management form.

## User Roles

Role behavior is controlled in `tracker/user_roles.py`.

- `Admin`: `is_superuser=True` and `is_staff=True`
- `Store Admin`: `is_superuser=False` and `is_staff=True`
- `Staff`: `is_superuser=False` and `is_staff=False`

## Core Data Models

### IncomeRecord

Stores income transactions.

Main fields:

- `title`
- `source`
- `category`
- `amount`
- `transaction_date`
- `payment_method`
- `notes`

### ExpenseRecord

Stores expenses for both supplier-related and general operating expenses.

Main fields:

- `title`
- `supplier` optional
- `vendor`
- `category`
- `amount`
- `transaction_date`
- `payment_method`
- `notes`

Business rule:

- If a supplier is selected, `vendor` is automatically set to the supplier name.
- If no supplier is selected, the record works as a general expense.

### Supplier

Stores supplier master details.

Mandatory fields:

- `name`
- `contact_person`
- `phone_number`

Other fields:

- `email`
- `address`
- `gstin_number`
- `fssai_number`
- `pan_number`
- `credit_terms`
- `opening_balance`
- `bank_name`
- `account_number`
- `ifsc_code`
- `status`
- `notes`

Business rule:

- `supplier_code` is auto-generated after save in the format `SUP-0001`.

### PurchaseRecord

Stores manual purchase records inside PostgreSQL.

Main fields:

- `supplier` optional
- `supplier_name`
- `purchase_type`
- `invoice_number`
- `total_amount`
- `paid_amount`
- `pending_amount`
- `transaction_date`
- `attachment`
- `notes`
- `created_at`

Business rules:

- If a saved supplier is selected, `supplier_name` is taken from that supplier.
- If no saved supplier is selected, `supplier_name` must be entered manually.
- `transaction_date` is automatically set from the system date if left empty.
- `pending_amount` is automatically calculated as `total_amount - paid_amount`.
- Manual purchase records are tagged with an internal manual source reference.

### UserAccountProfile

Stores extra details for Django users.

Main field in active use:

- `master_name`

The actual login account is the Django `User` model, and this profile stores additional account information.

## Dashboard Logic

The dashboard shows:

- Total income
- Total expenses
- Net balance
- Opening balance
- Closing balance
- Savings rate
- Total suppliers
- Recent income
- Recent expenses
- Last 6 months overview

Financial calculation rules:

- `opening_balance = all income before today - all expenses before today`
- `closing_balance = opening_balance + today's income - today's expenses`
- `net balance = total income - total expenses`
- `savings_rate = net balance / total income * 100`

## Income Module

The income page allows staff to:

- Add new income records
- View income history
- See total income value
- Auto-load the next 50 rows while browsing the list

## Expense Module

The expense page supports two types of expense entries:

- Supplier expense
- General expense

Examples of general expenses:

- Electricity
- Salary
- Rent
- Fuel
- Courier
- Other day-to-day business costs

The form includes:

- Title
- Saved Supplier optional
- Paid To / Reference
- Category
- Amount
- Transaction Date
- Payment Method
- Notes

## Supplier Module

The supplier form includes:

- Auto-generated supplier ID display
- Mandatory supplier name
- Mandatory contact person
- Mandatory phone number
- Optional email with email-format validation
- Optional address and registration details
- Opening balance
- Bank details
- Active or inactive status
- Notes

## Purchase Module

The purchase module is manual and separate inside PostgreSQL.

The purchase form includes:

- Saved Supplier optional
- Supplier Name
- Type
- Invoice Number
- Total Amount
- Paid Amount
- One file upload
- Notes
- Auto-calculated pending amount preview

Purchase page behavior:

- Purchase date and time are stored automatically from the system
- No manual date entry is required on the form
- Users can save a purchase or save and immediately download the invoice PDF
- Uploaded files can be downloaded later from the purchase list

The purchase list shows:

- Supplier name
- Type
- Invoice number
- Total amount
- Paid amount
- Pending amount
- Saved by
- Saved on
- File download
- Invoice download

## Purchase Invoice PDF

Each saved purchase can generate a premium invoice PDF.

The invoice includes:

- Mahilmart branded header
- Invoice number
- Saved date and time
- Supplier name
- Purchase type
- Prepared by username
- Purchase date
- Supplier code if linked
- Attachment status
- Total amount
- Paid amount
- Pending amount
- Notes
- Supplier signature line
- Authorized sign line

## User Management Module

User management is available only for admin users.

The user form includes:

- User name
- Master name
- Role
- Status
- Password
- Confirm password

Supported actions:

- Create user
- Edit user
- Set active or inactive
- Change role
- Update password

Role mapping used by the system:

- If the role is `Admin`, the user becomes a Django superuser.
- If the role is `Store Admin`, the user becomes staff but not superuser.
- If the role is `Staff`, the user becomes neither staff nor superuser.

## Reports Module

The reports page shows:

- Monthly overview
- Top income categories
- Top expense categories

## Auto-Load Pagination

Income, expense, and purchase list pages load records in batches of 50 rows.

Behavior:

- The page initially loads 50 rows
- More rows are automatically fetched while scrolling
- A manual "Load next 50" option is also shown

## Media and File Uploads

Purchase attachments are stored in:

- `media/purchase_files/`

Media configuration:

- `MEDIA_URL = "/media/"`
- `MEDIA_ROOT = BASE_DIR / "media"`

When `DEBUG=True`, media files are served by Django automatically through the root URL configuration.

## Legacy Import Utility

The repository still contains one optional CSV import command:

```powershell
python manage.py import_purmas_csv <csv_path> --username <username>
```

This is a CSV-based import helper only. The live application no longer depends on Microsoft SQL Server.

## Testing and Validation

Run project checks:

```powershell
python manage.py check
```

Run automated tests:

```powershell
python manage.py test
```

Current test coverage includes:

- Dashboard totals and opening balance logic
- Supplier validation and auto code generation
- Income and expense pagination
- User management permissions and role updates
- Purchase pending amount calculation
- Purchase save flow with file upload
- Purchase invoice PDF generation
- Purchase auto-date behavior

## Current Business Rules Summary

- Supplier name, contact person, and phone number are mandatory
- Purchase pending amount is always calculated from total minus paid
- Purchase date is automatic
- Expenses can be supplier-related or general
- Only admins manage users
- Purchases are handled separately in PostgreSQL
- The application is PostgreSQL-first and independent from Microsoft SQL Server
