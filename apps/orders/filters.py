from django_filters import rest_framework as filters

from apps.core.dates import month_bounds, parse_month

from .models import Order


class OrderFilter(filters.FilterSet):
    status = filters.CharFilter(field_name="status")
    wilaya = filters.NumberFilter(field_name="wilaya_id")
    product = filters.NumberFilter(field_name="items__product_id", distinct=True)
    assigned_to = filters.NumberFilter(field_name="assigned_to_id")
    campaign = filters.NumberFilter(field_name="campaign_id")
    created_from = filters.DateFilter(field_name="created_at__date", lookup_expr="gte")
    created_to = filters.DateFilter(field_name="created_at__date", lookup_expr="lte")
    month = filters.CharFilter(method="filter_month")

    class Meta:
        model = Order
        fields = []

    def filter_month(self, queryset, name, value):
        start, end = month_bounds(parse_month(value))
        return queryset.filter(created_at__gte=start, created_at__lt=end)
