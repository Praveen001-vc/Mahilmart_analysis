from .income_categories import (
    DEFAULT_INCOME_CATEGORY,
    INCOME_CATEGORY_COUNTER,
    INCOME_CATEGORY_OFFICE,
    normalize_income_category_name,
)
from .models import IncomePurpose, IncomeRecord


def normalize_income_purpose_name(name):
    return " ".join(str(name or "").strip().split())[:120]


DEFAULT_INCOME_PURPOSES = ()

DEFAULT_INCOME_CATEGORY_PURPOSES = {
    INCOME_CATEGORY_COUNTER: list(DEFAULT_INCOME_PURPOSES),
    INCOME_CATEGORY_OFFICE: list(DEFAULT_INCOME_PURPOSES),
}


def get_default_income_category_purpose_map():
    return {
        category_name: [
            normalize_income_purpose_name(purpose_name)
            for purpose_name in purpose_options
            if normalize_income_purpose_name(purpose_name)
        ]
        for category_name, purpose_options in DEFAULT_INCOME_CATEGORY_PURPOSES.items()
    }


def ensure_income_purposes_for_role(user):
    if not getattr(user, "is_authenticated", False):
        return IncomePurpose.objects.none()

    default_purpose_map = get_default_income_category_purpose_map()
    existing_keys = {
        (category_name.casefold(), purpose_name.casefold())
        for category_name, purpose_name in IncomePurpose.objects.filter(user=user).values_list(
            "category",
            "name",
        )
    }
    purpose_records_to_create = []

    for category_name, purpose_options in default_purpose_map.items():
        normalized_category = normalize_income_category_name(category_name)
        if not normalized_category:
            continue

        for purpose_name in purpose_options:
            normalized_purpose = normalize_income_purpose_name(purpose_name)
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
                IncomePurpose(
                    user=user,
                    category=normalized_category,
                    name=normalized_purpose,
                )
            )

    for category_name, purpose_name in IncomeRecord.objects.filter(user=user).exclude(
        title=""
    ).values_list("category", "title"):
        normalized_category = normalize_income_category_name(category_name)
        if normalized_category not in (
            INCOME_CATEGORY_COUNTER,
            INCOME_CATEGORY_OFFICE,
        ):
            normalized_category = DEFAULT_INCOME_CATEGORY
        normalized_purpose = normalize_income_purpose_name(purpose_name)
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
            IncomePurpose(
                user=user,
                category=normalized_category,
                name=normalized_purpose,
            )
        )

    if purpose_records_to_create:
        IncomePurpose.objects.bulk_create(purpose_records_to_create, ignore_conflicts=True)

    return IncomePurpose.objects.filter(user=user).order_by("category", "name", "pk")


def get_income_category_purpose_map(user=None):
    purpose_map = {
        INCOME_CATEGORY_COUNTER: [],
        INCOME_CATEGORY_OFFICE: [],
    }

    if not getattr(user, "is_authenticated", False):
        return purpose_map

    for category_name, purpose_name in IncomePurpose.objects.filter(user=user).order_by(
        "category",
        "name",
        "pk",
    ).values_list(
        "category",
        "name",
    ):
        purpose_options = purpose_map.setdefault(category_name, [])
        if purpose_name not in purpose_options:
            purpose_options.append(purpose_name)

    return purpose_map


def get_or_create_role_income_purpose(user, category_name, name):
    normalized_category = normalize_income_category_name(category_name)
    normalized_name = normalize_income_purpose_name(name)
    if (
        not normalized_category
        or not normalized_name
        or not getattr(user, "is_authenticated", False)
    ):
        return None

    existing = (
        IncomePurpose.objects.filter(user=user)
        .filter(category__iexact=normalized_category, name__iexact=normalized_name)
        .first()
    )
    if existing is not None:
        return existing
    return IncomePurpose.objects.create(
        user=user,
        category=normalized_category,
        name=normalized_name,
    )
