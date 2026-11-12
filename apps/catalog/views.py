from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core import audit
from apps.core.exceptions import conflict
from apps.shipping.live import refresh_stock
from apps.shipping.models import MaystroConfig
from apps.shipping.stock import is_maystro_managed

from .models import Product, StockMovement
from .serializers import ProductSerializer, StockAdjustSerializer, StockInSerializer, StockMovementSerializer
from .services import apply_movement


class ProductViewSet(viewsets.ModelViewSet):
    section = "products"
    read_sections = ("products", "orders", "campaigns", "accounting", "reports")
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    pagination_class = None
    filterset_fields = ["is_active"]
    search_fields = ["name", "sku"]

    def list(self, request, *args, **kwargs):
        refresh_stock(background=True)
        return super().list(request, *args, **kwargs)

    @action(detail=False, methods=["post"], url_path="sync-maystro")
    def sync_maystro(self, request):
        if not refresh_stock(force=True):
            raise conflict("أدخل مفتاح Maystro أولاً في «الحماية والنسخ» ← «ربط Maystro»", "maystro_not_configured")
        audit.record("maystro_stock_synced", "حدّث المخزون من Maystro", request=request)
        products = self.filter_queryset(self.get_queryset())
        return Response({"synced_at": MaystroConfig.load().live_stock_at, "products": self.get_serializer(products, many=True).data})

    def perform_create(self, serializer):
        product = serializer.save()
        audit.record("product_created", f"أضاف المنتج {product.name}", request=self.request, target=product)

    def perform_update(self, serializer):
        product = serializer.save()
        audit.record("product_updated", f"عدّل المنتج {product.name}", request=self.request, target=product)

    def destroy(self, request, *args, **kwargs):
        product = self.get_object()
        audit.record("product_removed", f"أزال المنتج {product.name}", request=request, target=product)
        if product.order_items.exists():
            product.is_active = False
            product.save(update_fields=["is_active"])
        else:
            product.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def move(self, request, serializer_class, kind):
        product = self.get_object()
        if is_maystro_managed(product):
            raise conflict("مخزون هذا المنتج يُقرأ من Maystro، أدخل السلعة إلى مخزن Maystro وسيظهر هنا تلقائياً", "stock_from_maystro")
        serializer = serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        movement = apply_movement(product.pk, kind, data["quantity"], user=request.user, note=data.get("note", ""))
        audit.record("stock_changed", f"{movement.get_kind_display()}: {product.name} ({data['quantity']})", request=request, target=product)
        return Response(StockMovementSerializer(movement).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="stock-in", serializer_class=StockInSerializer)
    def stock_in(self, request, pk=None):
        return self.move(request, StockInSerializer, StockMovement.Kind.IN)

    @action(detail=True, methods=["post"], url_path="adjust", serializer_class=StockAdjustSerializer)
    def adjust(self, request, pk=None):
        return self.move(request, StockAdjustSerializer, StockMovement.Kind.ADJUST)


class StockMovementViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    section = "products"
    queryset = StockMovement.objects.select_related("product", "created_by")
    serializer_class = StockMovementSerializer
    filterset_fields = ["product", "kind"]
