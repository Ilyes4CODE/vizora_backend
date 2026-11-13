from datetime import timedelta

from django.core import signing
from django.db import transaction
from django.utils import timezone

from apps.core.models import Commune, Wilaya
from apps.finance.models import Campaign
from apps.orders.models import DeliveryType, Order, OrderStatus, Source
from apps.orders.services import PHONE_PATTERN, create_order, normalize_phone

from .catalog import available_units, forget_stock
from .models import OrderOrigin, StoreSettings

FORM_SALT = "vizora-store-form"
MIN_FORM_SECONDS = 2
MAX_FORM_AGE = 60 * 60 * 24
DUPLICATE_DAYS = 7
TRACKED_UTM = ("utm_source", "utm_medium", "utm_campaign")


class StoreError(Exception):
    def __init__(self, key):
        super().__init__(key)
        self.key = key


def form_stamp():
    return signing.TimestampSigner(salt=FORM_SALT).sign("form")


def bot_check(post):
    if post.get("website", "").strip():
        return "bot"
    stamp = post.get("stamp", "")
    signer = signing.TimestampSigner(salt=FORM_SALT)
    try:
        signer.unsign(stamp, max_age=MAX_FORM_AGE)
    except signing.SignatureExpired:
        return "expired"
    except signing.BadSignature:
        return "expired"
    try:
        signer.unsign(stamp, max_age=MIN_FORM_SECONDS)
    except signing.SignatureExpired:
        return None
    return "bot"


def client_ip(request):
    return request.META.get("REMOTE_ADDR") or None


def clean_text(value, limit):
    return " ".join(str(value or "").split())[:limit]


def clean_phone(raw):
    phone = normalize_phone(raw)
    return phone if PHONE_PATTERN.fullmatch(phone) else None


def clean_customer(post, t):
    errors = {}
    values = {
        "name": clean_text(post.get("name"), 150),
        "phone": clean_text(post.get("phone"), 20),
        "wilaya": clean_text(post.get("wilaya"), 3),
        "commune": clean_text(post.get("commune"), 80),
        "delivery_type": post.get("delivery_type") if post.get("delivery_type") in (DeliveryType.HOME, DeliveryType.DESK) else DeliveryType.HOME,
        "address": clean_text(post.get("address"), 255),
        "note": clean_text(post.get("note"), 300),
    }
    if len(values["name"]) < 3:
        errors["name"] = t["err_name"]
    phone = clean_phone(values["phone"])
    if phone is None:
        errors["phone"] = t["err_phone"]
    wilaya = None
    if values["wilaya"].isdigit():
        wilaya = Wilaya.objects.filter(code=int(values["wilaya"])).first()
    if wilaya is None:
        errors["wilaya"] = t["err_wilaya"]
    else:
        communes = Commune.objects.filter(wilaya=wilaya)
        if communes.exists():
            match = communes.filter(name_ar=values["commune"]).first() or communes.filter(name_fr=values["commune"]).first()
            if match is None:
                errors["commune"] = t["err_commune"]
            else:
                values["commune"] = match.name_ar
        elif not values["commune"]:
            errors["commune"] = t["err_commune"]
        available = wilaya.desk_available if values["delivery_type"] == DeliveryType.DESK else wilaya.home_available
        if not available:
            errors["delivery_type"] = t["err_delivery"]
    if values["delivery_type"] == DeliveryType.HOME and len(values["address"]) < 3:
        errors["address"] = t["err_address"]
    clean = dict(values, phone=phone, wilaya=wilaya)
    return clean, values, errors


def clean_visit(raw):
    raw = raw if isinstance(raw, dict) else {}
    data = {key: clean_text(raw.get(key), 100) for key in TRACKED_UTM}
    data["referrer"] = clean_text(raw.get("referrer"), 255)
    return data


def campaign_for(name):
    if not name:
        return None
    return Campaign.objects.filter(name__iexact=name, is_active=True).first()


def recent_from_ip(ip):
    if not ip:
        return 0
    return OrderOrigin.objects.filter(ip=ip, created_at__gte=timezone.now() - timedelta(hours=1)).count()


def open_duplicate(phone, product_ids):
    since = timezone.now() - timedelta(days=DUPLICATE_DAYS)
    return (
        Order.objects.filter(phone=phone, status=OrderStatus.PENDING, items__product_id__in=product_ids, created_at__gte=since)
        .order_by("-created_at")
        .first()
    )


def offer_note(lines):
    parts = []
    for line in lines:
        title = line["store_product"].title_ar
        if line.get("offer") is not None:
            title = f"{title} · {line['offer'].title_ar}"
        parts.append(f"{title} × {line['quantity']}")
    return "طلبية من الموقع: " + "، ".join(parts)


def place_order(lines, customer, request, lang, visit=None):
    settings = StoreSettings.load()
    if not settings.is_open:
        raise StoreError("store_closed")
    if not lines:
        raise StoreError("err_quantity")
    ip = client_ip(request)
    if recent_from_ip(ip) >= settings.orders_per_hour:
        raise StoreError("err_rate")
    product_ids = [line["store_product"].product_id for line in lines]
    existing = open_duplicate(customer["phone"], product_ids)
    if existing is not None:
        return existing, True
    needed = {}
    for line in lines:
        needed[line["store_product"].product_id] = needed.get(line["store_product"].product_id, 0) + line["quantity"]
    for line in lines:
        product = line["store_product"].product
        if needed[product.pk] > available_units(product, fresh=True):
            raise StoreError("err_stock")
    visit = clean_visit(visit)
    note = offer_note(lines)
    if customer.get("note"):
        note = f"{note}\nملاحظة الزبون: {customer['note']}"
    items = []
    for line in lines:
        price = line["offer"].price if line.get("offer") is not None else line["store_product"].product.price
        items.append({"product": line["store_product"].product, "quantity": line["quantity"], "unit_price": price})
    data = {
        "items": items,
        "customer_name": customer["name"],
        "phone": customer["phone"],
        "wilaya": customer["wilaya"],
        "commune": customer["commune"],
        "address": customer["address"],
        "delivery_type": customer["delivery_type"],
        "campaign": campaign_for(visit.get("utm_campaign")),
        "note": note,
    }
    with transaction.atomic():
        order = create_order(data, None, source=Source.WEBSITE, event_message="وصلت الطلبية من الموقع")
        OrderOrigin.objects.create(
            order=order,
            ip=ip,
            user_agent=clean_text(request.META.get("HTTP_USER_AGENT"), 255),
            lang=lang,
            utm_source=visit.get("utm_source", ""),
            utm_medium=visit.get("utm_medium", ""),
            utm_campaign=visit.get("utm_campaign", ""),
            referrer=visit.get("referrer", ""),
        )
    for product_id in needed:
        forget_stock(product_id)
    return order, False
