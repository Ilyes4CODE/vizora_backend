from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import ConfirmationCountsView, ConfirmationQueueView, OrderImportViewSet, OrderViewSet, OutcomeView, ParcelOutcomeView

router = SimpleRouter()
router.register("orders/import", OrderImportViewSet, basename="order-import")
router.register("orders", OrderViewSet, basename="order")

urlpatterns = [
    path("confirmation/queue/", ConfirmationQueueView.as_view(), name="confirmation-queue"),
    path("confirmation/counts/", ConfirmationCountsView.as_view(), name="confirmation-counts"),
    path("confirmation/orders/<int:pk>/outcome/", OutcomeView.as_view(), name="confirmation-outcome"),
    path("confirmation/parcels/<int:pk>/outcome/", ParcelOutcomeView.as_view(), name="confirmation-parcel-outcome"),
] + router.urls
