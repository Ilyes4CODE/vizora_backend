from django.db import transaction
from rest_framework import serializers

from .models import Product, StockMovement, StockSource
from .services import stock_levels


class ProductSerializer(serializers.ModelSerializer):
    name = serializers.CharField(max_length=150)
    sku = serializers.CharField(max_length=60, required=False, allow_blank=True, allow_null=True)
    courier_ref = serializers.CharField(max_length=60, required=False, allow_blank=True)
    price = serializers.IntegerField(min_value=0, max_value=100000000)
    cost = serializers.IntegerField(min_value=0, max_value=100000000, required=False)
    low_stock_threshold = serializers.IntegerField(min_value=0, max_value=1000000, required=False)
    stock_mode = serializers.ChoiceField(source="stock_source", choices=StockSource.choices, required=False)
    create_in_maystro = serializers.BooleanField(write_only=True, required=False, default=False)
    add_to_store = serializers.BooleanField(write_only=True, required=False, default=False)
    initial_stock = serializers.IntegerField(write_only=True, required=False, min_value=0, max_value=1000000)
    title_en = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=150)
    in_store = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id", "name", "sku", "price", "cost", "low_stock_threshold", "courier_ref", "stock_mode", "image", "is_active", "created_at",
            "create_in_maystro", "add_to_store", "initial_stock", "title_en", "in_store",
        ]
        read_only_fields = ["created_at"]

    def get_in_store(self, obj):
        store_product = getattr(obj, "store", None)
        return {"id": store_product.pk, "slug": store_product.slug, "is_published": store_product.is_published} if store_product else None

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
        if self.instance is not None:
            for key in ("create_in_maystro", "add_to_store", "initial_stock", "title_en"):
                attrs.pop(key, None)
        elif attrs.get("add_to_store") and attrs.get("price", 0) < 1:
            raise serializers.ValidationError({"price": "اكتب سعر البيع لنشر المنتج في الموقع"})
        return attrs

    def create(self, validated_data):
        from apps.shipping.products import register_in_maystro
        from apps.storefront.catalog import forget_stock
        from apps.storefront.products import add_to_store

        in_maystro = validated_data.pop("create_in_maystro", False)
        in_store = validated_data.pop("add_to_store", False)
        validated_data.pop("initial_stock", None)
        title_en = validated_data.pop("title_en", "")
        validated_data["stock_source"] = StockSource.MAYSTRO
        if in_maystro and not validated_data.get("courier_ref"):
            validated_data["courier_ref"], _ = register_in_maystro(
                validated_data["name"], reference=validated_data.get("sku"), low_stock_level=validated_data.get("low_stock_threshold")
            )
        with transaction.atomic():
            product = super().create(validated_data)
            if in_store:
                add_to_store(product, title_en)
        forget_stock(product.pk)
        return product

    def update(self, instance, validated_data):
        from apps.storefront.catalog import forget_stock
        from apps.storefront.products import add_to_store

        priced_now = instance.price < 1 and validated_data.get("price", instance.price) >= 1
        product = super().update(instance, validated_data)
        if priced_now and product.is_active and product.courier_ref and getattr(product, "store", None) is None:
            add_to_store(product)
        forget_stock(product.pk)
        return product

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
