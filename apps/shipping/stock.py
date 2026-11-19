from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone

from apps.catalog.models import Product
from apps.core.excel import normalize_text

from .maystro import ACTIVE_STATUSES
from .models import MaystroConfig, MaystroOrder, MaystroProduct

RUNWAY_WINDOW_DAYS = 30
DEFAULT_THRESHOLD = 50


def live_config():
    config = MaystroConfig.load()
    return config if config.encrypted_token else None


def remote_product(product, config=None):
    if not product.courier_ref or product.stock_source == "local":
        return None
    if config is None:
        config = live_config()
    if config is None or not config.encrypted_token:
        return None
    return MaystroProduct.objects.filter(maystro_id=product.courier_ref, deleted=False).first()


def is_maystro_managed(product):
    return remote_product(product) is not None


def reserved_quantity(product):
    total = product.order_items.filter(order__status="confirmed").aggregate(value=Sum("quantity"))["value"]
    return total or 0


def quantity_in(rows, maystro_id):
    total = 0
    for lines in rows:
        for line in lines or []:
            if isinstance(line, dict) and line.get("product_id") == maystro_id:
                total += int(line.get("quantity") or 0)
    return total


def warehouse_rows(lines):
    rows = []
    for line in lines:
        if not (line.get("quantity") or line.get("available_quantity") or line.get("incoming_quantity")):
            continue
        rows.append(
            {
                "name": line.get("warehouse_name") or str(line.get("warehouse") or ""),
                "quantity": line.get("quantity", 0),
                "available": line.get("available_quantity", 0),
                "defective": line.get("defective_quantity", 0),
                "incoming": line.get("incoming_quantity", 0),
            }
        )
    return sorted(rows, key=lambda row: -row["available"])


def maystro_levels(product, remote):
    lines = [line for line in remote.stock or [] if isinstance(line, dict)]
    remote_available = sum(int(line.get("available_quantity") or 0) for line in lines)
    reserved = reserved_quantity(product)
    available = remote_available - reserved
    active = MaystroOrder.objects.filter(status__in=ACTIVE_STATUSES).values_list("products", flat=True)
    since = timezone.now() - timedelta(days=RUNWAY_WINDOW_DAYS)
    shipped = quantity_in(MaystroOrder.objects.filter(shipped_at__gte=since, is_refund=False).values_list("products", flat=True), remote.maystro_id)
    daily_rate = shipped / RUNWAY_WINDOW_DAYS
    if available <= 0:
        state = "out"
    elif available <= product.low_stock_threshold:
        state = "low"
    else:
        state = "ok"
    return {
        "stock_source": "maystro",
        "on_hand": sum(int(line.get("quantity") or 0) for line in lines),
        "reserved": reserved,
        "at_courier": quantity_in(active, remote.maystro_id),
        "available": available,
        "defective": sum(int(line.get("defective_quantity") or 0) for line in lines),
        "incoming": sum(int(line.get("incoming_quantity") or 0) for line in lines),
        "warehouses": warehouse_rows(lines),
        "stock_synced_at": remote.synced_at,
        "state": state,
        "runway_days": int(max(available, 0) / daily_rate) if daily_rate else None,
    }


def unique_name(base):
    name = (base or "منتج من Maystro").strip()[:140]
    candidate = name
    index = 2
    while Product.objects.filter(name=candidate).exists():
        candidate = f"{name} ({index})"
        index += 1
    return candidate


def adopt_products():
    if live_config() is None:
        return 0
    linked = set(Product.objects.exclude(courier_ref="").values_list("courier_ref", flat=True))
    free = {}
    for product in Product.objects.filter(courier_ref=""):
        for key in (product.name, product.sku):
            if key:
                free.setdefault(normalize_text(key), product)
    created = 0
    for remote in MaystroProduct.objects.filter(deleted=False).exclude(maystro_id__in=linked):
        match = free.get(normalize_text(remote.name)) or (free.get(normalize_text(remote.reference)) if remote.reference else None)
        if match is not None and not match.courier_ref:
            match.courier_ref = remote.maystro_id
            match.save(update_fields=["courier_ref"])
            continue
        sku = remote.reference or None
        if sku and Product.objects.filter(sku=sku).exists():
            sku = None
        Product.objects.create(
            name=unique_name(remote.name),
            sku=sku,
            price=0,
            low_stock_threshold=remote.low_stock_level if remote.low_stock_level is not None else DEFAULT_THRESHOLD,
            courier_ref=remote.maystro_id,
        )
        created += 1
    return created
