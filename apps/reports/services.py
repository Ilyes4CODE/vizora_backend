from calendar import monthrange
from datetime import date

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.catalog.models import Product
from apps.catalog.services import stock_levels
from apps.core.dates import day_bounds, first_of_previous_month, iterate_days, month_bounds, month_key
from apps.finance.models import Settlement
from apps.finance.services import profit_summary, settlement_totals, share
from apps.orders.models import Order, OrderEvent, OrderItem, OrderStatus
from apps.orders.services import confirmation_queue, suivi_queue, visible_to
from apps.shipping.live import active_parcels, filter_parcels, is_ready
from apps.shipping.live import suivi_queue as live_suivi_queue
from apps.shipping.models import MaystroConfig
from apps.shipping.services import active_shipments, filter_display_stage

TOP_WILAYAS = 5


def month_orders(first_day):
    start, end = month_bounds(first_day)
    return Order.objects.filter(created_at__gte=start, created_at__lt=end)


def order_kpis(first_day):
    orders = month_orders(first_day)
    received = orders.count()
    confirmed = orders.exclude(confirmed_at=None).count()
    delivered = orders.filter(status=OrderStatus.DELIVERED).count()
    returned = orders.filter(status=OrderStatus.RETURNED).count()
    return {
        "orders": received,
        "confirmed": confirmed,
        "not_confirmed": received - confirmed,
        "confirmation_rate": share(confirmed, received),
        "delivered": delivered,
        "returned": returned,
        "delivery_rate": share(delivered, confirmed),
    }


def daily_order_counts(first_day):
    last_day = date(first_day.year, first_day.month, monthrange(first_day.year, first_day.month)[1])
    found = dict(
        month_orders(first_day)
        .order_by()
        .annotate(day=TruncDate("created_at"))
        .values_list("day")
        .annotate(total=Count("id"))
    )
    return [{"date": day, "orders": found.get(day, 0)} for day in iterate_days(first_day, last_day)]


def funnel(kpis):
    received = kpis["orders"]
    steps = [
        ("received", "وردت", received),
        ("confirmed", "مؤكدة", kpis["confirmed"]),
        ("delivered", "مسلّمة", kpis["delivered"]),
        ("returned", "مرتجعة", kpis["returned"]),
    ]
    return [{"key": key, "label": label, "value": value, "share": share(value, received)} for key, label, value in steps]


def agents_today():
    start, end = day_bounds(timezone.localdate())
    rows = (
        OrderEvent.objects.filter(stage=OrderEvent.Stage.CONFIRM, outcome="confirmed", created_at__gte=start, created_at__lt=end)
        .order_by()
        .values("user_id", "user__full_name")
        .annotate(confirmed=Count("id"))
        .order_by("-confirmed")
    )
    return [{"user": row["user_id"], "name": row["user__full_name"], "confirmed": row["confirmed"]} for row in rows]


def low_stock_products():
    alerts = []
    for product in Product.objects.filter(is_active=True):
        levels = stock_levels(product)
        if levels["state"] != "ok":
            alerts.append({"id": product.pk, "name": product.name, "available": levels["available"], "state": levels["state"]})
    return alerts


def unpaid_dues():
    settlements = [(item, settlement_totals(item)) for item in Settlement.objects.filter(status=Settlement.Status.OPEN)]
    latest = settlements[0] if settlements else None
    return {
        "amount": sum(totals["net"] for _, totals in settlements),
        "settlements": len(settlements),
        "latest": {
            "id": latest[0].pk,
            "period_start": latest[0].period_start,
            "period_end": latest[0].period_end,
            "delivered_parcels": latest[1]["delivered_parcels"],
        } if latest else None,
    }


def parcel_figures(config, user):
    if is_ready(config):
        return {
            "suivi_due": live_suivi_queue(config).count(),
            "stuck_parcels": filter_parcels(config, "stuck").count(),
            "in_shipping": active_parcels(config).count(),
        }
    return {
        "suivi_due": visible_to(suivi_queue(), user).count(),
        "stuck_parcels": filter_display_stage(active_shipments(), "stuck").count(),
        "in_shipping": active_shipments().count(),
    }


def dashboard(user, first_day):
    config = MaystroConfig.load()
    kpis = order_kpis(first_day)
    agents = agents_today()
    result = {
        "month": month_key(first_day),
        "today": {
            "date": timezone.localdate(),
            "pending_confirmation": visible_to(confirmation_queue(), user).count(),
            **parcel_figures(config, user),
            "confirmed_today": sum(agent["confirmed"] for agent in agents),
            "low_stock": low_stock_products(),
        },
        "kpis": kpis,
        "daily_orders": daily_order_counts(first_day),
        "funnel": funnel(kpis),
        "agents_today": agents,
        "maystro": {"mode": config.mode, "last_sync_at": config.last_sync_at, "last_sync_ok": config.last_sync_ok},
    }
    if user.has_section("accounting"):
        summary = profit_summary(first_day)
        result["kpis"]["net_profit"] = summary["net_profit"]
        result["kpis"]["margin"] = summary["margin"]
    if user.has_section("dues"):
        result["unpaid_dues"] = unpaid_dues()
    return result


def product_split(first_day):
    start, end = month_bounds(first_day)
    rows = (
        OrderItem.objects.filter(order__status=OrderStatus.DELIVERED, order__delivered_at__gte=start, order__delivered_at__lt=end)
        .values("product_id", "product__name")
        .annotate(quantity=Sum("quantity"))
        .order_by("-quantity")
    )
    total = sum(row["quantity"] for row in rows)
    return {
        "total": total,
        "products": [
            {"id": row["product_id"], "name": row["product__name"], "quantity": row["quantity"], "share": share(row["quantity"], total)}
            for row in rows
        ],
    }


def wilaya_performance(first_day):
    start, end = month_bounds(first_day)
    rows = list(
        Order.objects.order_by()
        .values("wilaya_id", "wilaya__name_ar")
        .annotate(
            delivered=Count("id", filter=Q(status=OrderStatus.DELIVERED, delivered_at__gte=start, delivered_at__lt=end)),
            returned=Count("id", filter=Q(status=OrderStatus.RETURNED, returned_at__gte=start, returned_at__lt=end)),
        )
        .filter(Q(delivered__gt=0) | Q(returned__gt=0))
        .order_by("-delivered")
    )

    def entry(name, delivered, returned):
        return {"wilaya": name, "delivered": delivered, "returned": returned, "success_rate": share(delivered, delivered + returned)}

    result = [entry(row["wilaya__name_ar"], row["delivered"], row["returned"]) for row in rows[:TOP_WILAYAS]]
    others = rows[TOP_WILAYAS:]
    if others:
        result.append(entry("ولايات أخرى", sum(r["delivered"] for r in others), sum(r["returned"] for r in others)))
    return result


def monthly_report(first_day):
    months = []
    cursor = first_day
    for _ in range(6):
        months.append(cursor)
        cursor = first_of_previous_month(cursor)
    summary = profit_summary(first_day)
    return {
        "month": month_key(first_day),
        "kpis": order_kpis(first_day),
        "accounting": summary,
        "net_profit_by_month": [{"month": month_key(m), "net_profit": profit_summary(m)["net_profit"]} for m in reversed(months)],
        "product_split": product_split(first_day),
        "wilayas": wilaya_performance(first_day),
    }


def daily_report(first_day, last_day):
    created = {
        row["day"]: row
        for row in Order.objects.filter(created_at__gte=day_bounds(first_day)[0], created_at__lt=day_bounds(last_day)[1])
        .order_by()
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(orders=Count("id"), confirmed=Count("id", filter=~Q(confirmed_at=None)))
    }
    delivered = {
        row["day"]: row
        for row in Order.objects.filter(
            status=OrderStatus.DELIVERED, delivered_at__gte=day_bounds(first_day)[0], delivered_at__lt=day_bounds(last_day)[1]
        )
        .order_by()
        .annotate(day=TruncDate("delivered_at"))
        .values("day")
        .annotate(delivered=Count("id"), sales=Sum("subtotal"))
    }
    rows = []
    for day in iterate_days(first_day, last_day):
        orders = created.get(day, {}).get("orders", 0)
        confirmed = created.get(day, {}).get("confirmed", 0)
        rows.append(
            {
                "date": day,
                "orders": orders,
                "confirmed": confirmed,
                "confirmation_rate": share(confirmed, orders),
                "delivered": delivered.get(day, {}).get("delivered", 0),
                "sales": delivered.get(day, {}).get("sales", 0),
            }
        )
    return rows
