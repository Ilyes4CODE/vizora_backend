from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import audit
from apps.core.dates import month_bounds, parse_month
from apps.core.excel import workbook_response
from apps.core.exceptions import conflict

from .models import Campaign, Expense, Settlement
from .serializers import (
    CampaignSerializer,
    CampaignSpendSerializer,
    ExpenseSerializer,
    MarkPaidSerializer,
    SettlementCreateSerializer,
    SettlementSerializer,
)
from .services import campaign_performance, create_settlement, dues_summary, import_statement, profit_summary


class AccountingSummaryView(APIView):
    section = "accounting"

    def get(self, request):
        return Response(profit_summary(parse_month(request.query_params.get("month"))))


class AccountingExportView(APIView):
    section = "accounting"

    def get(self, request):
        first_day = parse_month(request.query_params.get("month"))
        summary = profit_summary(first_day)
        rows = [("المبيعات المسلّمة", summary["sales"])]
        rows += [(line["label"], line["amount"]) for line in summary["breakdown"]]
        rows += [("إجمالي المصاريف", summary["expenses_total"]), ("صافي الربح", summary["net_profit"])]
        return workbook_response("المحاسبة", ["البيان", "المبلغ (دج)"], rows, f"accounting-{summary['month']}.xlsx")


class ExpenseViewSet(viewsets.ModelViewSet):
    section = "accounting"
    queryset = Expense.objects.select_related("created_by", "campaign")
    serializer_class = ExpenseSerializer
    filterset_fields = ["category", "campaign"]
    search_fields = ["title"]

    def get_queryset(self):
        queryset = super().get_queryset()
        month = self.request.query_params.get("month")
        if month:
            start, end = month_bounds(parse_month(month))
            queryset = queryset.filter(date__gte=start.date(), date__lt=end.date())
        return queryset

    def perform_create(self, serializer):
        expense = serializer.save(created_by=self.request.user)
        audit.record("expense_created", f"أضاف مصروف {expense.title}", request=self.request, target=expense)

    def perform_update(self, serializer):
        expense = serializer.save()
        audit.record("expense_updated", f"عدّل مصروف {expense.title}", request=self.request, target=expense)

    def perform_destroy(self, instance):
        audit.record("expense_deleted", f"حذف مصروف {instance.title}", request=self.request)
        instance.delete()


class CampaignViewSet(viewsets.ModelViewSet):
    section = "campaigns"
    read_sections = ("campaigns", "orders", "accounting")
    action_sections = {"performance": ("campaigns",)}
    queryset = Campaign.objects.all()
    serializer_class = CampaignSerializer
    pagination_class = None
    filterset_fields = ["platform", "is_active"]

    def perform_create(self, serializer):
        campaign = serializer.save()
        audit.record("campaign_created", f"أضاف الحملة {campaign.name}", request=self.request, target=campaign)

    def perform_update(self, serializer):
        campaign = serializer.save()
        audit.record("campaign_updated", f"عدّل الحملة {campaign.name}", request=self.request, target=campaign)

    def destroy(self, request, *args, **kwargs):
        campaign = self.get_object()
        if campaign.orders.exists() or campaign.expenses.exists():
            campaign.is_active = False
            campaign.save(update_fields=["is_active"])
        else:
            campaign.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["get"])
    def performance(self, request):
        first_day = parse_month(request.query_params.get("month"))
        return Response(campaign_performance(first_day, request.query_params.get("platform")))

    @action(detail=True, methods=["post"], serializer_class=CampaignSpendSerializer)
    def spend(self, request, pk=None):
        campaign = self.get_object()
        serializer = CampaignSpendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        expense = Expense.objects.create(
            date=data["date"],
            title=data.get("title") or f"إنفاق حملة {campaign.name}",
            category=Expense.Category.ADS,
            amount=data["amount"],
            campaign=campaign,
            created_by=request.user,
        )
        audit.record("campaign_spend", f"سجّل إنفاق الحملة {campaign.name}", request=request, target=campaign)
        return Response(ExpenseSerializer(expense).data, status=status.HTTP_201_CREATED)


class SettlementViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    section = "dues"
    queryset = Settlement.objects.prefetch_related("statement_lines")
    serializer_class = SettlementSerializer
    pagination_class = None
    filterset_fields = ["status"]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def get_queryset(self):
        queryset = super().get_queryset()
        month = self.request.query_params.get("month")
        if month:
            start, end = month_bounds(parse_month(month))
            queryset = queryset.filter(period_start__gte=start.date(), period_start__lt=end.date())
        return queryset

    def create(self, request):
        serializer = SettlementCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        settlement = create_settlement(user=request.user, **serializer.validated_data)
        audit.record("settlement_created", f"أنشأ دفعة {settlement.period_start} - {settlement.period_end}", request=request, target=settlement)
        return Response(SettlementSerializer(settlement).data, status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        if instance.status == Settlement.Status.PAID:
            raise conflict("لا يمكن حذف دفعة مسددة", "settlement_paid")
        audit.record("settlement_deleted", f"حذف دفعة {instance.period_start} - {instance.period_end}", request=self.request)
        instance.delete()

    @action(detail=False, methods=["get"])
    def summary(self, request):
        return Response(dues_summary(parse_month(request.query_params.get("month"))))

    @action(detail=True, methods=["post"])
    def statement(self, request, pk=None):
        settlement = import_statement(self.get_object(), request.FILES.get("file"))
        audit.record("statement_imported", "استورد كشف Maystro", request=request, target=settlement)
        return Response(SettlementSerializer(settlement).data)

    @action(detail=True, methods=["post"], url_path="mark-paid", serializer_class=MarkPaidSerializer)
    def mark_paid(self, request, pk=None):
        settlement = self.get_object()
        if settlement.status == Settlement.Status.PAID:
            raise conflict("هذه الدفعة مسددة مسبقاً", "settlement_paid")
        serializer = MarkPaidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        settlement.status = Settlement.Status.PAID
        settlement.paid_on = serializer.validated_data.get("paid_on") or timezone.localdate()
        settlement.save(update_fields=["status", "paid_on"])
        audit.record("settlement_paid", f"سجّل استلام دفعة {settlement.period_start} - {settlement.period_end}", request=request, target=settlement)
        return Response(SettlementSerializer(settlement).data)
