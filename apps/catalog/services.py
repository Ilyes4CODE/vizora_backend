from datetime import timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.exceptions import conflict

from .models import Product, StockMovement

RUNWAY_WINDOW_DAYS = 30


@transaction.atomic
def apply_movement(product_id, kind, quantity, user=None, note="", order_reference=""):
    product = Product.objects.select_for_update().get(pk=product_id)
    balance = product.on_hand + quantity
    if balance < 0:
        raise conflict(f"المخزون غير كافٍ للمنتج {product.name}", "insufficient_stock")
    product.on_hand = balance
    product.save(update_fields=["on_hand"])
    return StockMovement.objects.create(
        product=product,
        kind=kind,
        quantity=quantity,
        balance_after=balance,
        note=note,
        order_reference=order_reference,
        created_by=user,
    )


def quantity_in_status(product, status):
    total = product.order_items.filter(order__status=status).aggregate(value=Sum("quantity"))["value"]
    return total or 0


def available_quantity(product):
    return stock_levels(product)["available"]


def stock_levels(product):
    from apps.shipping.stock import maystro_levels, remote_product

    remote = remote_product(product)
    if remote is not None:
        return maystro_levels(product, remote)
    reserved = quantity_in_status(product, "confirmed")
    available = product.on_hand - reserved
    since = timezone.now() - timedelta(days=RUNWAY_WINDOW_DAYS)
    shipped = product.movements.filter(kind=StockMovement.Kind.SHIP, created_at__gte=since).aggregate(value=Sum("quantity"))["value"] or 0
    daily_rate = abs(shipped) / RUNWAY_WINDOW_DAYS
    if available <= 0:
        state = "out"
    elif available <= product.low_stock_threshold:
        state = "low"
    else:
        state = "ok"
    return {
        "stock_source": "local",
        "on_hand": product.on_hand,
        "reserved": reserved,
        "at_courier": quantity_in_status(product, "shipping"),
        "available": available,
        "defective": 0,
        "incoming": 0,
        "warehouses": [],
        "stock_synced_at": None,
        "state": state,
        "runway_days": int(max(available, 0) / daily_rate) if daily_rate else None,
    }
