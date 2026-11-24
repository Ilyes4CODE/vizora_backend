from . import audit
from rest_framework import serializers

from .models import AuditLog, Backup, BusinessProfile, Commune, SecuritySettings, Wilaya

DAYS = {"sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"}


class WilayaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wilaya
        fields = ["code", "name_ar", "name_fr", "home_fee", "desk_fee", "home_available", "desk_available", "courier_ref"]
        read_only_fields = ["code"]


class CommuneSerializer(serializers.ModelSerializer):
    class Meta:
        model = Commune
        fields = ["id", "wilaya", "name_ar", "name_fr", "courier_ref"]
        read_only_fields = ["wilaya"]


class BusinessProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessProfile
        fields = ["name", "phone", "email", "address", "facebook", "instagram", "activity", "working_hours"]

    def validate_working_hours(self, value):
        if not isinstance(value, dict) or set(value) - DAYS:
            raise serializers.ValidationError("أيام غير صالحة")
        for periods in value.values():
            if not isinstance(periods, list) or any(not isinstance(p, list) or len(p) != 2 for p in periods):
                raise serializers.ValidationError("الفترات يجب أن تكون [بداية، نهاية]")
        return value


class SecuritySettingsSerializer(serializers.ModelSerializer):
    auto_logout_minutes = serializers.IntegerField(min_value=5, max_value=720)

    class Meta:
        model = SecuritySettings
        fields = ["two_factor_required", "auto_logout_enabled", "auto_logout_minutes", "approved_devices_only"]


class AuditLogSerializer(serializers.ModelSerializer):
    actor = serializers.SerializerMethodField()
    action_label = serializers.SerializerMethodField()
    group = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = ["id", "user", "user_name", "actor", "action", "action_label", "group", "message", "target_type", "target_id", "ip_address", "created_at"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["message"] = audit.readable(data["message"])
        return data

    def get_actor(self, obj):
        return obj.user_name or "النظام"

    def get_action_label(self, obj):
        return audit.label_of(obj.action)

    def get_group(self, obj):
        return audit.group_of(obj.action)


class BackupSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = Backup
        fields = ["id", "file_name", "size_bytes", "kind", "kind_label", "created_by_name", "created_at"]
        read_only_fields = fields


class RestoreRequestSerializer(serializers.Serializer):
    confirm = serializers.ChoiceField(choices=["RESTORE"])
