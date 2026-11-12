from django.db.models import Count
from django.utils import timezone
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import audit
from apps.core.excel import read_rows, workbook_response
from apps.core.exceptions import conflict
from apps.shipping.live import live_mode, record_parcel_call, refresh_parcels
from apps.shipping.live import suivi_queue as live_queue
from apps.shipping.models import MaystroConfig, MaystroOrder
from apps.shipping.serializers import SuiviParcelSerializer

from .filters import OrderFilter
from .importing import commit_batch, evaluate, suggest_mapping
from .models import ImportBatch, Order, OrderStatus
from .serializers import (
    BulkAssignSerializer,
    CancelSerializer,
    ImportBatchSerializer,
    MappingSerializer,
    OrderDetailSerializer,
    OrderListSerializer,
    OrderWriteSerializer,
    OutcomeSerializer,
    product_summary,
)
from .services import (
    cancel_order,
    confirmation_queue,
    create_order,
    find_duplicate,
    record_confirmation,
    record_suivi,
    suivi_queue,
    update_order,
    visible_to,
)

ORDER_RELATIONS = ("wilaya", "assigned_to", "campaign", "shipment")
TEMPLATE_HEADERS = ["الاسم", "الهاتف", "الولاية", "البلدية", "العنوان", "المنتج", "الكمية", "نوع التوصيل", "الحملة", "ملاحظة"]


def order_queryset():
    return Order.objects.select_related(*ORDER_RELATIONS).prefetch_related("items__product", "events__user")


class OrderViewSet(viewsets.ModelViewSet):
    section = "orders"
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_class = OrderFilter
    search_fields = ["reference", "customer_name", "phone"]
    ordering_fields = ["created_at", "total"]

    def get_queryset(self):
        return order_queryset()

    def get_serializer_class(self):
        if self.action == "list":
            return OrderListSerializer
        if self.action in ("create", "partial_update"):
            return OrderWriteSerializer
        return OrderDetailSerializer

    def detail_response(self, order, status_code=status.HTTP_200_OK):
        fresh = order_queryset().get(pk=order.pk)
        return Response(OrderDetailSerializer(fresh).data, status=status_code)

    def create(self, request, *args, **kwargs):
        serializer = OrderWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not data.pop("allow_duplicate", False):
            existing = find_duplicate(data["phone"], [item["product"].pk for item in data["items"]])
            if existing is not None:
                raise conflict(f"يوجد طلب مشابه لنفس الزبون والمنتج: {existing.reference}", "duplicate_order")
        order = create_order(data, request.user)
        audit.record("order_created", f"أضاف الطلبية {order.reference}", request=request, target=order)
        return self.detail_response(order, status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        order = self.get_object()
        serializer = OrderWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        order = update_order(order.pk, serializer.validated_data, request.user)
        audit.record("order_updated", f"عدّل الطلبية {order.reference}", request=request, target=order)
        return self.detail_response(order)

    @action(detail=False, methods=["get"])
    def counts(self, request):
        params = {key: value for key, value in request.query_params.items() if key != "status"}
        queryset = OrderFilter(params, queryset=Order.objects.all(), request=request).qs
        found = dict(queryset.order_by().values_list("status").annotate(total=Count("id", distinct=True)))
        values = {value: found.get(value, 0) for value in OrderStatus.values}
        return Response({"all": sum(values.values()), **values})

    @action(detail=False, methods=["get"])
    def export(self, request):
        orders = self.filter_queryset(self.get_queryset())
        rows = [
            (
                order.reference, order.customer_name, order.phone, order.wilaya.name_ar, order.commune,
                product_summary(order), order.subtotal, order.delivery_fee, order.total, order.get_status_display(),
                order.assigned_to.full_name if order.assigned_to else "",
                timezone.localtime(order.created_at).strftime("%Y-%m-%d %H:%M"),
            )
            for order in orders
        ]
        headers = ["الطلبية", "الزبون", "الهاتف", "الولاية", "البلدية", "المنتج", "المبلغ", "التوصيل", "المجموع", "الحالة", "الموظف", "التاريخ"]
        return workbook_response("الطلبيات", headers, rows, "orders.xlsx")

    @action(detail=False, methods=["post"], url_path="bulk-assign", serializer_class=BulkAssignSerializer)
    def bulk_assign(self, request):
        serializer = BulkAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        updated = Order.objects.filter(
            pk__in=serializer.validated_data["order_ids"],
            status__in=[OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.SHIPPING],
        ).update(assigned_to=user)
        audit.record("orders_assigned", f"عيّن {updated} طلبية", request=request)
        return Response({"updated": updated})

    @action(detail=True, methods=["post"], serializer_class=CancelSerializer)
    def cancel(self, request, pk=None):
        order = self.get_object()
        serializer = CancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = cancel_order(order.pk, request.user, serializer.validated_data.get("reason", ""))
        audit.record("order_cancelled", f"ألغى الطلبية {order.reference}", request=request, target=order)
        return self.detail_response(order)


class OrderImportViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    section = "orders"
    queryset = ImportBatch.objects.all()
    serializer_class = ImportBatchSerializer
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def create(self, request):
        uploaded = request.FILES.get("file")
        columns, rows = read_rows(uploaded)
        batch = ImportBatch.objects.create(
            file_name=uploaded.name, columns=columns, rows=rows, mapping=suggest_mapping(columns), created_by=request.user
        )
        if all(field in batch.mapping for field in ("customer_name", "phone", "wilaya", "product")):
            _, batch.result = evaluate(batch, batch.mapping)
            batch.save(update_fields=["result"])
        return Response(ImportBatchSerializer(batch).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def template(self, request):
        sample = [("كريم مزيان", "0555004112", "الجزائر", "الدار البيضاء", "", "اسم المنتج", 1, "إلى المنزل", "", "")]
        return workbook_response("نموذج الطلبيات", TEMPLATE_HEADERS, sample, "orders-template.xlsx")

    def editable_batch(self):
        batch = self.get_object()
        if batch.status == ImportBatch.Status.COMMITTED:
            raise conflict("تم استيراد هذا الملف مسبقاً", "import_already_committed")
        return batch

    @action(detail=True, methods=["post"], url_path="validate", serializer_class=MappingSerializer)
    def validate_batch(self, request, pk=None):
        batch = self.editable_batch()
        serializer = MappingSerializer(data=request.data, context={"columns": batch.columns, "current": batch.mapping})
        serializer.is_valid(raise_exception=True)
        batch.mapping = serializer.validated_data["mapping"]
        _, batch.result = evaluate(batch, batch.mapping)
        batch.save(update_fields=["mapping", "result"])
        return Response(ImportBatchSerializer(batch).data)

    @action(detail=True, methods=["post"])
    def commit(self, request, pk=None):
        batch = self.editable_batch()
        serializer = MappingSerializer(data={}, context={"columns": batch.columns, "current": batch.mapping})
        serializer.is_valid(raise_exception=True)
        batch = commit_batch(batch, request.user)
        audit.record("orders_imported", f"استورد {batch.created_count} طلبية من {batch.file_name}", request=request, target=batch)
        return Response(ImportBatchSerializer(batch).data)


def live_suivi_queue():
    if not live_mode():
        return None
    refresh_parcels()
    return live_queue(MaystroConfig.load())


class ConfirmationQueueView(generics.ListAPIView):
    section = "confirmation"
    serializer_class = OrderDetailSerializer
    filter_backends = []

    def stage(self):
        stage = self.request.query_params.get("stage", "confirm")
        if stage not in ("confirm", "suivi"):
            raise ValidationError({"stage": "القيمة يجب أن تكون confirm أو suivi"})
        return stage

    def get_serializer_class(self):
        return SuiviParcelSerializer if getattr(self, "live", False) else OrderDetailSerializer

    def get_queryset(self):
        stage = self.stage()
        self.live = False
        if stage == "suivi":
            parcels = live_suivi_queue()
            if parcels is not None:
                self.live = True
                return parcels.select_related("shipment__order").prefetch_related("calls__user")
        queryset = confirmation_queue() if stage == "confirm" else suivi_queue()
        ordered = queryset.select_related(*ORDER_RELATIONS).prefetch_related("items__product", "events__user").order_by("call_attempts", "created_at", "id")
        return visible_to(ordered, self.request.user)


class ConfirmationCountsView(APIView):
    section = "confirmation"

    def get(self, request):
        parcels = live_suivi_queue()
        return Response(
            {
                "confirm": visible_to(confirmation_queue(), request.user).count(),
                "suivi": parcels.count() if parcels is not None else visible_to(suivi_queue(), request.user).count(),
            }
        )


class ParcelOutcomeView(APIView):
    section = "confirmation"
    serializer_class = OutcomeSerializer

    def post(self, request, pk):
        generics.get_object_or_404(MaystroOrder, pk=pk)
        payload = {key: request.data.get(key) for key in ("outcome", "note", "next_call_at") if key in request.data}
        serializer = OutcomeSerializer(data={**payload, "stage": "suivi"})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        parcel = record_parcel_call(pk, data["outcome"], request.user, data.get("note", ""), data.get("next_call_at"))
        audit.record("parcel_call_outcome", f"سجّل {data['outcome']} للطرد {parcel.display_id}", request=request)
        queryset = MaystroOrder.objects.select_related("shipment__order").prefetch_related("calls__user")
        return Response(SuiviParcelSerializer(queryset.get(pk=pk)).data)


class OutcomeView(APIView):
    section = "confirmation"
    serializer_class = OutcomeSerializer

    def post(self, request, pk):
        order = generics.get_object_or_404(Order, pk=pk)
        if not visible_to(Order.objects.filter(pk=pk), request.user).exists():
            raise conflict("هذه الطلبية عند موظف آخر", "order_taken")
        serializer = OutcomeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        handler = record_confirmation if data["stage"] == "confirm" else record_suivi
        order = handler(order.pk, data["outcome"], request.user, data.get("note", ""), data.get("next_call_at"))
        audit.record("call_outcome", f"سجّل {data['outcome']} للطلبية {order.reference}", request=request, target=order)
        return Response(OrderDetailSerializer(order_queryset().get(pk=order.pk)).data)
