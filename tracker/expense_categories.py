from .models import ExpenseCategory
from .user_roles import filter_queryset_by_role

DEFAULT_EXPENSE_CATEGORIES = (
    "Transport",
    "Utilities",
    "Salary",
    "Rent",
    "Purchase",
    "Maintenance",
    "Delivery",
    "Fuel",
    "General",
)


def normalize_expense_category_name(name):
    return " ".join(str(name or "").strip().split())[:80]


def ensure_expense_categories_for_role(user):
    if not getattr(user, "is_authenticated", False):
        return ExpenseCategory.objects.none()

    queryset = filter_queryset_by_role(ExpenseCategory.objects.all(), user).order_by("name", "pk")
    if queryset.exists():
        return queryset

    ExpenseCategory.objects.bulk_create(
        [
            ExpenseCategory(user=user, name=category_name)
            for category_name in DEFAULT_EXPENSE_CATEGORIES
        ],
        ignore_conflicts=True,
    )
    return filter_queryset_by_role(ExpenseCategory.objects.all(), user).order_by("name", "pk")


def get_expense_category_options(user):
    return list(
        ensure_expense_categories_for_role(user).values_list("name", flat=True)
    )


def get_or_create_role_expense_category(user, name):
    normalized_name = normalize_expense_category_name(name)
    if not normalized_name or not getattr(user, "is_authenticated", False):
        return None

    existing = (
        filter_queryset_by_role(ExpenseCategory.objects.all(), user)
        .filter(name__iexact=normalized_name)
        .first()
    )
    if existing is not None:
        return existing
    return ExpenseCategory.objects.create(user=user, name=normalized_name)
