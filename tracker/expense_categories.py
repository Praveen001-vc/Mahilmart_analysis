from .models import ExpenseCategory, ExpensePurpose, ExpenseRecord


def normalize_expense_category_name(name):
    return " ".join(str(name or "").strip().split())[:80]


def normalize_expense_purpose_name(name):
    return " ".join(str(name or "").strip().split())[:120]


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


def _build_counter_expense_purpose_options():
    ordered_names = []
    seen = set()

    for purpose_options in DEFAULT_EXPENSE_CATEGORY_PURPOSES.values():
        for raw_name in purpose_options:
            normalized_name = normalize_expense_purpose_name(raw_name)
            normalized_key = normalized_name.casefold()
            if not normalized_name or normalized_key in seen:
                continue
            seen.add(normalized_key)
            ordered_names.append(normalized_name)

    return tuple(ordered_names)


DEFAULT_COUNTER_EXPENSE_PURPOSES = _build_counter_expense_purpose_options()


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


def get_default_expense_category_purpose_map():
    return {
        COUNTER_EXPENSE_CATEGORY: list(DEFAULT_COUNTER_EXPENSE_PURPOSES),
        **{
            category_name: [
                normalize_expense_purpose_name(purpose_name)
                for purpose_name in purpose_options
                if normalize_expense_purpose_name(purpose_name)
            ]
            for category_name, purpose_options in DEFAULT_EXPENSE_CATEGORY_PURPOSES.items()
        },
    }


def ensure_expense_purposes_for_role(user):
    if not getattr(user, "is_authenticated", False):
        return ExpensePurpose.objects.none()

    ensure_expense_categories_for_role(user)
    default_purpose_map = get_default_expense_category_purpose_map()
    existing_keys = {
        (category_name.casefold(), purpose_name.casefold())
        for category_name, purpose_name in ExpensePurpose.objects.filter(user=user).values_list(
            "category",
            "name",
        )
    }
    purpose_records_to_create = []

    for category_name, purpose_options in default_purpose_map.items():
        normalized_category = normalize_expense_category_name(category_name)
        if not normalized_category:
            continue

        get_or_create_role_expense_category(user, normalized_category)
        for purpose_name in purpose_options:
            normalized_purpose = normalize_expense_purpose_name(purpose_name)
            if not normalized_purpose:
                continue
            purpose_key = (
                normalized_category.casefold(),
                normalized_purpose.casefold(),
            )
            if purpose_key in existing_keys:
                continue
            existing_keys.add(purpose_key)
            purpose_records_to_create.append(
                ExpensePurpose(
                    user=user,
                    category=normalized_category,
                    name=normalized_purpose,
                )
            )

    for category_name, purpose_name in ExpenseRecord.objects.filter(user=user).exclude(
        category=""
    ).exclude(title="").values_list("category", "title"):
        normalized_category = normalize_expense_category_name(category_name)
        normalized_purpose = normalize_expense_purpose_name(purpose_name)
        if not normalized_category or not normalized_purpose:
            continue
        purpose_key = (
            normalized_category.casefold(),
            normalized_purpose.casefold(),
        )
        if purpose_key in existing_keys:
            continue
        existing_keys.add(purpose_key)
        purpose_records_to_create.append(
            ExpensePurpose(
                user=user,
                category=normalized_category,
                name=normalized_purpose,
            )
        )

    if purpose_records_to_create:
        ExpensePurpose.objects.bulk_create(purpose_records_to_create, ignore_conflicts=True)

    return ExpensePurpose.objects.filter(user=user).order_by("category", "name", "pk")


def get_expense_category_purpose_map(user=None):
    if not getattr(user, "is_authenticated", False):
        return get_default_expense_category_purpose_map()

    purpose_map = {
        category_name: []
        for category_name in get_expense_category_options(user)
    }

    for category_name, purpose_name in ensure_expense_purposes_for_role(user).values_list(
        "category",
        "name",
    ):
        purpose_options = purpose_map.setdefault(category_name, [])
        if purpose_name not in purpose_options:
            purpose_options.append(purpose_name)

    return purpose_map


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


def get_or_create_role_expense_purpose(user, category_name, name):
    normalized_category = normalize_expense_category_name(category_name)
    normalized_name = normalize_expense_purpose_name(name)
    if (
        not normalized_category
        or not normalized_name
        or not getattr(user, "is_authenticated", False)
    ):
        return None

    get_or_create_role_expense_category(user, normalized_category)
    existing = (
        ExpensePurpose.objects.filter(user=user)
        .filter(category__iexact=normalized_category, name__iexact=normalized_name)
        .first()
    )
    if existing is not None:
        return existing
    return ExpensePurpose.objects.create(
        user=user,
        category=normalized_category,
        name=normalized_name,
    )
