from rest_framework import serializers

from .models import Product, StockMovement
from .services import stock_levels


class ProductSerializer(serializers.ModelSerializer):
    name = serializers.CharField(max_length=150)
    sku = serializers.CharField(max_length=60, required=False, allow_blank=True, allow_null=True)
    courier_ref = serializers.CharField(max_length=60, required=False, allow_blank=True)
    price = serializers.IntegerField(min_value=0, max_value=100000000)
    cost = serializers.IntegerField(min_value=0, max_value=100000000, required=False)
    low_stock_threshold = serializers.IntegerField(min_value=0, max_value=1000000, required=False)

    class Meta:
        model = Product
        fields = ["id", "name", "sku", "price", "cost", "low_stock_threshold", "courier_ref", "image", "is_active", "created_at"]
        read_only_fields = ["created_at"]

    def others(self):
        queryset = Product.objects.all()
        return queryset.exclude(pk=self.instance.pk) if self.instance is not None else queryset

    def validate_name(self, value):
        value = " ".join(value.split())
        if len(value) < 2:
            raise serializers.ValidationError("اكتب اسم المنتج")
        if self.others().filter(name__iexact=value).exists():
            raise serializers.ValidationError("يوجد منتج بنفس الاسم، اختر اسماً آخر")
        return value

    def validate_sku(self, value):
        value = (value or "").strip()
        if not value:
            return None
        if self.others().filter(sku__iexact=value).exists():
            raise serializers.ValidationError("هذا الرمز مستعمل لمنتج آخر")
        return value

    def validate_courier_ref(self, value):
        value = (value or "").strip()
        if value and self.others().filter(courier_ref=value).exists():
            raise serializers.ValidationError("هذا المعرّف مربوط بمنتج آخر")
        return value

    def validate(self, attrs):
        if self.instance is None and "is_active" not in getattr(self, "initial_data", {}):
            attrs["is_active"] = True
        return attrs

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
