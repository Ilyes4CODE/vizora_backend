from django.db.models import F, Q
from django.utils.crypto import constant_time_compare
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import audit
from apps.core.exceptions import ApiError, conflict
from apps.orders.models import Order, OrderStatus

from .account import (
    ORDER_EVENTS,
    delivered_months,
    maystro_payments,
    mirror_orders,
    payment_months,
    real_balance,
    record_payment,
    start_background_sync,
    summary,
    sync_account,
    sync_status,
    upsert_event,
)
from .live import filter_parcels, live_mode, parcel_counts, parcel_history, refresh_money, refresh_parcels, search_parcels
from .maystro import MaystroError, decode_webhook, get_client
from .models import MaystroConfig, MaystroOrder, MaystroPayment, Shipment
from .serializers import (
    AccountSyncSerializer,
    LiveParcelSerializer,
    ManualStatusSerializer,
    MaystroConfigSerializer,
    MaystroOrderSerializer,
    MaystroPaymentSerializer,
    ParcelDetailSerializer,
    ParcelSerializer,
    ReceiveReturnSerializer,
    SendSerializer,
)
from .services import (
    apply_status_update,
    filter_display_stage,
    parse_moment,
    receive_return,
    send_orders,
    stage_counts,
    sync_catalog,
    sync_parcels,
)


class ParcelViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    section = "shipping"
    queryset = Shipment.objects.select_related("order__wilaya").prefetch_related("events")
    search_fields = ["tracking_number", "order__reference", "order__customer_name", "order__phone"]

    def get_queryset(self):
        queryset = super().get_queryset()
        stage = self.request.query_params.get("stage")
        if self.action == "list" and stage:
            return filter_display_stage(queryset, stage)
        if self.action == "list":
            return queryset.filter(Q(stage__in=["transit", "out", "desk", "failed"]))
        return queryset

    def get_serializer_class(self):
        return ParcelSerializer if self.action == "list" else ParcelDetailSerializer

    def list(self, request, *args, **kwargs):
        if not live_mode():
            return super().list(request, *args, **kwargs)
        refresh_parcels()
        config = MaystroConfig.load()
        queryset = search_parcels(filter_parcels(config, request.query_params.get("stage", "")), request.query_params.get("search"))
        queryset = queryset.select_related("shipment__order").order_by(F("remote_updated_at").desc(nulls_last=True), "-id")
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(LiveParcelSerializer(page, many=True).data)

    def retrieve(self, request, *args, **kwargs):
        if not live_mode():
            return super().retrieve(request, *args, **kwargs)
        row = get_object_or_404(MaystroOrder.objects.select_related("shipment__order"), pk=kwargs["pk"])
        data = LiveParcelSerializer(row).data
        try:
            data["events"] = parcel_history(row)
            data["events_error"] = ""
            data["delivery_attempts"] = sum(1 for event in data["events"] if event["raw_status"] == "31")
        except MaystroError as error:
            data["events"] = []
            data["events_error"] = error.message
        return Response(data)

    @action(detail=False, methods=["get"])
    def counts(self, request):
        if live_mode():
            refresh_parcels()
            config = MaystroConfig.load()
            return Response({**parcel_counts(config), "live": True, "refreshed_at": config.live_parcels_at, "error": config.live_error})
        return Response(stage_counts())

    def linked_shipment(self):
        if not live_mode():
            return self.get_object()
        row = get_object_or_404(MaystroOrder, pk=self.kwargs["pk"])
        if row.shipment_id is None:
            raise conflict("هذا الطرد أُنشئ في Maystro مباشرة وليس له طلبية في Vizora", "parcel_not_linked")
        return row.shipment

    @action(detail=True, methods=["post"], url_path="status", serializer_class=ManualStatusSerializer)
    def manual_status(self, request, pk=None):
        if live_mode():
            raise conflict("الحالة تُقرأ مباشرة من Maystro في الوضع المباشر", "status_from_maystro")
        shipment = self.get_object()
        serializer = ManualStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        apply_status_update(shipment.pk, f"manual:{data['stage']}", data.get("message", ""), stage=data["stage"])
        audit.record("parcel_status_manual", f"غيّر حالة الطرد {shipment.tracking_number} يدوياً", request=request, target=shipment)
        return Response(ParcelDetailSerializer(self.get_queryset().get(pk=shipment.pk)).data)

    @action(detail=True, methods=["post"], url_path="receive-return", serializer_class=ReceiveReturnSerializer)
    def receive_return_action(self, request, pk=None):
        shipment = self.linked_shipment()
        serializer = ReceiveReturnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        receive_return(shipment.pk, serializer.validated_data["condition"], request.user)
        audit.record("return_received", f"استلم مرتجع {shipment.tracking_number}", request=request, target=shipment)
        if live_mode():
            return Response(LiveParcelSerializer(MaystroOrder.objects.select_related("shipment__order").get(pk=pk)).data)
        return Response(ParcelDetailSerializer(self.get_queryset().get(pk=shipment.pk)).data)


class SendToCourierView(APIView):
    section = "shipping"
    serializer_class = SendSerializer

    def post(self, request):
        serializer = SendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        order_ids = data.get("order_ids") or list(
            Order.objects.filter(status=OrderStatus.CONFIRMED).order_by("confirmed_at").values_list("pk", flat=True)[:500]
        )
        result = send_orders(order_ids, request.user)
        if data["all_confirmed"]:
            result["failed"] = [item for item in result["failed"] if item["code"] != "invalid_transition"]
        audit.record("orders_sent", f"أرسل {len(result['sent'])} طلبية إلى Maystro", request=request)
        return Response(result)


class SyncView(APIView):
    section = "shipping"

    def post(self, request):
        return Response(sync_parcels())


class MaystroConfigView(APIView):
    section = "security"
    read_sections = ("security", "shipping")
    serializer_class = MaystroConfigSerializer

    def get(self, request):
        return Response(MaystroConfigSerializer(MaystroConfig.load()).data)

    def put(self, request):
        config = MaystroConfig.load()
        serializer = MaystroConfigSerializer(config, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        audit.record("maystro_config_updated", "عدّل إعدادات Maystro", request=request)
        return Response(serializer.data)


class MaystroTestView(APIView):
    section = "security"

    def post(self, request):
        if not MaystroConfig.load().encrypted_token:
            raise conflict("أدخل مفتاح Maystro أولاً في «الحماية والنسخ» ← «ربط Maystro»", "maystro_not_configured")
        try:
            get_client().ping()
        except MaystroError as error:
            raise ApiError(error.message, error.code, status.HTTP_502_BAD_GATEWAY) from error
        return Response({"connected": True})


class MaystroAccountSyncView(APIView):
    section = ("shipping", "dues")
    read_sections = ("dues",)
    serializer_class = AccountSyncSerializer

    def get(self, request):
        return Response(sync_status())

    def post(self, request):
        serializer = AccountSyncSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data["background"]:
            start_background_sync()
            audit.record("maystro_account_sync_started", "بدأ مزامنة كاملة لطلبيات Maystro", request=request)
            return Response({"started": True}, status=status.HTTP_202_ACCEPTED)
        result = sync_account(full=serializer.validated_data["full"])
        audit.record("maystro_account_synced", f"زامن طلبيات Maystro ({result['fetched']} طلبية)", request=request)
        return Response(result)


class MaystroSummaryView(APIView):
    section = "dues"

    def get(self, request):
        refresh_money()
        return Response(summary(request.query_params))


class MaystroOrdersView(generics.ListAPIView):
    section = "dues"
    serializer_class = MaystroOrderSerializer
    filter_backends = []

    def get_queryset(self):
        return mirror_orders(self.request.query_params)


class MaystroBalanceView(APIView):
    section = "dues"

    def get(self, request):
        refresh_money()
        return Response(real_balance())


class MaystroPaymentViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    section = "dues"
    serializer_class = MaystroPaymentSerializer
    filter_backends = []

    def get_queryset(self):
        return maystro_payments(self.request.query_params)

    def list(self, request, *args, **kwargs):
        refresh_money()
        return super().list(request, *args, **kwargs)


class MaystroPaymentMonthsView(APIView):
    section = "dues"

    def get(self, request):
        refresh_money()
        return Response(payment_months())


class MaystroDeliveredMonthsView(APIView):
    section = "dues"

    def get(self, request):
        return Response(delivered_months())


class SyncCatalogView(APIView):
    section = "security"

    def post(self, request):
        result = sync_catalog()
        audit.record("maystro_catalog_synced", "زامن الولايات والبلديات والمنتجات مع Maystro", request=request)
        return Response(result)


class WebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = []

    def post(self, request, token):
        config = MaystroConfig.load()
        if not constant_time_compare(token, config.webhook_token):
            raise ApiError("رمز غير صالح", "invalid_webhook_token", status.HTTP_403_FORBIDDEN)
        message = decode_webhook(request.body.decode("utf-8", errors="replace"))
        payload = message.get("payload") if isinstance(message, dict) else None
        if not isinstance(payload, dict):
            raise ApiError("بيانات ناقصة", "invalid_webhook_payload")
        event = message.get("event")
        if event == "StorePayment":
            created, matched = record_payment(payload)
            return Response({"result": "payment_recorded" if created else "recorded", "orders": matched})
        if event and event not in ORDER_EVENTS:
            return Response({"result": "ignored"})
        upsert_event(payload)
        tracking = payload.get("display_id_order")
        external_id = payload.get("id")
        raw_status = payload.get("status")
        if (not tracking and not external_id) or raw_status in (None, ""):
            return Response({"result": "ignored"})
        condition = Q(tracking_number=str(tracking)) if tracking else Q(external_id=str(external_id))
        shipment = Shipment.objects.filter(condition).first()
        if shipment is None:
            return Response({"result": "ignored"})
        _, changed = apply_status_update(shipment.pk, raw_status, occurred_at=parse_moment(payload.get("last_update")))
        return Response({"result": "applied" if changed else "recorded"})
