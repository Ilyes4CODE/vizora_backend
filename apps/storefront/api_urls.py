from django.urls import path
from rest_framework.routers import SimpleRouter

from .api import (
    ContactMessageViewSet,
    ReviewViewSet,
    StockAlertViewSet,
    StoreImageViewSet,
    StoreOfferViewSet,
    StorePageViewSet,
    StoreProductViewSet,
    StoreSettingsView,
    StoreSummaryView,
)

router = SimpleRouter()
router.register("store/products", StoreProductViewSet, basename="store-product")
router.register("store/images", StoreImageViewSet, basename="store-image")
router.register("store/offers", StoreOfferViewSet, basename="store-offer")
router.register("store/pages", StorePageViewSet, basename="store-page")
router.register("store/reviews", ReviewViewSet, basename="store-review")
router.register("store/messages", ContactMessageViewSet, basename="store-message")
router.register("store/alerts", StockAlertViewSet, basename="store-alert")

urlpatterns = [
    path("store/summary/", StoreSummaryView.as_view(), name="store-summary"),
    path("store/settings/", StoreSettingsView.as_view(), name="store-settings"),
] + router.urls
