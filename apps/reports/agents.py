from collections import defaultdict

from django.db.models import Count, Sum

from apps.accounts.models import Role, User
from apps.core.dates import day_bounds
from apps.finance.services import share
from apps.orders.models import ConfirmationOutcome, Order, OrderEvent, OrderStatus
from apps.shipping.models import ParcelCall

OUTCOMES = [value for value, _ in ConfirmationOutcome.choices]


def blank(user):
    return {
        "user": user.pk if user else None,
        "name": user.full_name if user else "",
        "role": user.role if user else "",
        "role_label": user.get_role_display() if user else "",
        "is_active": user.is_active if user else False,
        "calls": 0,
        "orders_called": 0,
        "outcomes": {outcome: 0 for outcome in OUTCOMES},
        "confirmed": 0,
        "confirmation_rate": 0,
        "waiting": 0,
        "shipping": 0,
        "delivered": 0,
        "returned": 0,
        "cancelled": 0,
        "delivery_rate": 0,
        "delivered_sales": 0,
        "suivi_calls": 0,
        "sent": 0,
    }


def agent_performance(first_day, last_day, user_id=None):
    start = day_bounds(first_day)[0]
    end = day_bounds(last_day)[1]
    people = {user.pk: user for user in User.objects.all()}
    shown = User.objects.filter(pk=user_id) if user_id else User.objects.filter(role=Role.CONFIRMATION, is_active=True)
    rows = {user.pk: blank(user) for user in shown}

    def row(pk):
        if pk not in rows:
            rows[pk] = blank(people.get(pk))
        return rows[pk]

    calls = OrderEvent.objects.filter(stage=OrderEvent.Stage.CONFIRM, user__isnull=False, created_at__gte=start, created_at__lt=end)
    if user_id:
        calls = calls.filter(user_id=user_id)
    for pk, outcome, total in calls.order_by().values_list("user_id", "outcome").annotate(total=Count("id")):
        entry = row(pk)
        entry["calls"] += total
        if outcome in entry["outcomes"]:
            entry["outcomes"][outcome] += total
    for pk, total in calls.order_by().values_list("user_id").annotate(total=Count("order_id", distinct=True)):
        row(pk)["orders_called"] = total

    confirmed = Order.objects.filter(confirmed_by__isnull=False, confirmed_at__gte=start, confirmed_at__lt=end)
    if user_id:
        confirmed = confirmed.filter(confirmed_by_id=user_id)
    states = {
        OrderStatus.CONFIRMED: "waiting",
        OrderStatus.SHIPPING: "shipping",
        OrderStatus.DELIVERED: "delivered",
        OrderStatus.RETURNED: "returned",
        OrderStatus.CANCELLED: "cancelled",
    }
    for pk, state, total, sales in confirmed.order_by().values_list("confirmed_by_id", "status").annotate(total=Count("id"), sales=Sum("subtotal")):
        entry = row(pk)
        entry["confirmed"] += total
        if state in states:
            entry[states[state]] += total
        if state == OrderStatus.DELIVERED:
            entry["delivered_sales"] += sales or 0

    suivi = defaultdict(int)
    events = OrderEvent.objects.filter(stage=OrderEvent.Stage.SUIVI, user__isnull=False, created_at__gte=start, created_at__lt=end)
    parcels = ParcelCall.objects.filter(user__isnull=False, created_at__gte=start, created_at__lt=end)
    if user_id:
        events = events.filter(user_id=user_id)
        parcels = parcels.filter(user_id=user_id)
    for queryset in (events, parcels):
        for pk, total in queryset.order_by().values_list("user_id").annotate(total=Count("id")):
            suivi[pk] += total
    for pk, total in suivi.items():
        row(pk)["suivi_calls"] = total

    sent = Order.objects.filter(shipped_by__isnull=False, shipped_at__gte=start, shipped_at__lt=end)
    if user_id:
        sent = sent.filter(shipped_by_id=user_id)
    for pk, total in sent.order_by().values_list("shipped_by_id").annotate(total=Count("id")):
        row(pk)["sent"] = total

    agents = []
    for entry in rows.values():
        if entry["user"] is None:
            continue
        entry["confirmation_rate"] = share(entry["confirmed"], entry["orders_called"]) if entry["orders_called"] else (100 if entry["confirmed"] else 0)
        entry["delivery_rate"] = share(entry["delivered"], entry["delivered"] + entry["returned"])
        agents.append(entry)
    agents.sort(key=lambda item: (-item["confirmed"], -item["calls"], item["name"]))
    keys = ["calls", "orders_called", "confirmed", "waiting", "shipping", "delivered", "returned", "cancelled", "delivered_sales", "suivi_calls", "sent"]
    totals = {key: sum(item[key] for item in agents) for key in keys}
    totals["confirmation_rate"] = share(totals["confirmed"], totals["orders_called"]) if totals["orders_called"] else 0
    totals["delivery_rate"] = share(totals["delivered"], totals["delivered"] + totals["returned"])
    return {"from": first_day, "to": last_day, "agents": agents, "totals": totals}
