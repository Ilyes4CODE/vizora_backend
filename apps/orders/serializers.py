from rest_framework import serializers

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.core.models import Wilaya
from apps.finance.models import Campaign

from .models import ConfirmationOutcome, DeliveryType, ImportBatch, Order, OrderEvent, OrderItem, SuiviOutcome
from .services import normalize_phone, phone_problem
from .importing import FIELD_ALIASES, REQUIRED_FIELDS


def item_label(item):
    return f"{item.product.name} ({item.variant})" if item.variant else item.product.name


def product_summary(order):
    return "، ".join(f"{item_label(item)} × {item.quantity}" for item in order.items.all())


class OrderItemInputSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.filter(is_active=True))
    quantity = serializers.IntegerField(min_value=1, max_value=1000)
    variant = serializers.CharField(max_length=150, required=False, allow_blank=True)


class OrderWriteSerializer(serializers.Serializer):
    customer_name = serializers.CharField(max_length=150)
    phone = serializers.CharField(max_length=25)
    wilaya = serializers.PrimaryKeyRelatedField(queryset=Wilaya.objects.all())
    commune = serializers.CharField(max_length=80, required=False, allow_blank=True)
    address = serializers.CharField(max_length=255, required=False, allow_blank=True)
    delivery_type = serializers.ChoiceField(choices=DeliveryType.choices, required=False)
    items = OrderItemInputSerializer(many=True, allow_empty=False)
    campaign = serializers.PrimaryKeyRelatedField(queryset=Campaign.objects.all(), required=False, allow_null=True)
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(is_active=True), required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True)
    allow_duplicate = serializers.BooleanField(required=False)

    def validate_phone(self, value):
        if phone_problem(value):
            raise serializers.ValidationError("رقم الهاتف غير صالح")
        return normalize_phone(value)


class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = OrderItem
        fields = ["id", "product", "product_name", "variant", "quantity", "unit_price"]


class OrderEventSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.full_name", read_only=True, default="")

    class Meta:
        model = OrderEvent
        fields = ["id", "stage", "outcome", "message", "note", "user", "user_name", "created_at"]


class OrderListSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    wilaya_name = serializers.CharField(source="wilaya.name_ar", read_only=True)
    assigned_name = serializers.CharField(source="assigned_to.full_name", read_only=True, default="")
    products = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id", "reference", "customer_name", "phone", "wilaya", "wilaya_name", "commune", "products",
            "subtotal", "delivery_fee", "total", "status", "status_label", "assigned_to", "assigned_name",
            "call_attempts", "source", "created_at",
        ]

    def get_products(self, obj):
        return product_summary(obj)


class OrderDetailSerializer(OrderListSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    events = OrderEventSerializer(many=True, read_only=True)
    delivery_type_label = serializers.CharField(source="get_delivery_type_display", read_only=True)
    campaign_name = serializers.CharField(source="campaign.name", read_only=True, default="")
    shipment = serializers.SerializerMethodField()

    class Meta(OrderListSerializer.Meta):
        fields = OrderListSerializer.Meta.fields + [
            "address", "delivery_type", "delivery_type_label", "campaign", "campaign_name", "note",
            "next_call_at", "cancel_reason", "suivi_attempts", "suivi_next_at", "confirmed_at", "shipped_at",
            "delivered_at", "returned_at", "cancelled_at", "items", "events", "shipment",
        ]

    def get_shipment(self, obj):
        shipment = getattr(obj, "shipment", None)
        if shipment is None:
            return None
        return {
            "id": shipment.pk,
            "tracking_number": shipment.tracking_number,
            "stage": shipment.stage,
            "stage_label": shipment.get_stage_display(),
            "delivery_attempts": shipment.delivery_attempts,
        }


class BulkAssignSerializer(serializers.Serializer):
    order_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False, max_length=500)
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(is_active=True), allow_null=True)


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=30)


class OutcomeSerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=["confirm", "suivi"], default="confirm")
    outcome = serializers.CharField()
    note = serializers.CharField(required=False, allow_blank=True)
    next_call_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate(self, attrs):
        choices = ConfirmationOutcome.values if attrs["stage"] == "confirm" else SuiviOutcome.values
        if attrs["outcome"] not in choices:
            raise serializers.ValidationError({"outcome": "نتيجة غير صالحة"})
        return attrs


class ImportBatchSerializer(serializers.ModelSerializer):
    row_count = serializers.SerializerMethodField()

    class Meta:
        model = ImportBatch
        fields = ["id", "file_name", "columns", "mapping", "result", "status", "created_count", "row_count", "created_at"]

    def get_row_count(self, obj):
        return len(obj.rows)


class MappingSerializer(serializers.Serializer):
    mapping = serializers.DictField(child=serializers.CharField(), required=False)

    def validate_mapping(self, value):
        unknown = set(value) - set(FIELD_ALIASES)
        if unknown:
            raise serializers.ValidationError("حقول غير معروفة: " + "، ".join(sorted(unknown)))
        columns = self.context["columns"]
        if any(column not in columns for column in value.values()):
            raise serializers.ValidationError("عمود غير موجود في الملف")
        return value

    def validate(self, attrs):
        mapping = attrs.get("mapping", self.context["current"])
        missing = [field for field in REQUIRED_FIELDS if field not in mapping]
        if missing:
            raise serializers.ValidationError({"mapping": "أعمدة مطلوبة: " + "، ".join(missing)})
        return {"mapping": mapping}
