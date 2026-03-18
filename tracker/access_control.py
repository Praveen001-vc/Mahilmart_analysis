from dataclasses import dataclass

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect

from .models import UserModulePermission


TRACKER_PERMISSION_ITEMS = [
    ("Dashboard", "allow_dashboard", "dashboard"),
    ("Sales", "allow_sales", "sales-list"),
    ("Daily Settlement", "allow_daily_settlement", "daily-settlement"),
    ("Income", "allow_income", "income-list"),
    ("Purchases", "allow_purchases", "purchase-list"),
    ("Suppliers", "allow_suppliers", "supplier-list"),
    ("Expenses", "allow_expenses", "expense-list"),
    ("Reports", "allow_reports", "reports"),
]


@dataclass(frozen=True)
class ResolvedPermissionSet:
    allow_dashboard: bool = False
    allow_sales: bool = False
    allow_daily_settlement: bool = False
    allow_income: bool = False
    allow_purchases: bool = False
    allow_suppliers: bool = False
    allow_expenses: bool = False
    allow_reports: bool = False


FULL_ACCESS_PERMISSIONS = ResolvedPermissionSet(
    allow_dashboard=True,
    allow_sales=True,
    allow_daily_settlement=True,
    allow_income=True,
    allow_purchases=True,
    allow_suppliers=True,
    allow_expenses=True,
    allow_reports=True,
)

NO_ACCESS_PERMISSIONS = ResolvedPermissionSet()


def get_user_permission_record(user):
    if not getattr(user, "is_authenticated", False) or user.is_superuser:
        return None
    permission_record, _created = UserModulePermission.objects.get_or_create(user=user)
    return permission_record


def get_resolved_permissions(user):
    if not getattr(user, "is_authenticated", False):
        return NO_ACCESS_PERMISSIONS
    if user.is_superuser:
        return FULL_ACCESS_PERMISSIONS
    return get_user_permission_record(user) or NO_ACCESS_PERMISSIONS


def build_permission_items(permission_record):
    return [
        {
            "label": label,
            "field": field_name,
            "route_name": route_name,
            "enabled": getattr(permission_record, field_name, False),
        }
        for label, field_name, route_name in TRACKER_PERMISSION_ITEMS
    ]


def user_has_permission(user, field_name):
    return bool(getattr(get_resolved_permissions(user), field_name, False))


def get_first_accessible_route_name(user):
    if not getattr(user, "is_authenticated", False):
        return None
    if user.is_superuser:
        return "dashboard"

    permissions = get_resolved_permissions(user)
    for _label, field_name, route_name in TRACKER_PERMISSION_ITEMS:
        if getattr(permissions, field_name, False):
            return route_name
    return None


class ModulePermissionRequiredMixin(LoginRequiredMixin):
    permission_field = ""
    permission_denied_message = "You do not have permission to open this page."

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if user_has_permission(request.user, self.permission_field):
            return super().dispatch(request, *args, **kwargs)

        if self.permission_denied_message:
            messages.error(request, self.permission_denied_message)

        fallback_route_name = get_first_accessible_route_name(request.user)
        current_route_name = getattr(getattr(request, "resolver_match", None), "url_name", "")
        if fallback_route_name and fallback_route_name != current_route_name:
            return redirect(fallback_route_name)
        return redirect("access-denied")
