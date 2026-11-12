from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import (
    MaystroAccountSyncView,
    MaystroBalanceView,
    MaystroConfigView,
    MaystroOrdersView,
    MaystroPaymentViewSet,
    MaystroSummaryView,
    MaystroTestView,
    ParcelViewSet,
    SendToCourierView,
    SyncCatalogView,
    SyncView,
    WebhookView,
)

router = SimpleRouter()
router.register("shipping/parcels", ParcelViewSet, basename="parcel")
router.register("shipping/maystro/payments", MaystroPaymentViewSet, basename="maystro-payment")

urlpatterns = [
    path("shipping/send/", SendToCourierView.as_view(), name="shipping-send"),
    path("shipping/sync/", SyncView.as_view(), name="shipping-sync"),
    path("shipping/config/", MaystroConfigView.as_view(), name="shipping-config"),
    path("shipping/config/test/", MaystroTestView.as_view(), name="shipping-config-test"),
    path("shipping/config/sync-catalog/", SyncCatalogView.as_view(), name="shipping-sync-catalog"),
    path("shipping/maystro/sync/", MaystroAccountSyncView.as_view(), name="maystro-account-sync"),
    path("shipping/maystro/summary/", MaystroSummaryView.as_view(), name="maystro-account-summary"),
    path("shipping/maystro/orders/", MaystroOrdersView.as_view(), name="maystro-account-orders"),
    path("shipping/maystro/balance/", MaystroBalanceView.as_view(), name="maystro-account-balance"),
    path("shipping/webhook/<str:token>/", WebhookView.as_view(), name="shipping-webhook"),
] + router.urls
