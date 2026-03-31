INCOME_CATEGORY_COUNTER = "Counter Income"
INCOME_CATEGORY_OFFICE = "Office Income"
DEFAULT_INCOME_CATEGORY = INCOME_CATEGORY_COUNTER

INCOME_CATEGORY_CHOICES = (
    (INCOME_CATEGORY_COUNTER, INCOME_CATEGORY_COUNTER),
    (INCOME_CATEGORY_OFFICE, INCOME_CATEGORY_OFFICE),
)
INCOME_CATEGORY_VALUES = tuple(choice[0] for choice in INCOME_CATEGORY_CHOICES)


def normalize_income_category_name(value):
    normalized = " ".join(str(value or "").strip().split())
    normalized_casefold = normalized.casefold()
    if normalized_casefold == INCOME_CATEGORY_COUNTER.casefold():
        return INCOME_CATEGORY_COUNTER
    if normalized_casefold == INCOME_CATEGORY_OFFICE.casefold():
        return INCOME_CATEGORY_OFFICE
    return normalized
