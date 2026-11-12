from rest_framework import serializers

from .account import CANCELLED, CANCELLED_LABELS, outcome_of
from .live import DELIVERY_LABELS, alert_label, parcel_display_stage, parcel_stage, status_label
from .maystro import STATUS_LABELS
from .models import MaystroConfig, MaystroOrder, MaystroPayment, Shipment, ShipmentEvent, Stage
from .services import display_stage

DISPLAY_LABELS = {**dict(Stage.choices), "stuck": "متوقف"}


class ShipmentEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShipmentEvent
        fields = ["id", "raw_status", "stage", "message", "occurred_at"]


class ParcelSerializer(serializers.ModelSerializer):
    order_id = serializers.IntegerField(source="order.pk", read_only=True)
    reference = serializers.CharField(source="order.reference", read_only=True)
    customer_name = serializers.CharField(source="order.customer_name", read_only=True)
    phone = serializers.CharField(source="order.phone", read_only=True)
    wilaya_name = serializers.CharField(source="order.wilaya.name_ar", read_only=True)
    amount = serializers.IntegerField(source="order.total", read_only=True)
    display_stage = serializers.SerializerMethodField()
    display_stage_label = serializers.SerializerMethodField()

    class Meta:
        model = Shipment
        fields = [
            "id", "order_id", "reference", "tracking_number", "customer_name", "phone", "wilaya_name", "amount",
            "stage", "display_stage", "display_stage_label", "raw_status", "delivery_attempts", "return_fee",
            "return_condition", "return_received_at", "last_update_at", "created_at",
        ]

    def get_display_stage(self, obj):
        return display_stage(obj)

    def get_display_stage_label(self, obj):
        return DISPLAY_LABELS[display_stage(obj)]


class ParcelDetailSerializer(ParcelSerializer):
    events = ShipmentEventSerializer(many=True, read_only=True)

    class Meta(ParcelSerializer.Meta):
        fields = ParcelSerializer.Meta.fields + ["events"]


class LiveParcelSerializer(serializers.ModelSerializer):
    kind = serializers.SerializerMethodField()
    order_id = serializers.SerializerMethodField()
    reference = serializers.SerializerMethodField()
    tracking_number = serializers.CharField(source="display_id", read_only=True)
    phone = serializers.CharField(source="customer_phone", read_only=True)
    phone2 = serializers.CharField(source="customer_phone2", read_only=True)
    wilaya_name = serializers.CharField(source="wilaya", read_only=True)
    commune = serializers.CharField(source="commune_name", read_only=True)
    amount = serializers.IntegerField(source="total_price", read_only=True)
    products = serializers.SerializerMethodField()
    stage = serializers.SerializerMethodField()
    display_stage = serializers.SerializerMethodField()
    display_stage_label = serializers.SerializerMethodField()
    raw_status = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()
    alert_reason = serializers.SerializerMethodField()
    delivery_attempts = serializers.SerializerMethodField()
    return_fee = serializers.SerializerMethodField()
    return_condition = serializers.SerializerMethodField()
    return_received_at = serializers.SerializerMethodField()
    last_update_at = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(source="ordered_at", read_only=True)

    class Meta:
        model = MaystroOrder
        fields = [
            "id", "kind", "order_id", "shipment_id", "reference", "tracking_number", "customer_name", "phone", "phone2",
            "wilaya_name", "commune", "address", "amount", "products", "stage", "display_stage", "display_stage_label",
            "raw_status", "status_label", "alert_reason", "postponed_to", "delivery_attempts", "return_fee",
            "return_condition", "return_received_at", "last_update_at", "created_at",
        ]

    def status_map(self):
        if "status_map" not in self.context:
            self.context["status_map"] = MaystroConfig.load().status_map
        return self.context["status_map"]

    def get_kind(self, obj):
        return "maystro"

    def get_products(self, obj):
        return "، ".join(part.strip() for part in obj.product_names.split(";") if part.strip())

    def get_order_id(self, obj):
        return obj.shipment.order_id if obj.shipment_id else None

    def get_reference(self, obj):
        return obj.shipment.order.reference if obj.shipment_id else obj.display_id

    def get_stage(self, obj):
        return parcel_stage(obj, self.status_map())

    def get_display_stage(self, obj):
        return parcel_display_stage(obj, self.status_map())

    def get_display_stage_label(self, obj):
        return DISPLAY_LABELS.get(parcel_display_stage(obj, self.status_map()), "")

    def get_raw_status(self, obj):
        return str(obj.status)

    def get_status_label(self, obj):
        return status_label(obj)

    def get_alert_reason(self, obj):
        return alert_label(obj.alert_reason)

    def get_delivery_attempts(self, obj):
        return obj.shipment.delivery_attempts if obj.shipment_id else None

    def get_return_fee(self, obj):
        return obj.return_fees or 0

    def get_return_condition(self, obj):
        return obj.shipment.return_condition if obj.shipment_id else ""

    def get_return_received_at(self, obj):
        return obj.shipment.return_received_at if obj.shipment_id else None

    def get_last_update_at(self, obj):
        return obj.remote_updated_at or obj.synced_at


class SuiviParcelSerializer(LiveParcelSerializer):
    total = serializers.IntegerField(source="total_price", read_only=True)
    subtotal = serializers.IntegerField(source="product_price", read_only=True)
    delivery_fee = serializers.IntegerField(source="delivery_price", read_only=True)
    status = serializers.SerializerMethodField()
    delivery_type_label = serializers.SerializerMethodField()
    call_attempts = serializers.SerializerMethodField()
    events = serializers.SerializerMethodField()
    shipment = serializers.SerializerMethodField()

    class Meta(LiveParcelSerializer.Meta):
        fields = LiveParcelSerializer.Meta.fields + [
            "total", "subtotal", "delivery_fee", "status", "delivery_type_label", "call_attempts", "suivi_attempts",
            "suivi_next_at", "events", "shipment",
        ]

    def get_status(self, obj):
        return "shipping"

    def get_delivery_type_label(self, obj):
        return DELIVERY_LABELS.get(obj.delivery_type, "")

    def get_call_attempts(self, obj):
        return 0

    def get_events(self, obj):
        return [
            {
                "id": call.pk,
                "stage": "suivi",
                "outcome": call.outcome,
                "message": call.message,
                "note": call.note,
                "user": call.user_id,
                "user_name": call.user.full_name if call.user else "",
                "created_at": call.created_at,
            }
            for call in obj.calls.all()
        ]

    def get_shipment(self, obj):
        return {
            "id": obj.shipment_id,
            "tracking_number": obj.display_id,
            "stage": parcel_stage(obj, self.status_map()),
            "stage_label": status_label(obj),
            "delivery_attempts": obj.shipment.delivery_attempts if obj.shipment_id else None,
        }


class SendSerializer(serializers.Serializer):
    order_ids = serializers.ListField(child=serializers.IntegerField(), required=False, max_length=500)
    all_confirmed = serializers.BooleanField(default=False)

    def validate(self, attrs):
        if not attrs.get("order_ids") and not attrs["all_confirmed"]:
            raise serializers.ValidationError({"order_ids": "حدد الطلبيات أو اختر كل المؤكدة"})
        return attrs


class ManualStatusSerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=Stage.choices)
    message = serializers.CharField(required=False, allow_blank=True, max_length=255)


class ReceiveReturnSerializer(serializers.Serializer):
    condition = serializers.ChoiceField(choices=["good", "damaged"])


class AccountSyncSerializer(serializers.Serializer):
    full = serializers.BooleanField(default=False)
    background = serializers.BooleanField(default=False)


class MaystroPaymentSerializer(serializers.ModelSerializer):
    source_label = serializers.CharField(source="get_source_display", read_only=True)

    class Meta:
        model = MaystroPayment
        fields = ["id", "source", "source_label", "paid_on", "amount", "orders_count"]


class MaystroOrderSerializer(serializers.ModelSerializer):
    status_label = serializers.SerializerMethodField()
    outcome = serializers.SerializerMethodField()
    paid = serializers.SerializerMethodField()

    class Meta:
        model = MaystroOrder
        fields = [
            "maystro_id", "display_id", "status", "status_label", "outcome", "product_names", "product_price", "delivery_price",
            "total_price", "paid", "wilaya", "commune_name", "ordered_at", "shipped_at", "delivered_at", "is_refund", "is_exchange",
            "shipment_id",
        ]

    def get_status_label(self, obj):
        if obj.status == CANCELLED:
            return CANCELLED_LABELS[obj.shipped_at is not None]
        return STATUS_LABELS.get(str(obj.status), str(obj.status))

    def get_outcome(self, obj):
        return outcome_of(obj.status, obj.shipped_at is not None)

    def get_paid(self, obj):
        return obj.paid_to_store or obj.payment_id is not None


class MaystroConfigSerializer(serializers.ModelSerializer):
    token = serializers.CharField(write_only=True, required=False, allow_blank=True)
    has_token = serializers.SerializerMethodField()
    webhook_path = serializers.SerializerMethodField()
    mirror_orders = serializers.SerializerMethodField()
    store_id = serializers.CharField(required=False, allow_blank=True, max_length=64)

    class Meta:
        model = MaystroConfig
        fields = [
            "store_id", "status_map", "return_fee", "token", "has_token", "webhook_path",
            "last_sync_at", "last_sync_ok", "last_sync_error", "mirror_synced_at",
            "mirror_full_sync_at", "mirror_sync_ok", "mirror_sync_error", "mirror_orders",
        ]
        read_only_fields = [
            "last_sync_at", "last_sync_ok", "last_sync_error", "mirror_synced_at",
            "mirror_full_sync_at", "mirror_sync_ok", "mirror_sync_error",
        ]

    def get_mirror_orders(self, obj):
        return MaystroOrder.objects.count()

    def validate_store_id(self, value):
        return value.strip()

    def get_has_token(self, obj):
        return bool(obj.encrypted_token)

    def get_webhook_path(self, obj):
        return f"/api/shipping/webhook/{obj.webhook_token}/"

    def validate_status_map(self, value):
        if any(stage not in Stage.values for stage in value.values()):
            raise serializers.ValidationError("مرحلة غير معروفة")
        return {str(key).strip().lower(): stage for key, stage in value.items()}

    def update(self, instance, validated_data):
        token = validated_data.pop("token", None)
        if token:
            instance.set_token(token)
        return super().update(instance, validated_data)
