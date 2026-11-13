from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import BusinessProfile, Commune, Wilaya
from apps.orders.models import Order, OrderStatus
from apps.shipping.models import Stage

from . import catalog
from .models import ContactMessage, Review, StockAlert, StorePage, StoreSettings
from .ordering import StoreError, bot_check, clean_customer, clean_phone, clean_text, client_ip, form_stamp, place_order
from .templatetags.store import illustration, illustration_path
from .text import LANGUAGES, pick, text_for

TRACK_LIMIT = 20
TRACK_WINDOW = 600
CONTACT_LIMIT = 5
CART_LIMIT = 10
ILLUSTRATIONS = (
    "hero-1", "hero-2", "hero-3", "card-hero", "service-topup", "service-games", "feature-delivery", "feature-cod",
    "feature-guarantee", "step-1-order", "step-2-call", "step-3-ship", "step-4-receive", "promo", "thank-you", "track",
    "empty-cart", "not-found", "contact", "bg-shapes",
)
STAGE_EN = {
    "transit": "In transit",
    "out": "Out for delivery",
    "desk": "Waiting at the delivery desk",
    "failed": "Delivery attempt failed",
    "delivered": "Delivered",
    "returned": "Returned",
}


def lang_of(request):
    lang = request.query_params.get("lang") or (request.data.get("lang") if isinstance(request.data, dict) else None)
    return lang if lang in LANGUAGES else "ar"


def phone_digits(value):
    return "".join(character for character in str(value or "") if character.isdigit())


def opening_hours(profile):
    for ranges in (profile.working_hours or {}).values():
        if ranges:
            return " · ".join(f"{start.lstrip('0')} – {end.lstrip('0')}" for start, end in ranges)
    return ""


def failure(errors, status=422):
    return Response({"detail": errors.get("form") or next(iter(errors.values())), "errors": errors}, status=status)


def shop_config(lang):
    settings = StoreSettings.load()
    profile = BusinessProfile.load()
    return {
        "lang": lang,
        "stamp": form_stamp(),
        "store": {
            "is_open": settings.is_open,
            "whatsapp_link": f"https://wa.me/{phone_digits(settings.whatsapp)}" if settings.whatsapp else "",
            "facebook_url": settings.facebook_url,
            "instagram_url": settings.instagram_url,
            "tiktok_url": settings.tiktok_url,
            "announcement": pick(settings, "announcement", lang),
            "delivery_days": pick(settings, "delivery_days", lang),
            "facebook_pixel": settings.facebook_pixel,
            "tiktok_pixel": settings.tiktok_pixel,
            "google_analytics": settings.google_analytics,
        },
        "business": {
            "name": profile.name,
            "phone": profile.phone,
            "phone_link": "+" + phone_digits(profile.phone) if profile.phone else "",
            "email": profile.email,
            "address": profile.address,
            "hours": opening_hours(profile),
        },
        "pages": [{"slug": page.slug, "title": pick(page, "title", lang)} for page in StorePage.objects.filter(show_in_footer=True)],
        "reviews": [
            {"id": review.pk, "name": review.name, "wilaya": review.wilaya, "text": review.text, "rating": review.rating}
            for review in Review.objects.filter(is_published=True)[:6]
        ],
        "illustrations": {name: illustration(name) for name in ILLUSTRATIONS if illustration_path(name)},
    }


class PublicView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]


class ShopConfigView(PublicView):
    def get(self, request):
        return Response(shop_config(lang_of(request)))


class ShopProductsView(PublicView):
    def get(self, request):
        return Response(catalog.cards(lang_of(request)))


class ShopProductView(PublicView):
    def get(self, request, slug):
        lang = lang_of(request)
        item = catalog.find(slug)
        if item is None:
            return Response({"detail": text_for(lang)["not_found_title"]}, status=404)
        card = catalog.card(item, lang)
        card["related"] = [row for row in catalog.cards(lang) if row["id"] != card["id"]][:3]
        return Response(card)


class ShopWilayasView(PublicView):
    def get(self, request):
        lang = lang_of(request)
        rows = [
            {
                "code": wilaya.code,
                "name": wilaya.name_ar if lang == "ar" else (wilaya.name_fr or wilaya.name_ar),
                "home": wilaya.home_fee if wilaya.home_available else None,
                "desk": wilaya.desk_fee if wilaya.desk_available else None,
            }
            for wilaya in Wilaya.objects.all()
        ]
        return Response(rows)


class ShopCommunesView(PublicView):
    def get(self, request, code):
        return Response([{"ar": commune.name_ar, "fr": commune.name_fr or commune.name_ar} for commune in Commune.objects.filter(wilaya_id=code)])


class ShopPageView(PublicView):
    def get(self, request, slug):
        lang = lang_of(request)
        entry = StorePage.objects.filter(slug=slug).first()
        if entry is None:
            return Response({"detail": text_for(lang)["not_found_title"]}, status=404)
        return Response({"slug": entry.slug, "title": pick(entry, "title", lang), "body": pick(entry, "body", lang), "updated_at": entry.updated_at})


def order_lines(order, lang):
    rows = []
    for line in order.items.select_related("product").all():
        title = line.product.name
        store_product = getattr(line.product, "store", None)
        if store_product is not None:
            title = pick(store_product, "title", lang)
        rows.append({"title": title, "quantity": line.quantity, "total": line.unit_price * line.quantity})
    return rows


def parse_lines(raw, t):
    if not isinstance(raw, list) or not raw or len(raw) > CART_LIMIT:
        return None, t["err_quantity"]
    lines = []
    for row in raw:
        if not isinstance(row, dict):
            return None, t["err_quantity"]
        item = catalog.find(str(row.get("product", ""))[:80])
        if item is None:
            return None, t["err_stock"]
        offers = list(item.offers.all())
        offer = None
        if offers:
            offer = next((entry for entry in offers if str(entry.pk) == str(row.get("offer"))), None)
            if offer is None:
                return None, t["err_offer"]
        try:
            quantity = int(row.get("quantity", 1))
        except (TypeError, ValueError):
            return None, t["err_quantity"]
        if quantity < 1 or quantity > item.max_quantity:
            return None, t["err_quantity"]
        lines.append({"store_product": item, "offer": offer, "quantity": quantity})
    return lines, None


class ShopOrderView(PublicView):
    def post(self, request):
        lang = lang_of(request)
        t = text_for(lang)
        data = request.data if isinstance(request.data, dict) else {}
        verdict = bot_check(data)
        if verdict == "bot":
            return Response({"detail": t["err_form"], "errors": {"form": t["err_form"]}}, status=422)
        customer, _, errors = clean_customer(data, t)
        lines, problem = parse_lines(data.get("lines"), t)
        if problem:
            errors["lines"] = problem
        if verdict == "expired":
            errors["form"] = t["err_expired"]
        if errors:
            errors.setdefault("form", t["err_form"])
            return failure(errors)
        try:
            order, existing = place_order(lines, customer, request, lang, visit=data.get("visit"))
        except StoreError as error:
            return failure({"form": t.get(error.key, t["err_form"])})
        return Response(
            {
                "reference": order.reference,
                "existing": existing,
                "lines": order_lines(order, lang),
                "subtotal": order.subtotal,
                "delivery_fee": order.delivery_fee,
                "total": order.total,
            },
            status=200 if existing else 201,
        )


def stage_label(stage, lang):
    if lang == "en":
        return STAGE_EN.get(stage, stage)
    try:
        return Stage(stage).label
    except ValueError:
        return stage


def timeline(order, t):
    steps = [
        {"label": t["step_received"], "at": order.created_at},
        {"label": t["step_confirmed"], "at": order.confirmed_at},
        {"label": t["step_shipping"], "at": order.shipped_at},
        {"label": t["step_delivered"], "at": order.delivered_at},
    ]
    reached = {OrderStatus.PENDING: 0, OrderStatus.CONFIRMED: 1, OrderStatus.SHIPPING: 2, OrderStatus.DELIVERED: 3}.get(order.status, 0)
    for index, step in enumerate(steps):
        if index < reached or (index == reached and order.status == OrderStatus.DELIVERED):
            step["state"] = "done"
        elif index == reached:
            step["state"] = "current"
        else:
            step["state"] = "todo"
    return steps


class ShopTrackView(PublicView):
    def post(self, request):
        lang = lang_of(request)
        t = text_for(lang)
        key = f"store-track-{client_ip(request)}"
        attempts = cache.get(key, 0)
        cache.set(key, attempts + 1, TRACK_WINDOW)
        digits = phone_digits(request.data.get("reference"))
        phone = clean_phone(request.data.get("phone"))
        order = None
        if attempts < TRACK_LIMIT and digits and phone:
            order = Order.objects.filter(reference=f"VZ-{digits}", phone=phone).first()
        if order is None:
            return Response({"detail": t["track_not_found"]}, status=404)
        shipment = getattr(order, "shipment", None)
        return Response(
            {
                "reference": order.reference,
                "status": order.status,
                "cancelled": order.status == OrderStatus.CANCELLED,
                "returned": order.status == OrderStatus.RETURNED,
                "steps": timeline(order, t),
                "tracking_number": shipment.tracking_number if shipment else "",
                "stage": stage_label(shipment.stage, lang) if shipment else "",
            }
        )


class ShopContactView(PublicView):
    def post(self, request):
        lang = lang_of(request)
        t = text_for(lang)
        data = request.data if isinstance(request.data, dict) else {}
        verdict = bot_check(data)
        if verdict == "bot":
            return Response({"sent": True}, status=201)
        name = clean_text(data.get("name"), 120)
        message = str(data.get("message") or "").strip()[:2000]
        phone = clean_phone(data.get("phone"))
        errors = {}
        if len(name) < 2:
            errors["name"] = t["err_name"]
        if phone is None:
            errors["phone"] = t["err_phone"]
        if len(message) < 3:
            errors["message"] = t["err_message"]
        if verdict == "expired":
            errors["form"] = t["err_expired"]
        ip = client_ip(request)
        if ip and ContactMessage.objects.filter(ip=ip, created_at__gte=timezone.now() - timedelta(hours=1)).count() >= CONTACT_LIMIT:
            errors["form"] = t["err_rate"]
        if errors:
            return failure(errors)
        ContactMessage.objects.create(name=name, phone=phone, message=message, lang=lang, ip=ip)
        return Response({"sent": True}, status=201)


class ShopNotifyView(PublicView):
    def post(self, request, slug):
        lang = lang_of(request)
        t = text_for(lang)
        item = catalog.find(slug)
        if item is None:
            return Response({"detail": t["not_found_title"]}, status=404)
        if bot_check(request.data if isinstance(request.data, dict) else {}) == "bot":
            return Response({"saved": True}, status=201)
        phone = clean_phone(request.data.get("phone"))
        if phone is None:
            return failure({"phone": t["err_phone"]})
        StockAlert.objects.get_or_create(store_product=item, phone=phone, defaults={"lang": lang})
        return Response({"saved": True}, status=201)

