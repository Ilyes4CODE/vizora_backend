from rest_framework import serializers

from .models import Campaign, Expense, Settlement
from .services import settlement_totals


class CampaignSerializer(serializers.ModelSerializer):
    platform_label = serializers.CharField(source="get_platform_display", read_only=True)

    class Meta:
        model = Campaign
        fields = ["id", "name", "platform", "platform_label", "product", "is_active", "starts_on", "ends_on", "created_at"]
        read_only_fields = ["created_at"]


class ExpenseSerializer(serializers.ModelSerializer):
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default="")
    amount = serializers.IntegerField(min_value=1)

    class Meta:
        model = Expense
        fields = ["id", "date", "title", "category", "category_label", "amount", "campaign", "created_by", "created_by_name", "created_at"]
        read_only_fields = ["created_by", "created_at"]


class CampaignSpendSerializer(serializers.Serializer):
    date = serializers.DateField()
    amount = serializers.IntegerField(min_value=1)
    title = serializers.CharField(required=False, allow_blank=True, max_length=200)


class SettlementCreateSerializer(serializers.Serializer):
    period_start = serializers.DateField()
    period_end = serializers.DateField()

    def validate(self, attrs):
        if attrs["period_end"] < attrs["period_start"]:
            raise serializers.ValidationError({"period_end": "نهاية الفترة قبل بدايتها"})
        return attrs


class SettlementSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Settlement
        fields = ["id", "courier", "period_start", "period_end", "status", "status_label", "paid_on", "statement_uploaded_at", "created_at"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data.update(settlement_totals(instance))
        return data


class MarkPaidSerializer(serializers.Serializer):
    paid_on = serializers.DateField(required=False)
