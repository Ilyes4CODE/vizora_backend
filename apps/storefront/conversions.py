import hashlib
import logging
import re
import threading
import time
import unicodedata
from datetime import timedelta

import requests
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.core.models import Commune
from apps.orders.models import Order, OrderStatus, Source

from .models import OrderOrigin, PixelEvent, PurchaseMoment, StoreSettings
from .serializers import public_base

logger = logging.getLogger(__name__)

TIMEOUT = 10
RETRY_DAYS = 7
REPORT_DAYS = 30
RECENT_ROWS = 25
CURRENCY = "DZD"
SERVER_PLATFORMS = ("facebook", "tiktok")
PURCHASE = {"facebook": "Purchase", "tiktok": "CompletePayment", "google": "purchase"}
LEAD = {"facebook": "Lead", "tiktok": "PlaceAnOrder", "google": "generate_lead"}
LABELS = {"facebook": "Facebook", "tiktok": "TikTok", "google": "Google Analytics"}


def sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def digits(value):
    return re.sub(r"\D", "", str(value or ""))


def international(phone):
    number = digits(phone)
    if number.startswith("0"):
        number = "213" + number[1:]
    return number


def latin(value):
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", text.lower())


def name_part(value):
    return re.sub(r"[\W_]+", "", str(value or "").lower())


def server_ready(store, platform):
    if platform == "facebook":
        return bool(store.facebook_pixel and store.encrypted_facebook_token)
    if platform == "tiktok":
        return bool(store.tiktok_pixel and store.encrypted_tiktok_token)
    return False


def ready_platforms(store):
    return [platform for platform in SERVER_PLATFORMS if server_ready(store, platform)]


def browser_events(store):
    later = store.purchase_moment != PurchaseMoment.PLACED
    return {
        "facebook": LEAD["facebook"] if later and server_ready(store, "facebook") else PURCHASE["facebook"],
        "tiktok": LEAD["tiktok"] if later and server_ready(store, "tiktok") else PURCHASE["tiktok"],
        "google": LEAD["google"] if later else PURCHASE["google"],
    }


def content_id(product):
    store_product = getattr(product, "store", None)
    return store_product.slug if store_product is not None else str(product.pk)


def order_facts(order, origin):
    words = order.customer_name.split()
    commune = Commune.objects.filter(wilaya=order.wilaya, name_ar=order.commune).first()
    contents = []
    for item in order.items.select_related("product", "product__store"):
        store_product = getattr(item.product, "store", None)
        contents.append(
            {
                "id": content_id(item.product),
                "title": store_product.title_ar if store_product is not None else item.product.name,
                "quantity": item.quantity,
                "price": item.unit_price,
            }
        )
    return {
        "event_id": order.reference,
        "order_id": order.reference,
        "value": order.subtotal,
        "contents": contents,
        "phone": international(order.phone),
        "first_name": name_part(words[0]) if words else "",
        "last_name": name_part(words[-1]) if len(words) > 1 else "",
        "city": latin(commune.name_fr if commune and commune.name_fr else order.commune),
        "state": latin(order.wilaya.name_fr),
        "ip": origin.ip or "",
        "user_agent": origin.user_agent,
        "fbp": origin.fbp,
        "fbc": origin.fbc,
        "ttp": origin.ttp,
        "ttclid": origin.ttclid,
        "url": origin.page_url,
        "referrer": origin.referrer,
    }


def test_facts(request):
    stamp = timezone.now().strftime("%Y%m%d%H%M%S")
    return {
        "event_id": f"test-{stamp}",
        "order_id": f"test-{stamp}",
        "value": 7900,
        "contents": [{"id": "visa-card", "title": "Test", "quantity": 1, "price": 7900}],
        "phone": "213555000000",
        "first_name": "test",
        "last_name": "",
        "city": "alger",
        "state": "alger",
        "ip": request.META.get("REMOTE_ADDR") or "",
        "user_agent": request.META.get("HTTP_USER_AGENT", "")[:255],
        "fbp": "",
        "fbc": "",
        "ttp": "",
        "ttclid": "",
        "url": f"{public_base()}/",
        "referrer": "",
    }


def without_empty(data):
    return {key: value for key, value in data.items() if value not in ("", None, [], [""])}


def facebook_payload(name, facts):
    user = without_empty(
        {
            "ph": [sha(facts["phone"])],
            "external_id": [sha(facts["phone"])],
            "fn": [sha(facts["first_name"])],
            "ln": [sha(facts["last_name"])],
            "ct": [sha(facts["city"])],
            "st": [sha(facts["state"])],
            "country": [sha("dz")],
            "client_ip_address": facts["ip"],
            "client_user_agent": facts["user_agent"],
            "fbp": facts["fbp"],
            "fbc": facts["fbc"],
        }
    )
    custom = {
        "currency": CURRENCY,
        "value": facts["value"],
        "order_id": facts["order_id"],
        "content_type": "product",
        "content_ids": [row["id"] for row in facts["contents"]],
        "contents": [{"id": row["id"], "quantity": row["quantity"], "item_price": row["price"]} for row in facts["contents"]],
        "num_items": sum(row["quantity"] for row in facts["contents"]),
    }
    return {
        "event_name": name,
        "event_time": int(time.time()),
        "event_id": facts["event_id"],
        "action_source": "website",
        "event_source_url": facts["url"] or f"{public_base()}/",
        "user_data": user,
        "custom_data": custom,
    }


def tiktok_payload(name, facts):
    user = without_empty(
        {
            "phone": sha("+" + facts["phone"]) if facts["phone"] else "",
            "external_id": sha(facts["phone"]),
            "ip": facts["ip"],
            "user_agent": facts["user_agent"],
            "ttclid": facts["ttclid"],
            "ttp": facts["ttp"],
        }
    )
    return {
        "event": name,
        "event_time": int(time.time()),
        "event_id": facts["event_id"],
        "user": user,
        "page": without_empty({"url": facts["url"] or f"{public_base()}/", "referrer": facts["referrer"]}),
        "properties": {
            "currency": CURRENCY,
            "value": facts["value"],
            "order_id": facts["order_id"],
            "content_type": "product",
            "contents": [
                {"content_id": row["id"], "content_name": row["title"], "quantity": row["quantity"], "price": row["price"]}
                for row in facts["contents"]
            ],
        },
    }


def short(text):
    return str(text or "")[:300]


def post_facebook(store, event, test_code=""):
    body = {"data": [event]}
    if test_code:
        body["test_event_code"] = test_code
    response = requests.post(
        f"{settings.CONVERSIONS_META_URL.rstrip('/')}/{store.facebook_pixel}/events",
        params={"access_token": store.facebook_token},
        json=body,
        timeout=TIMEOUT,
    )
    try:
        answer = response.json()
    except ValueError:
        answer = {}
    if response.status_code == 200 and answer.get("events_received", 0) >= 1:
        return True, "استلم Facebook الحدث"
    error = answer.get("error") or {}
    return False, short(error.get("error_user_msg") or error.get("message") or f"HTTP {response.status_code}")


def post_tiktok(store, event, test_code=""):
    body = {"event_source": "web", "event_source_id": store.tiktok_pixel, "data": [event]}
    if test_code:
        body["test_event_code"] = test_code
    response = requests.post(
        settings.CONVERSIONS_TIKTOK_URL,
        headers={"Access-Token": store.tiktok_token},
        json=body,
        timeout=TIMEOUT,
    )
    try:
        answer = response.json()
    except ValueError:
        answer = {}
    if response.status_code == 200 and answer.get("code") == 0:
        return True, "استلم TikTok الحدث"
    return False, short(answer.get("message") or f"HTTP {response.status_code}")


def deliver(store, platform, name, facts, order=None, test_code=""):
    try:
        if platform == "facebook":
            ok, detail = post_facebook(store, facebook_payload(name, facts), test_code)
        else:
            ok, detail = post_tiktok(store, tiktok_payload(name, facts), test_code)
    except requests.RequestException as error:
        ok, detail = False, short(f"تعذّر الاتصال بـ {LABELS[platform]}: {error.__class__.__name__}")
    PixelEvent.objects.create(
        order=order,
        platform=platform,
        event_name=name,
        event_id=facts["event_id"],
        value=facts["value"],
        status=PixelEvent.Status.SENT if ok else PixelEvent.Status.FAILED,
        is_test=bool(test_code),
        detail=detail,
    )
    return ok, detail


def already_sent(order, platform, name):
    return PixelEvent.objects.filter(order=order, platform=platform, event_name=name, status=PixelEvent.Status.SENT, is_test=False).exists()


def send_for_order(order_id, names):
    order = Order.objects.select_related("wilaya").filter(pk=order_id).first()
    origin = OrderOrigin.objects.filter(order_id=order_id).first()
    if order is None or origin is None:
        return []
    store = StoreSettings.load()
    results = []
    facts = None
    for platform in ready_platforms(store):
        name = names[platform]
        if already_sent(order, platform, name):
            continue
        facts = facts or order_facts(order, origin)
        results.append((platform, name, *deliver(store, platform, name, facts, order=order)))
    return results


def in_background(task, *args):
    if settings.CONVERSIONS_SYNC:
        task(*args)
        return

    def run():
        try:
            task(*args)
        except Exception:
            logger.exception("conversion event failed")
        finally:
            connection.close()

    transaction.on_commit(lambda: threading.Thread(target=run, daemon=True).start())


def placed_task(order_id):
    send_for_order(order_id, browser_events(StoreSettings.load()))


def purchase_task(order_id):
    send_for_order(order_id, PURCHASE)


def order_placed(order):
    if ready_platforms(StoreSettings.load()):
        in_background(placed_task, order.pk)


def purchase_at(moment, order):
    if order.source != Source.WEBSITE:
        return
    store = StoreSettings.load()
    if store.purchase_moment == moment and ready_platforms(store):
        in_background(purchase_task, order.pk)


def on_confirmed(sender, order, **kwargs):
    purchase_at(PurchaseMoment.CONFIRMED, order)


def on_delivered(sender, order, **kwargs):
    purchase_at(PurchaseMoment.DELIVERED, order)


def retryable():
    since = timezone.now() - timedelta(days=RETRY_DAYS)
    failed = PixelEvent.objects.filter(status=PixelEvent.Status.FAILED, is_test=False, order__isnull=False, created_at__gte=since)
    pending = {}
    for row in failed.order_by("created_at"):
        key = (row.order_id, row.platform, row.event_name)
        if key not in pending and not PixelEvent.objects.filter(
            order_id=row.order_id, platform=row.platform, event_name=row.event_name, status=PixelEvent.Status.SENT, is_test=False
        ).exists():
            pending[key] = row
    return list(pending.values())


def retry_failed():
    store = StoreSettings.load()
    sent = failed = 0
    for row in retryable():
        if not server_ready(store, row.platform):
            continue
        order = Order.objects.select_related("wilaya").get(pk=row.order_id)
        origin = OrderOrigin.objects.filter(order=order).first()
        if origin is None:
            continue
        ok, _ = deliver(store, row.platform, row.event_name, order_facts(order, origin), order=order)
        if ok:
            sent += 1
        else:
            failed += 1
    return {"sent": sent, "failed": failed}


def test_code_for(store, platform):
    return store.facebook_test_code if platform == "facebook" else store.tiktok_test_code


def send_test(platform, request):
    store = StoreSettings.load()
    test_code = test_code_for(store, platform)
    return deliver(store, platform, PURCHASE[platform], test_facts(request), test_code=test_code)


def reached(queryset, moment):
    if moment == PurchaseMoment.CONFIRMED:
        return queryset.filter(confirmed_at__isnull=False)
    if moment == PurchaseMoment.DELIVERED:
        return queryset.filter(status=OrderStatus.DELIVERED)
    return queryset


def order_totals(queryset):
    totals = queryset.aggregate(count=Count("id"), value=Sum("subtotal"))
    return {"count": totals["count"] or 0, "value": totals["value"] or 0}


def report():
    store = StoreSettings.load()
    since_start = timezone.now() - timedelta(days=REPORT_DAYS)
    web = Order.objects.filter(source=Source.WEBSITE, created_at__gte=since_start)
    orders = {
        "placed": order_totals(web),
        "confirmed": order_totals(reached(web, PurchaseMoment.CONFIRMED)),
        "delivered": order_totals(reached(web, PurchaseMoment.DELIVERED)),
    }
    events = (
        PixelEvent.objects.filter(created_at__gte=since_start, is_test=False)
        .values("platform", "event_name")
        .annotate(
            sent=Count("id", filter=Q(status=PixelEvent.Status.SENT)),
            failed=Count("id", filter=Q(status=PixelEvent.Status.FAILED)),
            value=Sum("value", filter=Q(status=PixelEvent.Status.SENT)),
        )
        .order_by("platform", "event_name")
    )
    by_platform = {}
    for row in events:
        by_platform.setdefault(row["platform"], []).append(
            {"name": row["event_name"], "sent": row["sent"], "failed": row["failed"], "value": row["value"] or 0}
        )
    pixels = {"facebook": store.facebook_pixel, "tiktok": store.tiktok_pixel, "google": store.google_analytics}
    test_codes = {"facebook": store.facebook_test_code, "tiktok": store.tiktok_test_code, "google": ""}
    names = browser_events(store)
    since = {"facebook": store.facebook_server_since, "tiktok": store.tiktok_server_since, "google": None}
    platforms = []
    for platform in ("facebook", "tiktok", "google"):
        server = server_ready(store, platform)
        purchase_in_browser = names[platform] == PURCHASE[platform]
        moment = PurchaseMoment.PLACED if purchase_in_browser else store.purchase_moment
        expected = None
        if server:
            start = max(since[platform] or since_start, since_start)
            expected = order_totals(reached(Order.objects.filter(source=Source.WEBSITE, created_at__gte=start), moment))
        platforms.append(
            {
                "key": platform,
                "label": LABELS[platform],
                "browser": bool(pixels[platform]),
                "server": server,
                "has_test_code": bool(test_codes[platform]),
                "order_event": names[platform],
                "purchase_event": PURCHASE[platform],
                "purchase_moment": moment,
                "server_since": since[platform] if server else None,
                "expected": expected,
                "events": by_platform.get(platform, []),
            }
        )
    recent = PixelEvent.objects.select_related("order").all()[:RECENT_ROWS]
    return {
        "days": REPORT_DAYS,
        "purchase_moment": store.purchase_moment,
        "orders": orders,
        "platforms": platforms,
        "retryable": len(retryable()),
        "recent": [
            {
                "id": row.pk,
                "platform": row.platform,
                "event_name": row.event_name,
                "event_id": row.event_id,
                "order_reference": row.order.reference if row.order else "",
                "value": row.value,
                "status": row.status,
                "is_test": row.is_test,
                "detail": row.detail,
                "created_at": row.created_at,
            }
            for row in recent
        ],
    }
