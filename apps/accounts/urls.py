from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import (
    ChangePasswordView,
    DeviceViewSet,
    DirectoryView,
    FirstAdminView,
    LoginView,
    LogoutView,
    MeView,
    PermissionMatrixView,
    SessionRefreshView,
    TwoFactorDisableView,
    TwoFactorEnableView,
    TwoFactorSetupView,
    UserViewSet,
)

router = SimpleRouter()
router.register("users", UserViewSet, basename="user")
router.register("devices", DeviceViewSet, basename="device")

urlpatterns = [
    path("auth/setup/", FirstAdminView.as_view(), name="first-admin"),
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/refresh/", SessionRefreshView.as_view(), name="refresh"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/me/", MeView.as_view(), name="me"),
    path("auth/change-password/", ChangePasswordView.as_view(), name="change-password"),
    path("auth/2fa/setup/", TwoFactorSetupView.as_view(), name="2fa-setup"),
    path("auth/2fa/enable/", TwoFactorEnableView.as_view(), name="2fa-enable"),
    path("auth/2fa/disable/", TwoFactorDisableView.as_view(), name="2fa-disable"),
    path("staff-directory/", DirectoryView.as_view(), name="staff-directory"),
    path("permissions/", PermissionMatrixView.as_view(), name="permissions"),
] + router.urls
