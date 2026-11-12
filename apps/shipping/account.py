import hashlib
import threading
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import time as datetime_time
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import connection, transaction
from django.db.models import BigIntegerField, Count, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.core.dates import local_datetime, parse_day
from apps.core.exceptions import ApiError, conflict

from .maystro import ACTIVE_STATUSES, STATUS_LABELS, MaystroError, get_client
from .models import MaystroConfig, MaystroOrder, MaystroPayment, MaystroProduct, Shipment
from .services import parse_moment

MIRROR_RECENT_DAYS = 45
SYNC_LOCK_MINUTES = 5
MAX_STORE_PAGES = 2000
FULL_SYNC_WORKERS = 4
POINTS_WILAYA = 16
DELIVERED = 41
CANCELLED = 50
RETURNED_STATUSES = (10, 51, 52, 53)
TOP_WILAYAS = 10
DAYS_SHOWN = 31
ORDER_EVENTS = {"OrderCreated", "OrderStatusChanged", "DeliveryStatusChange", "OrderFieldUpdated"}
MIRROR_FIELDS = [
    "display_id", "status", "product_price", "delivery_price", "total_price", "paid_to_store", "is_refund",
    "is_exchange", "delivery_type", "wilaya", "wilaya_id", "commune_name", "product_names", "products",
    "abort_reason", "alert_reason", "ordered_at", "shipped_at", "delivered_at", "remote_updated_at", "shipment",
    "customer_name", "customer_phone", "customer_phone2", "address", "alerted_at", "postponed_to", "synced_at",
]
FIELD_SOURCES = {
    "display_id": ("display_id", "display_id_order"),
    "product_price": ("product_price",),
    "delivery_price": ("price", "delivery_price"),
    "total_price": ("total_price",),
    "paid_to_store": ("paid_to_store",),
    "is_refund": ("is_refund",),
    "is_exchange": ("is_exchange",),
    "delivery_type": ("delivery_type",),
    "wilaya": ("wilaya",),
    "wilaya_id": ("wilaya_id",),
    "commune_name": ("commune_name",),
    "product_names": ("product_name", "products"),
    "products": ("products",),
    "abort_reason": ("abort_reason_name",),
    "alert_reason": ("alert_reason_name",),
    "ordered_at": ("ordered_at", "created_at"),
    "shipped_at": ("shipped_at",),
    "delivered_at": ("delivered_at",),
    "remote_updated_at": ("updated_at", "last_update"),
    "customer_name": ("customer_name",),
    "customer_phone": ("customer_phone",),
    "customer_phone2": ("customer_phone2",),
    "address": ("destination_text",),
    "alerted_at": ("alerted_at",),
    "postponed_to": ("postponed_to",),
}
SHIPPED_RETURN = Q(status=CANCELLED) & ~Q(shipped_at=None)
GROUPS = {
    "delivered": Q(status=DELIVERED, is_refund=False),
    "in_delivery": Q(status__in=ACTIVE_STATUSES, is_refund=False),
    "returned": (Q(status__in=RETURNED_STATUSES) | SHIPPED_RETURN) & Q(is_refund=False),
    "cancelled": Q(status=CANCELLED, shipped_at=None, is_refund=False),
    "refunds": Q(is_refund=True),
}
CANCELLED_LABELS = {True: "أُلغي بعد الشحن (رجع)", False: "أُلغي قبل الشحن"}
NOTES = [
    "Maystro لا يوفر رصيدك ولا رسوم الإرجاع عبر الواجهة البرمجية، وحقل «مدفوع للمتجر» عندهم لا يتحدّث، لذلك «المتبقي عند Maystro» تقدير فقط: قارنه دائماً مع بوابة Maystro.",
    "التقدير = قيمة المنتجات في الطرود المسلّمة − الدفعات المسجّلة − رسوم الإرجاع المقدّرة (عدد الطرود الراجعة × رسم الإرجاع في الإعدادات، أو الرسم الحقيقي إن أرسله Maystro).",
    "سجّل كل دفعة تستلمها من Maystro، ولا تسجّل يدوياً دفعة وصل إشعارها من Maystro حتى لا تُحسب مرتين.",
]


def money(value):
    if value in (None, ""):
        return 0
    try:
        return int(Decimal(str(value)).quantize(Decimal(1)))
    except (InvalidOperation, ValueError):
        return 0


def whole(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def share(part, whole_value):
    return round(part * 100 / whole_value, 1) if whole_value else 0


def product_lines(items):
    lines = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        lines.append(
            {
                "product_id": str(item.get("product_id") or item.get("product") or ""),
                "name": str(item.get("logistical_description") or item.get("description") or ""),
                "quantity": whole(item.get("quantity")) or 1,
            }
        )
    return lines


CONTACT_FIELDS = ("customer_name", "customer_phone", "customer_phone2", "address")


def without_contact(fields):
    if fields.get("status") not in ACTIVE_STATUSES:
        fields.update({name: "" for name in CONTACT_FIELDS})
    return fields


def row_fields(row):
    lines = product_lines(row.get("products"))
    return without_contact({
        "display_id": str(row.get("display_id") or row.get("display_id_order") or ""),
        "status": whole(row.get("status")) or 0,
        "product_price": money(row.get("product_price")),
        "delivery_price": money(row.get("price") if "price" in row else row.get("delivery_price")),
        "total_price": money(row.get("total_price")),
        "paid_to_store": bool(row.get("paid_to_store")),
        "is_refund": bool(row.get("is_refund")),
        "is_exchange": bool(row.get("is_exchange")),
        "delivery_type": whole(row.get("delivery_type")),
        "wilaya": str(row.get("wilaya") or "")[:80],
        "wilaya_id": whole(row.get("wilaya_id")),
        "commune_name": str(row.get("commune_name") or "")[:120],
        "product_names": str(row.get("product_name") or "، ".join(line["name"] for line in lines))[:500],
        "products": lines,
        "abort_reason": str(row.get("abort_reason_name") or "")[:200],
        "alert_reason": str(row.get("alert_reason_name") or "")[:200],
        "ordered_at": parse_moment(row.get("ordered_at") or row.get("created_at")),
        "shipped_at": parse_moment(row.get("shipped_at")),
        "delivered_at": parse_moment(row.get("delivered_at")),
        "remote_updated_at": parse_moment(row.get("updated_at") or row.get("last_update")),
        "customer_name": str(row.get("customer_name") or "")[:150],
        "customer_phone": str(row.get("customer_phone") or "")[:30],
        "customer_phone2": str(row.get("customer_phone2") or "")[:30],
        "address": str(row.get("destination_text") or "")[:255],
        "alerted_at": parse_moment(row.get("alerted_at")),
        "postponed_to": parse_moment(row.get("postponed_to")),
    })


def upsert_rows(rows):
    rows = [row for row in rows if isinstance(row, dict) and row.get("id")]
    ids = [str(row["id"]) for row in rows]
    existing = set(MaystroOrder.objects.filter(maystro_id__in=ids).values_list("maystro_id", flat=True))
    shipments = dict(Shipment.objects.filter(external_id__in=ids).values_list("external_id", "id"))
    objects = [MaystroOrder(maystro_id=str(row["id"]), shipment_id=shipments.get(str(row["id"])), **row_fields(row)) for row in rows]
    if objects:
        target = {"unique_fields": ["maystro_id"]} if connection.features.supports_update_conflicts_with_target else {}
        MaystroOrder.objects.bulk_create(objects, update_conflicts=True, update_fields=MIRROR_FIELDS, **target)
    created = len([item for item in ids if item not in existing])
    return created, len(ids) - created, len(shipments)


def page_is_last(data):
    return not data["results"] or not data["next"]


def known_pages(first):
    size = len(first["results"])
    if not size:
        return 1
    return max(1, min(MAX_STORE_PAGES, -(-int(first["count"] or 0) // size)))


def parallel_pages(client, first_page, last_page):
    pool = ThreadPoolExecutor(max_workers=FULL_SYNC_WORKERS)
    try:
        pages = range(first_page, last_page + 1)
        yield from zip(pages, pool.map(client.store_orders, pages))
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def page_stream(client, full):
    first = client.store_orders(1)
    yield first
    if page_is_last(first):
        return
    page = 2
    if full:
        last_page = known_pages(first)
        for page, data in parallel_pages(client, 2, last_page):
            yield data
            if page_is_last(data):
                return
        page = last_page + 1
    while page <= MAX_STORE_PAGES:
        data = client.store_orders(page)
        yield data
        if page_is_last(data):
            return
        page += 1


def pull_orders(client, full, progress=None):
    cutoff = None if full else timezone.now() - timedelta(days=MIRROR_RECENT_DAYS)
    totals_seen = {"pages": 0, "fetched": 0, "created": 0, "updated": 0, "linked": 0}
    for data in page_stream(client, full):
        rows = data["results"]
        totals_seen["pages"] += 1
        if not rows:
            break
        created, updated, linked = upsert_rows(rows)
        totals_seen["fetched"] += len(rows)
        totals_seen["created"] += created
        totals_seen["updated"] += updated
        totals_seen["linked"] += linked
        if progress is not None:
            progress(totals_seen["fetched"], max(int(data["count"] or 0), totals_seen["fetched"]))
        if cutoff is not None:
            moments = [moment for moment in (parse_moment(row.get("ordered_at") or row.get("created_at")) for row in rows) if moment]
            if moments and min(moments) < cutoff:
                break
    return totals_seen


def warehouse_lines(entries):
    return [
        {
            "warehouse": entry.get("warehouse"),
            "warehouse_name": entry.get("warehouse_name", ""),
            "quantity": whole(entry.get("quantity")) or 0,
            "defective_quantity": whole(entry.get("defective_quantity")) or 0,
            "incoming_quantity": whole(entry.get("incoming_quantity")) or 0,
            "outgoing_quantity": whole(entry.get("outgoing_quantity")) or 0,
            "available_quantity": whole(entry.get("available_quantity")) or 0,
        }
        for entry in entries
        if isinstance(entry, dict)
    ]


def sync_products(client):
    remote = client.products()
    for item in remote:
        stock = []
        if not item.get("deleted"):
            try:
                stock = warehouse_lines(client.stock_details(item["id"]))
            except MaystroError as error:
                if error.status_code not in (400, 404):
                    raise
        MaystroProduct.objects.update_or_create(
            maystro_id=str(item["id"]),
            defaults={
                "display_id": whole(item.get("display_id")),
                "name": str(item.get("logistical_description") or "")[:255],
                "reference": str(item.get("product_id") or "")[:120],
                "delivery_rate": item.get("delivery_rate"),
                "cancelation_rate": item.get("cancelation_rate"),
                "deleted": bool(item.get("deleted")),
                "last_used": parse_moment(item.get("last_used")),
                "low_stock_level": whole(item.get("low_stock_level")),
                "stock": stock,
            },
        )
    from .stock import adopt_products

    adopt_products()
    MaystroConfig.objects.filter(pk=MaystroConfig.load().pk).update(live_stock_at=timezone.now())
    return len(remote)


def refresh_points(client):
    wilayas = client.wilayas()
    center = next((w.get("center_commune") for w in wilayas if isinstance(w, dict) and whole(w.get("code")) == POINTS_WILAYA), None)
    if not center:
        return None
    options = client.delivery_options(center)
    for option in options if isinstance(options, list) else []:
        if isinstance(option, dict) and option.get("current_points") is not None:
            return float(option["current_points"])
    return None


def claim_sync_slot():
    now = timezone.now()
    with transaction.atomic():
        config = MaystroConfig.objects.select_for_update().get(pk=MaystroConfig.load().pk)
        if config.mirror_running_since and now - config.mirror_running_since < timedelta(minutes=SYNC_LOCK_MINUTES):
            raise conflict("مزامنة Maystro جارية بالفعل، انتظر قليلاً", "sync_running")
        config.mirror_running_since = now
        config.mirror_progress_done = 0
        config.mirror_progress_total = 0
        config.save(update_fields=["mirror_running_since", "mirror_progress_done", "mirror_progress_total"])
    return config


def sync_is_running(config):
    started = config.mirror_running_since
    return started is not None and timezone.now() - started < timedelta(minutes=SYNC_LOCK_MINUTES)


def claim_live_slot():
    if MaystroConfig.load().mode != MaystroConfig.Mode.LIVE:
        raise conflict("فعّل الوضع المباشر وأدخل مفتاح Maystro أولاً", "maystro_simulated")
    return claim_sync_slot()


def sync_status(config=None):
    config = config or MaystroConfig.load()
    return {
        "last_sync_at": config.mirror_synced_at,
        "last_sync_ok": config.mirror_sync_ok,
        "last_sync_error": config.mirror_sync_error,
        "orders_in_mirror": MaystroOrder.objects.count(),
        "full_sync_done": config.mirror_full_sync_at is not None,
        "running": sync_is_running(config),
        "interrupted": config.mirror_running_since is not None and not sync_is_running(config),
        "progress": {"done": config.mirror_progress_done, "total": config.mirror_progress_total},
    }


def sync_account(full=False):
    return perform_sync(claim_live_slot(), full)


def run_in_background(config):
    try:
        perform_sync(config, True)
    except ApiError:
        return
    finally:
        connection.close()


def start_background_sync():
    config = claim_live_slot()
    threading.Thread(target=run_in_background, args=(config,), daemon=True).start()


def perform_sync(config, full):
    full = full or config.mirror_full_sync_at is None
    client = get_client(config)
    try:
        result = pull_orders(client, full, progress=lambda done, total: MaystroConfig.objects.filter(pk=config.pk).update(mirror_progress_done=done, mirror_progress_total=total, mirror_running_since=timezone.now()))
        result["products"] = sync_products(client)
        result["points"] = refresh_points(client)
    except MaystroError as error:
        MaystroConfig.objects.filter(pk=config.pk).update(mirror_sync_ok=False, mirror_sync_error=error.message[:255], mirror_running_since=None)
        raise ApiError(error.message, error.code, 502) from error
    except Exception:
        MaystroConfig.objects.filter(pk=config.pk).update(mirror_running_since=None)
        raise
    finished = timezone.now()
    updates = {"mirror_synced_at": finished, "mirror_sync_ok": True, "mirror_sync_error": "", "mirror_running_since": None}
    if result["points"] is not None:
        updates["current_points"] = result["points"]
    if full:
        updates["mirror_full_sync_at"] = finished
    MaystroConfig.objects.filter(pk=config.pk).update(**updates)
    return {"full": full, **result, "synced_at": finished}


def period_bounds(params):
    date_from = params.get("date_from")
    date_to = params.get("date_to")
    if date_from or date_to:
        first = parse_day(date_from, "date_from") if date_from else None
        last = parse_day(date_to, "date_to") if date_to else None
        if first and last and last < first:
            raise ValidationError({"date_to": "نهاية الفترة قبل بدايتها"})
        return first, last
    period = params.get("period", "all")
    if period == "month":
        today = timezone.localdate()
        return today.replace(day=1), today
    if period != "all":
        raise ValidationError({"period": "القيمة يجب أن تكون all أو month"})
    return None, None


def in_period(queryset, first, last, field="ordered_at"):
    if first:
        queryset = queryset.filter(**{f"{field}__gte": local_datetime(first)})
    if last:
        queryset = queryset.filter(**{f"{field}__lt": local_datetime(last + timedelta(days=1))})
    return queryset


def totals(queryset):
    result = queryset.aggregate(count=Count("id"), price=Sum("product_price"), total=Sum("total_price"), fees=Sum("delivery_price"))
    return {key: value or 0 for key, value in result.items()}


def return_fee_totals(queryset, fee):
    result = queryset.aggregate(
        count=Count("id"),
        known=Count("id", filter=~Q(return_fees=None)),
        amount=Sum(Coalesce("return_fees", Value(fee, output_field=BigIntegerField()))),
    )
    return {key: value or 0 for key, value in result.items()}


def outcome_of(status, shipped):
    if status == DELIVERED:
        return "delivered"
    if status in RETURNED_STATUSES or (status == CANCELLED and shipped):
        return "returned"
    if status == CANCELLED:
        return "cancelled"
    if status in ACTIVE_STATUSES:
        return "in_delivery"
    return "other"


def product_breakdown(queryset):
    stats = defaultdict(lambda: {"name": "", "orders": 0, "delivered": 0, "returned": 0, "cancelled": 0})
    for status, shipped_at, lines in queryset.values_list("status", "shipped_at", "products"):
        outcome = outcome_of(status, shipped_at is not None)
        for line in lines or []:
            key = line.get("product_id") or line.get("name")
            entry = stats[key]
            entry["name"] = entry["name"] or line.get("name", "")
            entry["orders"] += 1
            if outcome in ("delivered", "returned", "cancelled"):
                entry[outcome] += 1
    catalog = {item.maystro_id: item for item in MaystroProduct.objects.all()}
    rows = []
    for key, entry in stats.items():
        product = catalog.get(key)
        finished = entry["delivered"] + entry["returned"] + entry["cancelled"]
        rows.append(
            {
                "maystro_id": key,
                "name": product.name if product else entry["name"],
                **{name: entry[name] for name in ("orders", "delivered", "returned", "cancelled")},
                "delivery_rate": share(entry["delivered"], finished),
                "shipped_delivery_rate": share(entry["delivered"], entry["delivered"] + entry["returned"]),
                "maystro_delivery_rate": product.delivery_rate if product else None,
                "maystro_cancelation_rate": product.cancelation_rate if product else None,
                "available": sum(line.get("available_quantity", 0) for line in product.stock) if product and product.stock else None,
                "stock": product.stock if product else [],
            }
        )
    return sorted(rows, key=lambda row: row["orders"], reverse=True)


def status_row(key, status, label, count, amount):
    return {"key": key, "status": status, "label": label, "count": count, "amount": amount or 0}


def status_breakdown(orders):
    rows = []
    for row in orders.order_by().values("status").annotate(count=Count("id"), amount=Sum("product_price")):
        if row["status"] != CANCELLED:
            label = STATUS_LABELS.get(str(row["status"]), str(row["status"]))
            rows.append(status_row(str(row["status"]), row["status"], label, row["count"], row["amount"]))
            continue
        for shipped in (True, False):
            part = orders.filter(status=CANCELLED, shipped_at__isnull=not shipped).aggregate(count=Count("id"), amount=Sum("product_price"))
            if part["count"]:
                key = "50_returned" if shipped else "50_cancelled"
                rows.append(status_row(key, CANCELLED, CANCELLED_LABELS[shipped], part["count"], part["amount"]))
    return sorted(rows, key=lambda row: (-row["count"], row["key"]))


def balance_scope(config):
    delivered = MaystroOrder.objects.filter(GROUPS["delivered"])
    returned = MaystroOrder.objects.filter(GROUPS["returned"])
    payments = MaystroPayment.objects.all()
    start = config.balance_start_date
    if start:
        moment = local_datetime(start)
        delivered = delivered.filter(Q(delivered_at__gte=moment) | Q(delivered_at=None, ordered_at__gte=moment))
        returned = returned.filter(Q(shipped_at__gte=moment) | Q(shipped_at=None, ordered_at__gte=moment))
        payments = payments.filter(paid_on__gte=moment)
    return delivered, returned, payments


def balance(config=None):
    config = config or MaystroConfig.load()
    delivered, returned, payments = balance_scope(config)
    value = totals(delivered)
    fees = return_fee_totals(returned, config.return_fee)
    paid = payments.aggregate(amount=Sum("amount"), count=Count("id"))
    paid_amount = paid["amount"] or 0
    return {
        "start_date": config.balance_start_date,
        "delivered_value": value["price"],
        "delivered_orders": value["count"],
        "returned_orders": fees["count"],
        "return_fee_per_order": config.return_fee,
        "return_fees_known_orders": fees["known"],
        "estimated_return_fees": fees["amount"],
        "paid_to_you": paid_amount,
        "payments_count": paid["count"],
        "estimated_held": value["price"] - paid_amount - fees["amount"],
        "is_estimate": True,
    }


def set_balance_start(start_date):
    MaystroConfig.objects.filter(pk=MaystroConfig.load().pk).update(balance_start_date=start_date)
    return balance()


def wilaya_breakdown(orders):
    rows = (
        orders.order_by()
        .values("wilaya")
        .annotate(
            orders=Count("id"),
            delivered=Count("id", filter=GROUPS["delivered"]),
            returned=Count("id", filter=GROUPS["returned"]),
            cancelled=Count("id", filter=GROUPS["cancelled"]),
        )
        .order_by("-orders", "wilaya")[:TOP_WILAYAS]
    )
    return [
        {
            "wilaya": row["wilaya"] or "—",
            "orders": row["orders"],
            "delivered": row["delivered"],
            "returned": row["returned"],
            "cancelled": row["cancelled"],
            "delivery_rate": share(row["delivered"], row["delivered"] + row["returned"] + row["cancelled"]),
            "shipped_delivery_rate": share(row["delivered"], row["delivered"] + row["returned"]),
        }
        for row in rows
    ]


def day_breakdown(orders):
    days = list(
        orders.exclude(ordered_at=None)
        .order_by()
        .annotate(day=TruncDate("ordered_at"))
        .values("day")
        .annotate(
            orders=Count("id"),
            delivered=Count("id", filter=GROUPS["delivered"]),
            returned=Count("id", filter=GROUPS["returned"]),
            value=Sum("product_price", filter=GROUPS["delivered"]),
        )
        .order_by("-day")[:DAYS_SHOWN]
    )
    return [
        {"date": row["day"], "orders": row["orders"], "delivered": row["delivered"], "returned": row["returned"], "delivered_value": row["value"] or 0}
        for row in reversed(days)
    ]


def summary(params):
    first, last = period_bounds(params)
    config = MaystroConfig.load()
    scoped = in_period(MaystroOrder.objects.all(), first, last)
    orders = scoped.filter(is_refund=False)
    active = totals(orders.filter(GROUPS["in_delivery"]))
    delivered = totals(orders.filter(GROUPS["delivered"]))
    returned_orders = orders.filter(GROUPS["returned"])
    returned = returned_orders.count()
    cancelled = orders.filter(GROUPS["cancelled"]).count()
    fees = return_fee_totals(returned_orders, config.return_fee)
    paid = in_period(MaystroPayment.objects.all(), first, last, field="paid_on").aggregate(amount=Sum("amount"), count=Count("id"))
    return {
        "period": {"from": first, "to": last},
        "sync": sync_status(config),
        "money": {
            "delivered_value": delivered["price"],
            "delivered_total": delivered["total"],
            "delivery_fees": delivered["fees"],
            "delivered_orders": delivered["count"],
            "in_delivery": active["price"],
            "in_delivery_orders": active["count"],
            "paid_to_you": paid["amount"] or 0,
            "payments_count": paid["count"],
            "estimated_return_fees": fees["amount"],
            "return_fees_known_orders": fees["known"],
        },
        "balance": balance(config),
        "counts": {
            "total": orders.count(),
            "delivered": delivered["count"],
            "returned": returned,
            "cancelled": cancelled,
            "shipped": delivered["count"] + returned,
            "in_delivery": active["count"],
            "refunds": scoped.filter(is_refund=True).count(),
            "exchanges": orders.filter(is_exchange=True).count(),
        },
        "delivery_rate": share(delivered["count"], delivered["count"] + returned + cancelled),
        "shipped_delivery_rate": share(delivered["count"], delivered["count"] + returned),
        "points": config.current_points,
        "by_status": status_breakdown(orders),
        "by_product": product_breakdown(orders),
        "by_wilaya": wilaya_breakdown(orders),
        "by_day": day_breakdown(orders),
        "notes": NOTES,
    }


def mirror_orders(params):
    first, last = period_bounds(params)
    queryset = in_period(MaystroOrder.objects.all(), first, last)
    group = params.get("group")
    if group:
        if group not in GROUPS:
            raise ValidationError({"group": "مجموعة غير معروفة"})
        queryset = queryset.filter(GROUPS[group])
    status = params.get("status")
    if status:
        if whole(status) is None:
            raise ValidationError({"status": "رقم حالة غير صالح"})
        queryset = queryset.filter(status=whole(status))
    search = (params.get("search") or "").strip()
    if search:
        queryset = queryset.filter(Q(display_id__icontains=search) | Q(product_names__icontains=search))
    return queryset.order_by("-ordered_at", "-id")


@transaction.atomic
def record_payment(payload):
    ids = sorted(str(item) for item in payload.get("orders") or [])
    paid_on = parse_moment(payload.get("date")) or timezone.now()
    fingerprint = hashlib.sha256(f"{payload.get('date')}|{','.join(ids)}".encode("utf-8")).hexdigest()
    payment, created = MaystroPayment.objects.get_or_create(
        fingerprint=fingerprint,
        defaults={"paid_on": paid_on, "order_ids": ids, "orders_count": len(ids), "source": MaystroPayment.Source.WEBHOOK},
    )
    matched = MaystroOrder.objects.filter(Q(maystro_id__in=ids) | Q(display_id__in=ids))
    if created:
        matched.update(payment=payment, paid_to_store=True)
        payment.amount = matched.filter(is_refund=False).aggregate(value=Sum("product_price"))["value"] or 0
        payment.save(update_fields=["amount"])
    return created, matched.count()


def add_manual_payment(amount, paid_on, note, user):
    return MaystroPayment.objects.create(
        fingerprint=f"manual-{uuid.uuid4().hex}",
        source=MaystroPayment.Source.MANUAL,
        paid_on=local_datetime(paid_on, datetime_time(12)),
        amount=amount,
        note=note,
        created_by=user,
    )


def remove_manual_payment(payment):
    if payment.source != MaystroPayment.Source.MANUAL:
        raise conflict("هذه الدفعة وصلت من Maystro ولا يمكن حذفها", "payment_locked")
    payment.delete()


def upsert_event(payload):
    maystro_id = str(payload.get("id") or "")
    if not maystro_id or payload.get("status") in (None, ""):
        return False
    fields = row_fields(payload)
    incoming = fields["remote_updated_at"]
    current = MaystroOrder.objects.filter(maystro_id=maystro_id).first()
    if current and current.remote_updated_at and incoming and incoming < current.remote_updated_at:
        return False
    if current is not None:
        for key, sources in FIELD_SOURCES.items():
            if any(source in payload for source in sources):
                continue
            if key in CONTACT_FIELDS and fields["status"] not in ACTIVE_STATUSES:
                continue
            fields.pop(key, None)
    if payload.get("return_fees") is not None:
        fields["return_fees"] = money(payload.get("return_fees"))
    shipment = Shipment.objects.filter(external_id=maystro_id).first()
    if shipment is not None:
        fields["shipment"] = shipment
    MaystroOrder.objects.update_or_create(maystro_id=maystro_id, defaults=fields)
    return True
