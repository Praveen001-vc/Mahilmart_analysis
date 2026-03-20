from .models import ExpenseCategory


def normalize_expense_category_name(name):
    return " ".join(str(name or "").strip().split())[:80]


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
DEFAULT_EXPENSE_CATEGORIES = tuple(DEFAULT_EXPENSE_CATEGORY_PURPOSES.keys())


def ensure_expense_categories_for_role(user):
    if not getattr(user, "is_authenticated", False):
        return ExpenseCategory.objects.none()

    for category_name in (COUNTER_EXPENSE_CATEGORY, *DEFAULT_EXPENSE_CATEGORIES):
        get_or_create_role_expense_category(user, category_name)
    return ExpenseCategory.objects.filter(user=user).order_by("name", "pk")


def get_expense_category_options(user):
    category_names = {
        category.name.casefold(): category.name
        for category in ensure_expense_categories_for_role(user)
    }
    ordered_names = []
    for category_name in (COUNTER_EXPENSE_CATEGORY, *DEFAULT_EXPENSE_CATEGORIES):
        matched_name = category_names.pop(category_name.casefold(), None)
        if matched_name is not None:
            ordered_names.append(matched_name)

    ordered_names.extend(
        sorted(category_names.values(), key=lambda value: value.casefold())
    )
    return ordered_names


def get_expense_category_purpose_map():
    return {
        category_name: list(purpose_options)
        for category_name, purpose_options in DEFAULT_EXPENSE_CATEGORY_PURPOSES.items()
    }


def get_or_create_role_expense_category(user, name):
    normalized_name = normalize_expense_category_name(name)
    if not normalized_name or not getattr(user, "is_authenticated", False):
        return None

    existing = (
        ExpenseCategory.objects.filter(user=user)
        .filter(name__iexact=normalized_name)
        .first()
    )
    if existing is not None:
        return existing
    return ExpenseCategory.objects.create(user=user, name=normalized_name)
