from rest_framework import serializers

from .models import Product, StockMovement
from .services import stock_levels


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ["id", "name", "sku", "price", "cost", "low_stock_threshold", "courier_ref", "image", "is_active", "created_at"]
        read_only_fields = ["created_at"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if request and (request.user.has_section("products") or request.user.has_section("accounting")):
            data["margin"] = instance.price - instance.cost
        else:
            data.pop("cost", None)
        data.update(stock_levels(instance))
        return data


class StockMovementSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default="النظام")

    class Meta:
        model = StockMovement
        fields = [
            "id", "product", "product_name", "kind", "kind_label", "quantity", "balance_after",
            "note", "order_reference", "created_by", "created_by_name", "created_at",
        ]


class StockInSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1, max_value=1000000)
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


class StockAdjustSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=-1000000, max_value=1000000)
    note = serializers.CharField(max_length=255)

    def validate_quantity(self, value):
        if value == 0:
            raise serializers.ValidationError("الكمية يجب ألا تساوي صفراً")
        return value
