from django.db import IntegrityError, transaction
from django.http import FileResponse
from django.utils import timezone
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from . import audit
from .backups import backup_path, create_backup, delete_backup, restore_backup
from .exceptions import ApiError, conflict
from .models import AuditLog, Backup, BusinessProfile, SecuritySettings, Wilaya
from .serializers import (
    AuditLogSerializer,
    BackupSerializer,
    BusinessProfileSerializer,
    CommuneSerializer,
    RestoreRequestSerializer,
    SecuritySettingsSerializer,
    WilayaSerializer,
)


class HealthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"status": "ok", "service": "vizora-global", "time": timezone.now()})


class SingletonView(generics.RetrieveUpdateAPIView):
    http_method_names = ["get", "put", "head", "options"]
    pagination_class = None
    model = None
    audit_action = ""
    audit_message = ""

    def get_object(self):
        instance = self.model.load()
        self.check_object_permissions(self.request, instance)
        return instance

    def perform_update(self, serializer):
        instance = serializer.save()
        audit.record(self.audit_action, self.audit_message, request=self.request, target=instance)


class BusinessProfileView(SingletonView):
    section = "security"
    read_sections = ()
    model = BusinessProfile
    serializer_class = BusinessProfileSerializer
    audit_action = "business_profile_updated"
    audit_message = "عدّل معلومات النشاط"


class SecuritySettingsView(SingletonView):
    section = "security"
    model = SecuritySettings
    serializer_class = SecuritySettingsSerializer
    audit_action = "security_settings_updated"
    audit_message = "عدّل إعدادات الحماية"

    def perform_update(self, serializer):
        turning_on = serializer.validated_data.get("two_factor_required") and not serializer.instance.two_factor_required
        if turning_on and not self.request.user.totp_enabled:
            raise conflict("فعّل التحقق بخطوتين لحسابك أولاً من نافذة حسابي", "enroll_two_factor_first")
        super().perform_update(serializer)


class AuditLogView(generics.ListAPIView):
    section = "security"
    queryset = AuditLog.objects.all()
    serializer_class = AuditLogSerializer
    filterset_fields = ["action", "user"]
    search_fields = ["message", "user_name"]


class WilayaViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    section = "shipping"
    read_sections = ()
    http_method_names = ["get", "post", "patch", "head", "options"]
    queryset = Wilaya.objects.all()
    serializer_class = WilayaSerializer
    pagination_class = None
    filter_backends = []

    def perform_update(self, serializer):
        wilaya = serializer.save()
        audit.record("wilaya_updated", f"عدّل أسعار توصيل ولاية {wilaya.name_ar}", request=self.request, target=wilaya)

    @action(detail=True, methods=["get", "post"], serializer_class=CommuneSerializer)
    def communes(self, request, pk=None):
        wilaya = self.get_object()
        if request.method == "GET":
            return Response(CommuneSerializer(wilaya.communes.all(), many=True).data)
        serializer = CommuneSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                serializer.save(wilaya=wilaya)
        except IntegrityError as error:
            raise conflict("البلدية موجودة مسبقاً", "commune_exists") from error
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class BackupViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    section = "security"
    queryset = Backup.objects.all()
    serializer_class = BackupSerializer
    pagination_class = None
    filter_backends = []

    def create(self, request):
        backup = create_backup(Backup.Kind.MANUAL, user=request.user)
        audit.record("backup_created", "أنشأ نسخة احتياطية", request=request, target=backup)
        return Response(BackupSerializer(backup).data, status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        audit.record("backup_deleted", f"حذف النسخة {instance.file_name}", request=self.request)
        delete_backup(instance)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        backup = self.get_object()
        path = backup_path(backup)
        if not path.exists():
            raise ApiError("ملف النسخة غير موجود", "backup_missing", status.HTTP_404_NOT_FOUND)
        audit.record("backup_downloaded", f"حمّل النسخة {backup.file_name}", request=request, target=backup)
        return FileResponse(path.open("rb"), as_attachment=True, filename=backup.file_name)

    @action(detail=True, methods=["post"], serializer_class=RestoreRequestSerializer)
    def restore(self, request, pk=None):
        serializer = RestoreRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        backup = self.get_object()
        safety_copy = restore_backup(backup, user=None)
        audit.record("backup_restored", f"استرجع النسخة {backup.file_name}", request=None, target=backup)
        return Response({"restored": backup.file_name, "safety_backup": BackupSerializer(safety_copy).data})
