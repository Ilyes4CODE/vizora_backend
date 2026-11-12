from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.exceptions import ApiError, conflict

from .authentication import ensure_session_active, ensure_token_current
from .models import Device, Role, RolePermission, Section, User


def seats_used():
    return User.objects.filter(is_active=True).count()


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, trim_whitespace=False)
    role_label = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = [
            "id", "username", "full_name", "phone", "role", "role_label", "is_active",
            "last_activity_at", "totp_enabled", "date_joined", "password",
        ]
        read_only_fields = ["last_activity_at", "totp_enabled", "date_joined"]

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate(self, attrs):
        instance = self.instance
        if instance is None and not attrs.get("password"):
            raise serializers.ValidationError({"password": "كلمة المرور مطلوبة"})
        activating = attrs.get("is_active", True) if instance is None else (not instance.is_active and attrs.get("is_active") is True)
        if activating and seats_used() >= settings.MAX_STAFF_ACCOUNTS:
            raise conflict(f"الحد الأقصى هو {settings.MAX_STAFF_ACCOUNTS} حسابات", "seat_limit_reached")
        if instance is not None and instance.is_admin_role:
            removing_admin = attrs.get("role", instance.role) != Role.ADMIN or attrs.get("is_active", True) is False
            if removing_admin:
                ensure_another_admin(instance)
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user

    def update(self, instance, validated_data):
        validated_data.pop("password", None)
        return super().update(instance, validated_data)


def ensure_another_admin(user):
    if not User.objects.filter(role=Role.ADMIN, is_active=True).exclude(pk=user.pk).exists():
        raise conflict("يجب أن يبقى مدير نظام واحد على الأقل", "last_admin")


class MeSerializer(serializers.ModelSerializer):
    role_label = serializers.CharField(source="get_role_display", read_only=True)
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "full_name", "phone", "role", "role_label", "totp_enabled", "permissions"]

    def get_permissions(self, obj):
        return obj.allowed_sections()


class DirectorySerializer(serializers.ModelSerializer):
    role_label = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = ["id", "full_name", "role", "role_label"]


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False)
    otp = serializers.CharField(required=False, allow_blank=True)
    device_id = serializers.CharField(required=False, allow_blank=True, max_length=120)
    device_name = serializers.CharField(required=False, allow_blank=True, max_length=120)


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(trim_whitespace=False)
    new_password = serializers.CharField(trim_whitespace=False)

    def validate_new_password(self, value):
        validate_password(value, user=self.context["request"].user)
        return value


class FirstAdminSerializer(serializers.Serializer):
    username = serializers.RegexField(r"^[A-Za-z0-9_.-]{3,30}$", error_messages={"invalid": "اسم المستخدم من 3 إلى 30 حرفاً لاتينياً أو أرقام"})
    full_name = serializers.CharField(max_length=120)
    password = serializers.CharField(trim_whitespace=False)

    def validate(self, attrs):
        validate_password(attrs["password"], user=User(username=attrs["username"], full_name=attrs["full_name"]))
        return attrs


class ResetPasswordSerializer(serializers.Serializer):
    new_password = serializers.CharField(trim_whitespace=False)

    def validate_new_password(self, value):
        validate_password(value)
        return value


class OtpSerializer(serializers.Serializer):
    otp = serializers.CharField()


class DisableTwoFactorSerializer(serializers.Serializer):
    password = serializers.CharField(trim_whitespace=False)


class SessionRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        token = RefreshToken(attrs["refresh"])
        user = User.objects.filter(pk=token.payload.get("user_id"), is_active=True).first()
        if user is None:
            raise ApiError("الحساب غير نشط", "account_inactive", 401)
        ensure_token_current(user, token)
        ensure_session_active(user)
        return super().validate(attrs)


class PermissionUpdateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=[r for r in Role.values if r != Role.ADMIN])
    section = serializers.ChoiceField(choices=Section.values)
    allowed = serializers.BooleanField()

    def save(self):
        entry, _ = RolePermission.objects.update_or_create(
            role=self.validated_data["role"],
            section=self.validated_data["section"],
            defaults={"allowed": self.validated_data["allowed"]},
        )
        return entry


class DeviceSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.full_name", read_only=True)

    class Meta:
        model = Device
        fields = ["id", "user", "user_name", "device_id", "name", "approved", "last_seen_at", "created_at"]
        read_only_fields = fields
