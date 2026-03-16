from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from .forms import StyledAuthenticationForm
from .views import (
    DashboardView,
    DailySettlementView,
    ExpenseCategoryListView,
    ExpenseCreateView,
    ExpenseListView,
    HomeRedirectView,
    IncomeCreateView,
    IncomeListView,
    PurchaseCreateView,
    PurchaseDetailView,
    PurchaseInvoiceDownloadView,
    PurchaseListView,
    PurchasePaymentCreateView,
    ReportsView,
    SalesListView,
    SupplierCreateView,
    SupplierListView,
    UserCreateView,
    UserListView,
    UserUpdateView,
)

urlpatterns = [
    path("", HomeRedirectView.as_view(), name="home"),
    path(
        "login/",
        LoginView.as_view(
            template_name="auth/login.html",
            authentication_form=StyledAuthenticationForm,
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("sales/", SalesListView.as_view(), name="sales-list"),
    path("income/", IncomeListView.as_view(), name="income-list"),
    path("income/add/", IncomeCreateView.as_view(), name="income-add"),
    path("purchases/", PurchaseListView.as_view(), name="purchase-list"),
    path("purchases/add/", PurchaseCreateView.as_view(), name="purchase-add"),
    path("purchases/<int:pk>/details/", PurchaseDetailView.as_view(), name="purchase-detail"),
    path("purchases/<int:pk>/invoice/", PurchaseInvoiceDownloadView.as_view(), name="purchase-invoice"),
    path("purchases/<int:pk>/pay/", PurchasePaymentCreateView.as_view(), name="purchase-pay"),
    path("suppliers/", SupplierListView.as_view(), name="supplier-list"),
    path("suppliers/add/", SupplierCreateView.as_view(), name="supplier-add"),
    path("users/", UserListView.as_view(), name="user-list"),
    path("users/add/", UserCreateView.as_view(), name="user-add"),
    path("users/<int:pk>/edit/", UserUpdateView.as_view(), name="user-edit"),
    path("expenses/", ExpenseListView.as_view(), name="expense-list"),
    path("expenses/add/", ExpenseCreateView.as_view(), name="expense-add"),
    path("expenses/categories/", ExpenseCategoryListView.as_view(), name="expense-category-list"),
    path("expenses/settlement/", DailySettlementView.as_view(), name="daily-settlement"),
    path("reports/", ReportsView.as_view(), name="reports"),
]
