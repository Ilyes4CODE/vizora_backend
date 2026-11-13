from django.utils import timezone
from django.utils.text import slugify
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import audit
from apps.orders.models import Order, OrderStatus, Source

from . import conversions
from .catalog import forget_stock
from .models import ContactMessage, Review, StockAlert, StoreImage, StoreOffer, StorePage, StoreProduct, StoreSettings
from .serializers import (
    ContactMessageSerializer,
    ImageUploadSerializer,
    ReviewSerializer,
    StockAlertSerializer,
    StoreImageSerializer,
    StoreOfferSerializer,
    StorePageSerializer,
    StoreProductSerializer,
    StoreSettingsSerializer,
    optimized_image,
    public_base,
)

NO_PUT = ["get", "post", "patch", "delete", "head", "options"]


class StoreSummaryView(APIView):
    section = "products"
    read_sections = ("products", "orders", "confirmation")

    def get(self, request):
        now = timezone.localtime()
        start_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_month = start_day.replace(day=1)
        web = Order.objects.filter(source=Source.WEBSITE)
        settings = StoreSettings.load()
        return Response(
            {
                "store_url": public_base(),
                "is_open": settings.is_open,
                "orders_today": web.filter(created_at__gte=start_day).count(),
                "orders_month": web.filter(created_at__gte=start_month).count(),
                "orders_pending": web.filter(status=OrderStatus.PENDING).count(),
                "orders_delivered_month": web.filter(status=OrderStatus.DELIVERED, created_at__gte=start_month).count(),
                "messages_open": ContactMessage.objects.filter(handled=False).count(),
                "alerts_open": StockAlert.objects.filter(notified=False).count(),
                "products_published": StoreProduct.objects.filter(is_published=True).count(),
            }
        )


class StoreSettingsView(APIView):
    section = "products"

    def get(self, request):
        return Response(StoreSettingsSerializer(StoreSettings.load()).data)

    def patch(self, request):
        serializer = StoreSettingsSerializer(StoreSettings.load(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        audit.record("store_settings_updated", "عدّل إعدادات الموقع", request=request)
        return Response(serializer.data)


class ConversionsView(APIView):
    section = "products"

    def get(self, request):
        return Response(conversions.report())


class ConversionsTestView(APIView):
    section = "products"

    def post(self, request):
        platform = request.data.get("platform")
        if platform not in conversions.SERVER_PLATFORMS:
            return Response({"detail": "اختر Facebook أو TikTok"}, status=400)
        store = StoreSettings.load()
        label = conversions.LABELS[platform]
        if not conversions.server_ready(store, platform):
            return Response({"detail": f"أضف معرّف البيكسل ورمز الخادم لـ {label} واحفظ أولاً"}, status=400)
        if not conversions.test_code_for(store, platform):
            return Response({"detail": f"أضف رمز الاختبار من {label} واحفظ أولاً"}, status=400)
        ok, detail = conversions.send_test(platform, request)
        audit.record("pixel_test_sent", f"أرسل حدثاً تجريبياً إلى {label}", request=request)
        return Response({"ok": ok, "detail": detail})


class ConversionsRetryView(APIView):
    section = "products"

    def post(self, request):
        result = conversions.retry_failed()
        audit.record("pixel_events_retried", f"أعاد إرسال {result['sent'] + result['failed']} حدثاً", request=request)
        return Response(result)


class StoreProductViewSet(viewsets.ModelViewSet):
    section = "products"
    serializer_class = StoreProductSerializer
    pagination_class = None
    http_method_names = NO_PUT
    queryset = StoreProduct.objects.select_related("product").prefetch_related("offers", "images")

    def perform_create(self, serializer):
        item = serializer.save()
        audit.record("store_product_created", f"أضاف {item.title_ar} إلى الموقع", request=self.request, target=item)

    def perform_update(self, serializer):
        item = serializer.save()
        forget_stock(item.product_id)
        audit.record("store_product_updated", f"عدّل {item.title_ar} في الموقع", request=self.request, target=item)

    def perform_destroy(self, instance):
        audit.record("store_product_removed", f"أزال {instance.title_ar} من الموقع", request=self.request, target=instance)
        instance.delete()

    @action(detail=True, methods=["post"], url_path="images")
    def upload_image(self, request, pk=None):
        item = self.get_object()
        serializer = ImageUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        stem = f"{slugify(item.slug) or 'product'}-{timezone.now().strftime('%Y%m%d%H%M%S%f')}"
        content, (width, height) = optimized_image(serializer.validated_data["image"], stem)
        image = StoreImage(
            store_product=item,
            width=width,
            height=height,
            alt_ar=serializer.validated_data.get("alt_ar", ""),
            alt_en=serializer.validated_data.get("alt_en", ""),
            sort_order=item.images.count(),
        )
        image.image.save(content.name, content, save=True)
        return Response(StoreImageSerializer(image, context={"request": request}).data, status=status.HTTP_201_CREATED)


class StoreImageViewSet(mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    section = "products"
    serializer_class = StoreImageSerializer
    http_method_names = NO_PUT
    queryset = StoreImage.objects.all()

    def perform_destroy(self, instance):
        instance.image.delete(save=False)
        instance.delete()


class StoreOfferViewSet(mixins.CreateModelMixin, mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    section = "products"
    serializer_class = StoreOfferSerializer
    http_method_names = NO_PUT
    queryset = StoreOffer.objects.all()


class StorePageViewSet(mixins.ListModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    section = "products"
    serializer_class = StorePageSerializer
    pagination_class = None
    http_method_names = NO_PUT
    queryset = StorePage.objects.all()


class ReviewViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    section = "products"
    serializer_class = ReviewSerializer
    pagination_class = None
    http_method_names = NO_PUT
    queryset = Review.objects.all()


class ContactMessageViewSet(mixins.ListModelMixin, mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    section = "products"
    read_sections = ("products", "orders", "confirmation")
    action_sections = {"partial_update": ("products", "orders", "confirmation")}
    serializer_class = ContactMessageSerializer
    http_method_names = NO_PUT
    queryset = ContactMessage.objects.all()

    def perform_update(self, serializer):
        handled = serializer.validated_data.get("handled")
        serializer.save(handled_at=timezone.now() if handled else None)


class StockAlertViewSet(mixins.ListModelMixin, mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    section = "products"
    serializer_class = StockAlertSerializer
    http_method_names = NO_PUT
    queryset = StockAlert.objects.select_related("store_product")
