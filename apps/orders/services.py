import re
from collections import defaultdict
from datetime import datetime, time, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.models import Role, RolePermission, Section, User
from apps.catalog.models import Product
from apps.catalog.services import available_quantity
from apps.core.exceptions import conflict

from .models import ConfirmationOutcome, DeliveryType, Order, OrderEvent, OrderItem, OrderStatus, SuiviOutcome
from .signals import order_confirmed

PHONE_PATTERN = re.compile(r"^0[567]\d{8}$")
EDITABLE_STATUSES = {OrderStatus.PENDING, OrderStatus.CONFIRMED}
SUIVI_STAGES = ("out", "desk", "failed")
AGENT_ACTIVE_HOURS = 24


def normalize_phone(raw):
    if isinstance(raw, float):
        raw = int(raw)
    digits = re.sub(r"\D", "", str(raw if raw is not None else ""))
    if digits.startswith("00213"):
        digits = "0" + digits[5:]
    elif digits.startswith("213") and len(digits) > 10:
        digits = "0" + digits[3:]
    if len(digits) == 9 and digits[0] in "567":
        digits = "0" + digits
    return digits


def phone_problem(raw):
    text = str(raw if raw is not None else "").strip()
    digits = normalize_phone(raw)
    if PHONE_PATTERN.fullmatch(digits):
        return None
    if text.startswith("+") or digits.startswith("00") or len(digits) > 10:
        return "phone_foreign"
    if len(digits) < 10:
        return "phone_short"
    return "phone_foreign"


def delivery_fee_for(wilaya, delivery_type):
    if delivery_type == DeliveryType.DESK:
        if not wilaya.desk_available:
            raise ValidationError({"delivery_type": f"مكتب التوصيل غير متاح في {wilaya.name_ar}"})
        return wilaya.desk_fee
    if not wilaya.home_available:
        raise ValidationError({"delivery_type": f"التوصيل إلى المنزل غير متاح في {wilaya.name_ar}"})
    return wilaya.home_fee


def clean_variant(value):
    return " ".join(str(value or "").split())[:150]


def merge_items(items):
    merged = {}
    for item in items:
        key = (item["product"].pk, clean_variant(item.get("variant")))
        if key in merged:
            merged[key][1] += item["quantity"]
        else:
            merged[key] = [item["product"], item["quantity"], key[1]]
    return [tuple(row) for row in merged.values()]


def priced_items(items):
    merged = {}
    for item in items:
        product = item["product"]
        price = item.get("unit_price", product.price)
        variant = clean_variant(item.get("variant"))
        key = (product.pk, price, variant)
        if key in merged:
            merged[key][1] += item["quantity"]
        else:
            merged[key] = [product, item["quantity"], price, variant]
    return [tuple(row) for row in merged.values()]


def choose_agent(exclude=None):
    roles = RolePermission.objects.filter(section=Section.CONFIRMATION, allowed=True).values_list("role", flat=True)
    agents = User.objects.filter(is_active=True, role__in=roles)
    if exclude is not None:
        agents = agents.exclude(pk=exclude.pk)
    recent = agents.filter(last_activity_at__gte=timezone.now() - timedelta(hours=AGENT_ACTIVE_HOURS))
    pool = recent if recent.exists() else agents
    return (
        pool.annotate(open_orders=Count("assigned_orders", filter=Q(assigned_orders__status=OrderStatus.PENDING)))
        .order_by("open_orders", "id")
        .first()
    )


def redistribute_pending(user):
    for order in Order.objects.filter(assigned_to=user, status=OrderStatus.PENDING):
        order.assigned_to = choose_agent(exclude=user)
        order.save(update_fields=["assigned_to"])


def add_event(order, message, user=None, stage=OrderEvent.Stage.SYSTEM, outcome="", note="", at=None):
    return OrderEvent.objects.create(
        order=order, message=message, user=user, stage=stage, outcome=outcome, note=note, created_at=at or timezone.now()
    )


@transaction.atomic
def create_order(data, user, source="manual", event_message="أضيفت الطلبية"):
    items = priced_items(data["items"])
    wilaya = data["wilaya"]
    delivery_type = data.get("delivery_type", DeliveryType.HOME)
    fee = delivery_fee_for(wilaya, delivery_type)
    if data.get("delivery_fee") is not None:
        fee = data["delivery_fee"]
    subtotal = sum(price * quantity for product, quantity, price, variant in items)
    order = Order.objects.create(
        customer_name=data["customer_name"],
        phone=data["phone"],
        wilaya=wilaya,
        commune=data.get("commune", ""),
        address=data.get("address", ""),
        delivery_type=delivery_type,
        subtotal=subtotal,
        delivery_fee=fee,
        total=subtotal + fee,
        source=source,
        campaign=data.get("campaign"),
        assigned_to=data.get("assigned_to") or choose_agent(),
        note=data.get("note", ""),
        created_by=user,
        created_at=timezone.now(),
    )
    order.reference = f"VZ-{10000 + order.pk}"
    order.save(update_fields=["reference"])
    OrderItem.objects.bulk_create(
        [
            OrderItem(order=order, product=product, quantity=quantity, unit_price=price, unit_cost=product.cost, variant=variant)
            for product, quantity, price, variant in items
        ]
    )
    add_event(order, event_message, user=user)
    return order


def ensure_available(needed):
    for product_id in sorted(needed):
        product = Product.objects.select_for_update().get(pk=product_id)
        if needed[product_id] > available_quantity(product):
            raise conflict(f"الكمية المتوفرة من {product.name} غير كافية", "insufficient_stock")


def order_quantities(order):
    quantities = defaultdict(int)
    for item in order.items.all():
        quantities[item.product_id] += item.quantity
    return quantities


@transaction.atomic
def update_order(order_id, data, user):
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status not in EDITABLE_STATUSES:
        raise conflict("لا يمكن تعديل طلبية بعد شحنها أو إغلاقها", "order_locked")
    before = order.total
    for field in ("customer_name", "phone", "wilaya", "commune", "address", "delivery_type", "campaign", "assigned_to", "note"):
        if field in data:
            setattr(order, field, data[field])
    if "items" in data:
        existing = list(order.items.all())
        kept_prices = {(item.product_id, item.variant): item.unit_price for item in existing}
        product_prices = {item.product_id: item.unit_price for item in existing}
        rows = []
        for item in data["items"]:
            product = item["product"]
            variant = clean_variant(item.get("variant"))
            price = item.get("unit_price")
            if price is None:
                price = kept_prices.get((product.pk, variant), product_prices.get(product.pk, product.price))
            rows.append({"product": product, "quantity": item["quantity"], "unit_price": price, "variant": variant})
        items = priced_items(rows)
        if order.status == OrderStatus.CONFIRMED:
            current = order_quantities(order)
            wanted = defaultdict(int)
            for product, quantity, price, variant in items:
                wanted[product.pk] += quantity
            extra = {key: quantity - current.get(key, 0) for key, quantity in wanted.items()}
            ensure_available({key: value for key, value in extra.items() if value > 0})
        order.items.all().delete()
        OrderItem.objects.bulk_create(
            [
                OrderItem(order=order, product=product, quantity=quantity, unit_price=price, unit_cost=product.cost, variant=variant)
                for product, quantity, price, variant in items
            ]
        )
    if "wilaya" in data or "delivery_type" in data:
        order.delivery_fee = delivery_fee_for(order.wilaya, order.delivery_type)
    if data.get("delivery_fee") is not None:
        order.delivery_fee = data["delivery_fee"]
    order.subtotal = sum(item.unit_price * item.quantity for item in order.items.all())
    order.total = order.subtotal + order.delivery_fee
    order.save()
    message = "عُدّلت بيانات الطلبية"
    if order.total != before:
        message = f"{message} · المجموع صار {order.total:,} د.ج بدل {before:,}"
    add_event(order, message, user=user)
    return order


def find_duplicate(phone, product_ids):
    since = timezone.now() - timedelta(days=settings.DUPLICATE_WINDOW_DAYS)
    return (
        Order.objects.filter(phone=phone, items__product_id__in=product_ids, created_at__gte=since)
        .exclude(status=OrderStatus.CANCELLED)
        .first()
    )


def retry_time():
    return timezone.now() + timedelta(hours=settings.CALL_RETRY_HOURS)


def next_morning():
    tomorrow = timezone.localdate() + timedelta(days=1)
    return timezone.make_aware(datetime.combine(tomorrow, time(8, 30)))


@transaction.atomic
def record_confirmation(order_id, outcome, user, note="", next_call_at=None):
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status != OrderStatus.PENDING:
        raise conflict("هذه الطلبية ليست بانتظار التأكيد", "invalid_transition")
    if outcome == ConfirmationOutcome.POSTPONED and next_call_at is None:
        raise ValidationError({"next_call_at": "حدد موعد الاتصال التالي"})
    now = timezone.now()
    if order.assigned_to_id is None:
        order.assigned_to = user
    order.call_attempts += 1
    if outcome == ConfirmationOutcome.CONFIRMED:
        ensure_available(order_quantities(order))
        order.status = OrderStatus.CONFIRMED
        order.confirmed_at = now
        order.confirmed_by = user
        order.next_call_at = None
    elif outcome in (ConfirmationOutcome.NO_ANSWER, ConfirmationOutcome.BUSY):
        order.next_call_at = retry_time()
    elif outcome == ConfirmationOutcome.POSTPONED:
        order.next_call_at = next_call_at
    else:
        order.status = OrderStatus.CANCELLED
        order.cancelled_at = now
        order.cancel_reason = outcome
        order.next_call_at = None
    order.save()
    label = ConfirmationOutcome(outcome).label
    add_event(order, f"{label} · {user.full_name}", user=user, stage=OrderEvent.Stage.CONFIRM, outcome=outcome, note=note)
    if order.status == OrderStatus.CONFIRMED:
        order_confirmed.send(sender=Order, order=order)
    return order


@transaction.atomic
def record_suivi(order_id, outcome, user, note="", next_call_at=None):
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status != OrderStatus.SHIPPING:
        raise conflict("هذه الطلبية ليست قيد الشحن", "invalid_transition")
    if outcome == SuiviOutcome.POSTPONED and next_call_at is None:
        raise ValidationError({"next_call_at": "حدد موعد الاتصال التالي"})
    order.suivi_attempts += 1
    if outcome == SuiviOutcome.NO_ANSWER:
        order.suivi_next_at = retry_time()
    elif outcome == SuiviOutcome.POSTPONED:
        order.suivi_next_at = next_call_at
    else:
        order.suivi_next_at = next_morning()
    order.save(update_fields=["suivi_attempts", "suivi_next_at"])
    label = SuiviOutcome(outcome).label
    add_event(order, f"{label} · {user.full_name}", user=user, stage=OrderEvent.Stage.SUIVI, outcome=outcome, note=note)
    return order


@transaction.atomic
def cancel_order(order_id, user, reason=""):
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status not in EDITABLE_STATUSES:
        raise conflict("لا يمكن إلغاء طلبية بعد شحنها أو إغلاقها", "order_locked")
    order.status = OrderStatus.CANCELLED
    order.cancelled_at = timezone.now()
    order.cancel_reason = reason or "manual"
    order.next_call_at = None
    order.save()
    add_event(order, f"أُلغيت الطلبية · {user.full_name}", user=user, note=reason)
    return order


def visible_to(queryset, user):
    if user.role == Role.ADMIN:
        return queryset
    return queryset.filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))


def confirmation_queue():
    now = timezone.now()
    return Order.objects.filter(status=OrderStatus.PENDING).filter(Q(next_call_at__isnull=True) | Q(next_call_at__lte=now))


def suivi_queue():
    now = timezone.now()
    return Order.objects.filter(status=OrderStatus.SHIPPING, shipment__stage__in=SUIVI_STAGES).filter(
        Q(suivi_next_at__isnull=True) | Q(suivi_next_at__lte=now)
    )
