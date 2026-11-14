from django.urls import path
from rest_framework.routers import SimpleRouter

from .design_api import (
    DesignImagesView,
    DesignTokenView,
    DesignView,
    LayoutCreateView,
    LayoutDetailView,
    LayoutDiscardView,
    LayoutDuplicateView,
    LayoutPublishView,
    LayoutRestoreView,
    LayoutVersionsView,
)
from .api import (
    ContactMessageViewSet,
    ConversionsRetryView,
    ConversionsTestView,
    ConversionsView,
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
    path("store/conversions/", ConversionsView.as_view(), name="store-conversions"),
    path("store/conversions/test/", ConversionsTestView.as_view(), name="store-conversions-test"),
    path("store/conversions/retry/", ConversionsRetryView.as_view(), name="store-conversions-retry"),
    path("store/design/", DesignView.as_view(), name="store-design"),
    path("store/design/token/", DesignTokenView.as_view(), name="store-design-token"),
    path("store/design/images/", DesignImagesView.as_view(), name="store-design-images"),
    path("store/design/layouts/", LayoutCreateView.as_view(), name="store-layouts"),
    path("store/design/layouts/<int:pk>/", LayoutDetailView.as_view(), name="store-layout"),
    path("store/design/layouts/<int:pk>/publish/", LayoutPublishView.as_view(), name="store-layout-publish"),
    path("store/design/layouts/<int:pk>/discard/", LayoutDiscardView.as_view(), name="store-layout-discard"),
    path("store/design/layouts/<int:pk>/duplicate/", LayoutDuplicateView.as_view(), name="store-layout-duplicate"),
    path("store/design/layouts/<int:pk>/versions/", LayoutVersionsView.as_view(), name="store-layout-versions"),
    path("store/design/layouts/<int:pk>/versions/<int:version>/restore/", LayoutRestoreView.as_view(), name="store-layout-restore"),
] + router.urls
