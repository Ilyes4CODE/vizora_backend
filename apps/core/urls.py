from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import (
    AuditLogView,
    BackupViewSet,
    BusinessProfileView,
    HealthView,
    SecuritySettingsView,
    WilayaViewSet,
)

router = SimpleRouter()
router.register("wilayas", WilayaViewSet, basename="wilaya")
router.register("backups", BackupViewSet, basename="backup")

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("settings/business/", BusinessProfileView.as_view(), name="settings-business"),
    path("settings/security/", SecuritySettingsView.as_view(), name="settings-security"),
    path("audit-log/", AuditLogView.as_view(), name="audit-log"),
] + router.urls
