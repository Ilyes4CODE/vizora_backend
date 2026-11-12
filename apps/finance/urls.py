from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import AccountingExportView, AccountingSummaryView, CampaignViewSet, ExpenseViewSet, SettlementViewSet

router = SimpleRouter()
router.register("expenses", ExpenseViewSet, basename="expense")
router.register("campaigns", CampaignViewSet, basename="campaign")
router.register("settlements", SettlementViewSet, basename="settlement")

urlpatterns = [
    path("accounting/summary/", AccountingSummaryView.as_view(), name="accounting-summary"),
    path("accounting/export/", AccountingExportView.as_view(), name="accounting-export"),
] + router.urls
