from django.db.models import Q

USER_ROLE_ADMIN = "admin"
USER_ROLE_STORE_ADMIN = "store_admin"
USER_ROLE_STAFF = "staff"

USER_ROLE_CHOICES = [
    (USER_ROLE_ADMIN, "Admin"),
    (USER_ROLE_STORE_ADMIN, "Store Admin"),
    (USER_ROLE_STAFF, "Staff"),
]


def get_user_role(user):
    if user.is_superuser:
        return USER_ROLE_ADMIN
    if user.is_staff:
        return USER_ROLE_STORE_ADMIN
    return USER_ROLE_STAFF


def get_user_role_label(user):
    role = get_user_role(user)
    return dict(USER_ROLE_CHOICES)[role]


def get_user_role_filter(user, field_name="user"):
    role = get_user_role(user)

    if role == USER_ROLE_ADMIN:
        return Q(**{f"{field_name}__is_superuser": True})
    if role == USER_ROLE_STORE_ADMIN:
        return Q(
            **{
                f"{field_name}__is_superuser": False,
                f"{field_name}__is_staff": True,
            }
        )
    return Q(
        **{
            f"{field_name}__is_superuser": False,
            f"{field_name}__is_staff": False,
        }
    )


def filter_queryset_by_role(queryset, user, field_name="user"):
    if not getattr(user, "is_authenticated", False):
        return queryset.none()
    return queryset.filter(get_user_role_filter(user, field_name))


def apply_user_role(user, role):
    user.is_superuser = role == USER_ROLE_ADMIN
    user.is_staff = role in {USER_ROLE_ADMIN, USER_ROLE_STORE_ADMIN}
    return user


def infer_role_from_source(username, master_name, user_type=False):
    combined_value = f"{username} {master_name}".strip().lower()

    if "admin" == username.strip().lower() or "admin" == master_name.strip().lower():
        return USER_ROLE_ADMIN
    if "storeadmin" in combined_value or "store admin" in combined_value or user_type:
        return USER_ROLE_STORE_ADMIN
    return USER_ROLE_STAFF
