from django.db import transaction
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.catalog.models import Product
from apps.core import audit

from .models import SheetSource
from .sheets import export_url, preview, read_sheet, sync_source


class SheetSourceSerializer(serializers.ModelSerializer):
    default_product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.filter(is_active=True), required=False, allow_null=True)
    default_product_name = serializers.CharField(source="default_product.name", read_only=True, default="")
    start_row = serializers.IntegerField(min_value=2, max_value=100000, required=False)

    class Meta:
        model = SheetSource
        fields = [
            "id", "name", "url", "start_row", "is_active", "default_product", "default_product_name", "total_includes_delivery",
            "last_synced_at", "last_sync_ok", "last_error", "last_result", "imported_count", "created_at",
        ]
        read_only_fields = ["last_synced_at", "last_sync_ok", "last_error", "last_result", "imported_count", "created_at"]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("اكتب اسماً للشيت")
        return value

    def validate_url(self, value):
        export_url(value)
        return value.strip()


class PreviewSerializer(serializers.Serializer):
    url = serializers.CharField(max_length=600)


class SheetSourceViewSet(viewsets.ModelViewSet):
    section = "orders"
    serializer_class = SheetSourceSerializer
    queryset = SheetSource.objects.select_related("default_product")
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    pagination_class = None
    filter_backends = []

    def answer(self, source, code=status.HTTP_200_OK):
        return Response(SheetSourceSerializer(SheetSource.objects.select_related("default_product").get(pk=source.pk)).data, status=code)

    def create(self, request, *args, **kwargs):
        serializer = SheetSourceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sheet = read_sheet(serializer.validated_data["url"])
        with transaction.atomic():
            source = serializer.save(created_by=request.user)
        sync_source(source, sheet)
        audit.record("sheet_linked", f"ربط Google Sheet «{source.name}»", request=request)
        return self.answer(source, status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        source = self.get_object()
        serializer = SheetSourceSerializer(source, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        changed = {key for key, value in serializer.validated_data.items() if getattr(source, key) != value}
        sheet = read_sheet(serializer.validated_data["url"]) if "url" in changed else None
        source = serializer.save()
        if source.is_active and changed & {"url", "start_row", "default_product", "total_includes_delivery", "is_active"}:
            sync_source(source, sheet)
        audit.record("sheet_updated", f"عدّل Google Sheet «{source.name}»", request=request)
        return self.answer(source)

    def destroy(self, request, *args, **kwargs):
        source = self.get_object()
        audit.record("sheet_removed", f"ألغى ربط Google Sheet «{source.name}»", request=request)
        source.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def sync(self, request, pk=None):
        source = self.get_object()
        result = sync_source(source)
        audit.record("sheet_synced", f"زامن Google Sheet «{source.name}» (+{result['created']})", request=request)
        return self.answer(source)

    @action(detail=False, methods=["post"], serializer_class=PreviewSerializer)
    def preview(self, request):
        serializer = PreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(preview(serializer.validated_data["url"]))
