import pyotp
from django.conf import settings
from django.contrib.auth import authenticate
from django.db import transaction
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from apps.core import audit
from apps.core.exceptions import ApiError, conflict
from apps.core.models import SecuritySettings
from apps.orders.services import redistribute_pending

from .models import Device, Role, RolePermission, Section, User
from .serializers import (
    ChangePasswordSerializer,
    DeviceSerializer,
    DirectorySerializer,
    DisableTwoFactorSerializer,
    FirstAdminSerializer,
    LoginSerializer,
    LogoutSerializer,
    MeSerializer,
    OtpSerializer,
    PermissionUpdateSerializer,
    ResetPasswordSerializer,
    SessionRefreshSerializer,
    UserSerializer,
    ensure_another_admin,
    seats_used,
)
from .services import end_all_sessions, issue_tokens, verify_otp


class FirstAdminView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def get_throttles(self):
        return super().get_throttles() if self.request.method == "POST" else []

    def get(self, request):
        return Response({"needed": not User.objects.exists()})

    def post(self, request):
        serializer = FirstAdminSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            if User.objects.exists():
                raise conflict("تم إنشاء حساب المدير من قبل، سجّل الدخول به", "setup_done")
            user = User.objects.create_superuser(username=data["username"], password=data["password"], full_name=data["full_name"].strip())
        audit.record("first_admin_created", f"أنشأ حساب المدير الأول ({user.username})", user=user)
        return Response({"username": user.username, "full_name": user.full_name}, status=status.HTTP_201_CREATED)


class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"
    serializer_class = LoginSerializer

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = authenticate(request, username=data["username"], password=data["password"])
        if user is None:
            audit.record("login_failed", f"محاولة دخول فاشلة: {data['username']}", request=request)
            raise ApiError("اسم المستخدم أو كلمة المرور غير صحيحة", "invalid_credentials", status.HTTP_401_UNAUTHORIZED)
        self.check_two_factor(user, data.get("otp"))
        self.check_device(user, data)
        user.last_activity_at = timezone.now()
        user.last_login = user.last_activity_at
        user.save(update_fields=["last_activity_at", "last_login"])
        refresh = issue_tokens(user)
        audit.record("login", "سجّل الدخول", request=request, user=user, target=user)
        return Response({"access": str(refresh.access_token), "refresh": str(refresh), "user": MeSerializer(user).data})

    def check_two_factor(self, user, otp):
        if not user.totp_enabled:
            return
        if not otp:
            raise ApiError("أدخل رمز التحقق", "otp_required", status.HTTP_403_FORBIDDEN)
        if not verify_otp(user, otp):
            raise ApiError("رمز التحقق غير صحيح", "otp_invalid", status.HTTP_401_UNAUTHORIZED)

    def check_device(self, user, data):
        device_id = data.get("device_id")
        restricted = SecuritySettings.load().approved_devices_only and user.role != Role.ADMIN
        if not device_id:
            if restricted:
                raise ApiError("معرّف الجهاز مطلوب", "device_required", status.HTTP_400_BAD_REQUEST)
            return
        device, created = Device.objects.get_or_create(
            user=user,
            device_id=device_id,
            defaults={"name": data.get("device_name", ""), "approved": not restricted},
        )
        if not created:
            device.save(update_fields=["last_seen_at"])
        if restricted and not device.approved:
            raise ApiError("هذا الجهاز بانتظار موافقة المدير", "device_pending", status.HTTP_403_FORBIDDEN)


class SessionRefreshView(TokenRefreshView):
    serializer_class = SessionRefreshSerializer


class AccountView(APIView):
    permission_classes = [IsAuthenticated]


class LogoutView(AccountView):
    serializer_class = LogoutSerializer

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            RefreshToken(serializer.validated_data["refresh"]).blacklist()
        except TokenError as error:
            raise ApiError("رمز الجلسة غير صالح", "token_not_valid") from error
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(AccountView):
    serializer_class = MeSerializer

    def get(self, request):
        return Response(MeSerializer(request.user).data)


class ChangePasswordView(AccountView):
    serializer_class = ChangePasswordSerializer

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        if not request.user.check_password(serializer.validated_data["current_password"]):
            raise ApiError("كلمة المرور الحالية غير صحيحة", "wrong_password")
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password"])
        end_all_sessions(request.user)
        audit.record("password_changed", "غيّر كلمة المرور", request=request, target=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TwoFactorSetupView(AccountView):
    def post(self, request):
        user = request.user
        if user.totp_enabled:
            raise conflict("التحقق بخطوتين مفعل مسبقاً", "two_factor_already_enabled")
        secret = pyotp.random_base32()
        user.set_totp_secret(secret)
        user.save(update_fields=["totp_secret"])
        uri = pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name="Vizora Global")
        return Response({"secret": secret, "otpauth_uri": uri})


class TwoFactorEnableView(AccountView):
    serializer_class = OtpSerializer

    def post(self, request):
        serializer = OtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not verify_otp(request.user, serializer.validated_data["otp"]):
            raise ApiError("رمز التحقق غير صحيح", "otp_invalid")
        request.user.totp_enabled = True
        request.user.save(update_fields=["totp_enabled"])
        audit.record("two_factor_enabled", "فعّل التحقق بخطوتين", request=request, target=request.user)
        return Response({"totp_enabled": True})


class TwoFactorDisableView(AccountView):
    serializer_class = DisableTwoFactorSerializer

    def post(self, request):
        serializer = DisableTwoFactorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not request.user.check_password(serializer.validated_data["password"]):
            raise ApiError("كلمة المرور غير صحيحة", "wrong_password")
        request.user.totp_enabled = False
        request.user.totp_secret = ""
        request.user.save(update_fields=["totp_enabled", "totp_secret"])
        audit.record("two_factor_disabled", "عطّل التحقق بخطوتين", request=request, target=request.user)
        return Response({"totp_enabled": False})


class DirectoryView(APIView):
    serializer_class = DirectorySerializer

    def get(self, request):
        users = User.objects.filter(is_active=True).order_by("full_name")
        return Response(DirectorySerializer(users, many=True).data)


class PermissionMatrixView(APIView):
    section = "team"
    serializer_class = PermissionUpdateSerializer

    def matrix(self):
        stored = {(p.role, p.section): p.allowed for p in RolePermission.objects.all()}
        return {
            "roles": [{"value": value, "label": label} for value, label in Role.choices],
            "sections": [
                {
                    "value": value,
                    "label": label,
                    "roles": {
                        role: role == Role.ADMIN or stored.get((role, value), False)
                        for role in Role.values
                    },
                }
                for value, label in Section.choices
            ],
        }

    def get(self, request):
        return Response(self.matrix())

    def put(self, request):
        serializer = PermissionUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = serializer.save()
        audit.record(
            "permission_updated",
            f"عدّل صلاحية {entry.get_role_display()} على {entry.get_section_display()}",
            request=request,
            target=entry,
        )
        return Response(self.matrix())


class UserViewSet(viewsets.ModelViewSet):
    section = "team"
    queryset = User.objects.order_by("id")
    serializer_class = UserSerializer
    pagination_class = None
    filter_backends = []

    def perform_create(self, serializer):
        user = serializer.save()
        audit.record("user_created", f"أضاف الموظف {user.full_name}", request=self.request, target=user)

    def perform_update(self, serializer):
        user = serializer.save()
        if not user.is_active:
            redistribute_pending(user)
        audit.record("user_updated", f"عدّل حساب {user.full_name}", request=self.request, target=user)

    def destroy(self, request, *args, **kwargs):
        user = self.get_object()
        if user.pk == request.user.pk:
            raise conflict("لا يمكنك تعطيل حسابك", "cannot_disable_self")
        if user.is_admin_role:
            ensure_another_admin(user)
        user.is_active = False
        user.save(update_fields=["is_active"])
        end_all_sessions(user)
        redistribute_pending(user)
        audit.record("user_disabled", f"عطّل حساب {user.full_name}", request=request, target=user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["get"])
    def seats(self, request):
        used = seats_used()
        return Response({"used": used, "total": settings.MAX_STAFF_ACCOUNTS, "free": max(settings.MAX_STAFF_ACCOUNTS - used, 0)})

    @action(detail=True, methods=["post"], url_path="reset-password", serializer_class=ResetPasswordSerializer)
    def reset_password(self, request, pk=None):
        user = self.get_object()
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])
        end_all_sessions(user)
        audit.record("password_reset", f"أعاد تعيين كلمة مرور {user.full_name}", request=request, target=user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="reset-two-factor")
    def reset_two_factor(self, request, pk=None):
        user = self.get_object()
        user.totp_enabled = False
        user.totp_secret = ""
        user.save(update_fields=["totp_enabled", "totp_secret"])
        audit.record("two_factor_reset", f"أعاد تعيين التحقق بخطوتين لـ {user.full_name}", request=request, target=user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class DeviceViewSet(mixins.ListModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    section = "security"
    queryset = Device.objects.select_related("user")
    serializer_class = DeviceSerializer
    pagination_class = None
    filterset_fields = ["approved", "user"]

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        device = self.get_object()
        device.approved = True
        device.save(update_fields=["approved", "last_seen_at"])
        audit.record("device_approved", f"وافق على جهاز {device.user.full_name}", request=request, target=device)
        return Response(DeviceSerializer(device).data)
