import json
from datetime import timedelta

from django.core.cache import cache
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from apps.core.models import BusinessProfile, Commune, Wilaya
from apps.orders.models import Order, OrderStatus
from apps.shipping.models import Stage

from . import catalog
from .models import ContactMessage, Review, StockAlert, StoreOffer, StorePage, StoreSettings
from .ordering import StoreError, bot_check, clean_customer, clean_phone, clean_text, client_ip, first_touch, form_stamp, place_order
from .text import LANGUAGES, pick, text_for

CART_LIMIT = 10
TRACK_LIMIT = 20
TRACK_WINDOW = 600
CONTACT_LIMIT = 5


def other_lang(lang):
    return "en" if lang == "ar" else "ar"


def store_url(lang, name, *args):
    return reverse(f"{lang}:{name}", args=args)


def alternate_url(request, lang):
    match = request.resolver_match
    target = other_lang(lang)
    if match is None or not match.url_name:
        return store_url(target, "home")
    kwargs = {key: value for key, value in match.kwargs.items() if key != "lang"}
    try:
        return reverse(f"{target}:{match.url_name}", kwargs=kwargs)
    except NoReverseMatch:
        return store_url(target, "home")


def opening_hours(profile):
    for ranges in (profile.working_hours or {}).values():
        if ranges:
            return " · ".join(f"{start.lstrip('0')} – {end.lstrip('0')}" for start, end in ranges)
    return ""


def phone_digits(value):
    return "".join(character for character in str(value or "") if character.isdigit())


def cart_lines(request):
    raw = request.session.get("cart") or []
    return [row for row in raw if isinstance(row, list) and len(row) == 3][:CART_LIMIT]


def cart_count(request):
    return sum(row[2] for row in cart_lines(request))


def wilaya_rows(lang):
    rows = []
    for wilaya in Wilaya.objects.all():
        rows.append(
            {
                "code": wilaya.code,
                "name": wilaya.name_ar if lang == "ar" else (wilaya.name_fr or wilaya.name_ar),
                "home": wilaya.home_fee if wilaya.home_available else None,
                "desk": wilaya.desk_fee if wilaya.desk_available else None,
            }
        )
    return rows


def base_context(request, lang, **extra):
    t = text_for(lang)
    settings = StoreSettings.load()
    profile = BusinessProfile.load()
    absolute = request.build_absolute_uri(request.path)
    context = {
        "lang": lang,
        "t": t,
        "store": settings,
        "profile": profile,
        "hours": opening_hours(profile),
        "phone_link": "+" + phone_digits(profile.phone) if profile.phone else "",
        "whatsapp_link": f"https://wa.me/{phone_digits(settings.whatsapp)}" if settings.whatsapp else "",
        "announcement": pick(settings, "announcement", lang),
        "alt_url": alternate_url(request, lang),
        "canonical": absolute,
        "home_ar": request.build_absolute_uri(store_url("ar", "home")),
        "cart_count": cart_count(request),
        "footer_pages": [{"slug": page.slug, "title": pick(page, "title", lang)} for page in StorePage.objects.filter(show_in_footer=True)],
        "stamp": form_stamp(),
        "page_title": "",
        "page_description": t["meta_description"],
        "og_image": request.build_absolute_uri("/static/storefront/img/og.jpg"),
        "noindex": False,
    }
    context["alt_absolute"] = request.build_absolute_uri(context["alt_url"])
    context.update(extra)
    return context


def page_response(request, lang, template, status=200, **extra):
    first_touch(request)
    response = render(request, f"storefront/{template}", base_context(request, lang, **extra), status=status)
    response["Content-Language"] = lang
    return response


def check_lang(lang):
    if lang not in LANGUAGES:
        raise Http404


@require_GET
@csrf_protect
def home(request, lang):
    check_lang(lang)
    cards = catalog.cards(lang)
    cards.sort(key=lambda row: (not row["is_featured"],))
    reviews = Review.objects.filter(is_published=True)[:6]
    organization = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "Vizora Global",
        "url": request.build_absolute_uri("/"),
        "logo": request.build_absolute_uri("/static/storefront/img/logo-512.png"),
        "telephone": BusinessProfile.load().phone,
    }
    return page_response(
        request,
        lang,
        "home.html",
        nav="home",
        cards=cards,
        reviews=reviews,
        contact_sent=request.GET.get("sent") == "1",
        contact_errors={},
        contact_values={},
        structured=json.dumps(organization, ensure_ascii=False),
    )


@require_GET
def product_list(request, lang):
    check_lang(lang)
    t = text_for(lang)
    return page_response(request, lang, "products.html", nav="products", cards=catalog.cards(lang), page_title=t["products_title"])


def product_schema(request, item, lang):
    availability = "https://schema.org/InStock" if item["orderable"] else "https://schema.org/OutOfStock"
    offers = {
        "@type": "AggregateOffer",
        "priceCurrency": "DZD",
        "lowPrice": item["price"],
        "highPrice": item["max_price"],
        "availability": availability,
    }
    data = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": item["title"],
        "description": item["summary"] or item["description"][:300],
        "brand": {"@type": "Brand", "name": "Vizora Global"},
        "offers": offers,
        "url": request.build_absolute_uri(request.path),
    }
    if item["images"]:
        data["image"] = [request.build_absolute_uri(image["url"]) for image in item["images"]]
    return json.dumps(data, ensure_ascii=False)


def parse_quantity(value, item):
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        return None
    if quantity < 1 or quantity > item["max_quantity"]:
        return None
    return quantity


def chosen_offer(store_product, value):
    offers = list(store_product.offers.all())
    if not offers:
        return None, True
    try:
        offer_id = int(value)
    except (TypeError, ValueError):
        return None, False
    for offer in offers:
        if offer.pk == offer_id:
            return offer, True
    return None, False


def product_context(request, store_product, lang, **extra):
    item = catalog.card(store_product, lang)
    related = [row for row in catalog.cards(lang) if row["id"] != item["id"]][:3]
    image = item["images"][0]["url"] if item["images"] else None
    return dict(
        item=item,
        related=related,
        wilayas=wilaya_rows(lang),
        wilayas_json=json.dumps(wilaya_rows(lang), ensure_ascii=False),
        structured=product_schema(request, item, lang),
        page_title=item["title"],
        page_description=item["summary"] or text_for(lang)["meta_description"],
        og_image=request.build_absolute_uri(image) if image else request.build_absolute_uri("/static/storefront/img/og.jpg"),
        errors={},
        values={"quantity": 1, "delivery_type": "home", "offer": item["offers"][0]["id"] if item["offers"] else ""},
        notified=request.GET.get("notified") == "1",
        nav="products",
        **extra,
    )


def remember_order(request, order):
    orders = request.session.get("orders") or []
    if order.reference not in orders:
        orders = (orders + [order.reference])[-10:]
    request.session["orders"] = orders


@csrf_protect
def product_detail(request, lang, slug):
    check_lang(lang)
    store_product = catalog.find(slug)
    if store_product is None:
        raise Http404
    if request.method != "POST":
        return page_response(request, lang, "product.html", **product_context(request, store_product, lang))
    t = text_for(lang)
    verdict = bot_check(request.POST)
    if verdict == "bot":
        return HttpResponseRedirect(store_url(lang, "home"))
    item = catalog.card(store_product, lang)
    customer, values, errors = clean_customer(request.POST, t)
    offer, offer_ok = chosen_offer(store_product, request.POST.get("offer"))
    if not offer_ok:
        errors["offer"] = t["err_offer"]
    quantity = parse_quantity(request.POST.get("quantity"), item)
    if quantity is None:
        errors["quantity"] = t["err_quantity"]
    values["offer"] = request.POST.get("offer", "")
    values["quantity"] = request.POST.get("quantity", "1")
    if verdict == "expired":
        errors["form"] = t["err_expired"]
    if not errors:
        try:
            order, existing = place_order([{"store_product": store_product, "offer": offer, "quantity": quantity}], customer, request, lang)
        except StoreError as error:
            errors["form"] = t.get(error.key, t["err_form"])
        else:
            remember_order(request, order)
            target = store_url(lang, "thanks", order.reference)
            return HttpResponseRedirect(f"{target}?existing=1" if existing else target)
    errors.setdefault("form", t["err_form"])
    context = product_context(request, store_product, lang)
    context.update(errors=errors, values=values)
    return page_response(request, lang, "product.html", status=422, **context)


def cart_rows(request, lang):
    rows = []
    for store_product_id, offer_id, quantity in cart_lines(request):
        store_product = catalog.published().filter(pk=store_product_id).first()
        if store_product is None:
            continue
        offer = None
        if offer_id:
            offer = StoreOffer.objects.filter(pk=offer_id, store_product=store_product, is_active=True).first()
            if offer is None:
                continue
        item = catalog.card(store_product, lang)
        price = offer.price if offer is not None else store_product.product.price
        rows.append(
            {
                "key": f"{store_product_id}-{offer_id}",
                "store_product": store_product,
                "offer": offer,
                "item": item,
                "offer_title": pick(offer, "title", lang) if offer is not None else "",
                "price": price,
                "quantity": quantity,
                "line_total": price * quantity,
            }
        )
    return rows


def save_cart(request, rows):
    request.session["cart"] = [[row[0], row[1], row[2]] for row in rows if row[2] > 0][:CART_LIMIT]


@require_POST
@csrf_protect
def cart_add(request, lang):
    check_lang(lang)
    raw = str(request.POST.get("product", ""))[:12]
    store_product = catalog.published().filter(pk=int(raw)).first() if raw.isdigit() else None
    if store_product is None:
        raise Http404
    item = catalog.card(store_product, lang)
    offer, offer_ok = chosen_offer(store_product, request.POST.get("offer"))
    quantity = parse_quantity(request.POST.get("quantity"), item) or 1
    if not offer_ok or not item["orderable"]:
        return HttpResponseRedirect(store_url(lang, "product", store_product.slug))
    offer_id = offer.pk if offer is not None else 0
    rows = cart_lines(request)
    for row in rows:
        if row[0] == store_product.pk and row[1] == offer_id:
            row[2] = min(row[2] + quantity, item["max_quantity"])
            break
    else:
        rows.append([store_product.pk, offer_id, quantity])
    save_cart(request, rows)
    return HttpResponseRedirect(store_url(lang, "cart") + "?added=1")


@require_POST
@csrf_protect
def cart_update(request, lang):
    check_lang(lang)
    rows = []
    for store_product_id, offer_id, quantity in cart_lines(request):
        key = f"{store_product_id}-{offer_id}"
        if request.POST.get("remove") == key:
            continue
        try:
            quantity = int(request.POST.get(f"qty-{key}", quantity))
        except ValueError:
            pass
        rows.append([store_product_id, offer_id, max(0, min(quantity, 10))])
    save_cart(request, rows)
    return HttpResponseRedirect(store_url(lang, "cart"))


@require_GET
@csrf_protect
def cart_view(request, lang):
    check_lang(lang)
    t = text_for(lang)
    rows = cart_rows(request, lang)
    return page_response(
        request,
        lang,
        "cart.html",
        rows=rows,
        subtotal=sum(row["line_total"] for row in rows),
        added=request.GET.get("added") == "1",
        page_title=t["cart_title"],
        noindex=True,
    )


@csrf_protect
def checkout(request, lang):
    check_lang(lang)
    t = text_for(lang)
    rows = cart_rows(request, lang)
    if not rows:
        return HttpResponseRedirect(store_url(lang, "cart"))
    subtotal = sum(row["line_total"] for row in rows)
    extra = {
        "rows": rows,
        "subtotal": subtotal,
        "wilayas": wilaya_rows(lang),
        "wilayas_json": json.dumps(wilaya_rows(lang), ensure_ascii=False),
        "page_title": t["checkout_title"],
        "noindex": True,
        "errors": {},
        "values": {"delivery_type": "home"},
    }
    if request.method != "POST":
        return page_response(request, lang, "checkout.html", **extra)
    verdict = bot_check(request.POST)
    if verdict == "bot":
        return HttpResponseRedirect(store_url(lang, "home"))
    customer, values, errors = clean_customer(request.POST, t)
    if verdict == "expired":
        errors["form"] = t["err_expired"]
    for row in rows:
        if row["quantity"] > row["item"]["available"]:
            errors["form"] = t["err_stock"]
    if not errors:
        lines = [{"store_product": row["store_product"], "offer": row["offer"], "quantity": row["quantity"]} for row in rows]
        try:
            order, existing = place_order(lines, customer, request, lang)
        except StoreError as error:
            errors["form"] = t.get(error.key, t["err_form"])
        else:
            remember_order(request, order)
            request.session["cart"] = []
            target = store_url(lang, "thanks", order.reference)
            return HttpResponseRedirect(f"{target}?existing=1" if existing else target)
    errors.setdefault("form", t["err_form"])
    extra.update(errors=errors, values=values)
    return page_response(request, lang, "checkout.html", status=422, **extra)


def order_lines(order, lang):
    rows = []
    for line in order.items.select_related("product").all():
        title = line.product.name
        store_product = getattr(line.product, "store", None)
        if store_product is not None:
            title = pick(store_product, "title", lang)
        rows.append({"title": title, "quantity": line.quantity, "total": line.unit_price * line.quantity})
    return rows


@require_GET
def thanks(request, lang, reference):
    check_lang(lang)
    if reference not in (request.session.get("orders") or []):
        return HttpResponseRedirect(store_url(lang, "track"))
    order = Order.objects.filter(reference=reference).select_related("wilaya").first()
    if order is None:
        raise Http404
    tracked = request.session.get("tracked") or []
    fire_purchase = reference not in tracked and request.GET.get("existing") != "1"
    if fire_purchase:
        request.session["tracked"] = (tracked + [reference])[-10:]
    t = text_for(lang)
    return page_response(
        request,
        lang,
        "thanks.html",
        order=order,
        lines=order_lines(order, lang),
        existing=request.GET.get("existing") == "1",
        fire_purchase=fire_purchase,
        thanks_text=t["thanks_text"].format(hours=opening_hours(BusinessProfile.load())),
        page_title=t["thanks_title"],
        noindex=True,
    )


def timeline(order, t):
    steps = [
        {"label": t["step_received"], "at": order.created_at},
        {"label": t["step_confirmed"], "at": order.confirmed_at},
        {"label": t["step_shipping"], "at": order.shipped_at},
        {"label": t["step_delivered"], "at": order.delivered_at},
    ]
    reached = {
        OrderStatus.PENDING: 0,
        OrderStatus.CONFIRMED: 1,
        OrderStatus.SHIPPING: 2,
        OrderStatus.DELIVERED: 3,
    }.get(order.status, 0)
    for index, step in enumerate(steps):
        step["state"] = "done" if index < reached or (index == reached and order.status == OrderStatus.DELIVERED) else ("current" if index == reached else "todo")
    return steps


STAGE_EN = {
    "transit": "In transit",
    "out": "Out for delivery",
    "desk": "Waiting at the delivery desk",
    "failed": "Delivery attempt failed",
    "delivered": "Delivered",
    "returned": "Returned",
}


def stage_label(stage, lang):
    if lang == "en":
        return STAGE_EN.get(stage, stage)
    try:
        return Stage(stage).label
    except ValueError:
        return stage


def find_order(reference, phone):
    digits = phone_digits(reference)
    if not digits or phone is None:
        return None
    return Order.objects.filter(reference=f"VZ-{digits}", phone=phone).select_related("wilaya").first()


@csrf_protect
def track(request, lang):
    check_lang(lang)
    t = text_for(lang)
    extra = {"nav": "track", "page_title": t["track_title"], "noindex": True, "looked_up": False, "result": None, "values": {}}
    if request.method == "POST":
        key = f"store-track-{client_ip(request)}"
        attempts = cache.get(key, 0)
        cache.set(key, attempts + 1, TRACK_WINDOW)
        reference = clean_text(request.POST.get("reference"), 30)
        phone = clean_phone(request.POST.get("phone"))
        extra["values"] = {"reference": reference, "phone": clean_text(request.POST.get("phone"), 20)}
        extra["looked_up"] = True
        order = None if attempts >= TRACK_LIMIT else find_order(reference, phone)
        if order is not None:
            shipment = getattr(order, "shipment", None)
            extra["result"] = {
                "order": order,
                "steps": timeline(order, t),
                "cancelled": order.status == OrderStatus.CANCELLED,
                "returned": order.status == OrderStatus.RETURNED,
                "tracking_number": shipment.tracking_number if shipment else "",
                "stage": stage_label(shipment.stage, lang) if shipment else "",
            }
    return page_response(request, lang, "track.html", **extra)


@require_POST
@csrf_protect
def contact(request, lang):
    check_lang(lang)
    t = text_for(lang)
    verdict = bot_check(request.POST)
    home_url = store_url(lang, "home")
    if verdict == "bot":
        return HttpResponseRedirect(home_url)
    values = {
        "name": clean_text(request.POST.get("name"), 120),
        "phone": clean_text(request.POST.get("phone"), 20),
        "message": str(request.POST.get("message", "")).strip()[:2000],
    }
    errors = {}
    if len(values["name"]) < 2:
        errors["name"] = t["err_name"]
    phone = clean_phone(values["phone"])
    if phone is None:
        errors["phone"] = t["err_phone"]
    if len(values["message"]) < 3:
        errors["message"] = t["err_message"]
    if verdict == "expired":
        errors["form"] = t["err_expired"]
    ip = client_ip(request)
    if ip and ContactMessage.objects.filter(ip=ip, created_at__gte=timezone.now() - timedelta(hours=1)).count() >= CONTACT_LIMIT:
        errors["form"] = t["err_rate"]
    if errors:
        cards = catalog.cards(lang)
        return page_response(
            request,
            lang,
            "home.html",
            status=422,
            cards=cards,
            reviews=Review.objects.filter(is_published=True)[:6],
            contact_sent=False,
            contact_errors=errors,
            contact_values=values,
            structured="",
        )
    ContactMessage.objects.create(name=values["name"], phone=phone, message=values["message"], lang=lang, ip=ip)
    return HttpResponseRedirect(f"{home_url}?sent=1#contact")


@require_POST
@csrf_protect
def stock_alert(request, lang, slug):
    check_lang(lang)
    store_product = catalog.find(slug)
    if store_product is None:
        raise Http404
    target = store_url(lang, "product", slug)
    if bot_check(request.POST) == "bot":
        return HttpResponseRedirect(target)
    phone = clean_phone(request.POST.get("phone"))
    if phone is None:
        return HttpResponseRedirect(target)
    StockAlert.objects.get_or_create(store_product=store_product, phone=phone, defaults={"lang": lang})
    return HttpResponseRedirect(f"{target}?notified=1")


@require_GET
def page(request, lang, slug):
    check_lang(lang)
    entry = StorePage.objects.filter(slug=slug).first()
    if entry is None:
        raise Http404
    return page_response(
        request,
        lang,
        "page.html",
        entry={"title": pick(entry, "title", lang), "body": pick(entry, "body", lang), "updated_at": entry.updated_at},
        page_title=pick(entry, "title", lang),
    )


@require_GET
def communes_json(request, code):
    rows = [{"ar": commune.name_ar, "fr": commune.name_fr or commune.name_ar} for commune in Commune.objects.filter(wilaya_id=code)]
    response = JsonResponse({"communes": rows})
    response["Cache-Control"] = "public, max-age=3600"
    return response


@require_GET
def sitemap(request):
    entries = []
    names = [("home", ()), ("products", ())]
    names += [("product", (item.slug,)) for item in catalog.published()]
    names += [("page", (entry.slug,)) for entry in StorePage.objects.all()]
    for name, args in names:
        urls = {lang: request.build_absolute_uri(store_url(lang, name, *args)) for lang in LANGUAGES}
        entries.append(urls)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">']
    for urls in entries:
        for lang in LANGUAGES:
            lines.append(f"<url><loc>{urls[lang]}</loc>")
            for alt in LANGUAGES:
                lines.append(f'<xhtml:link rel="alternate" hreflang="{alt}" href="{urls[alt]}"/>')
            lines.append("</url>")
    lines.append("</urlset>")
    return HttpResponse("\n".join(lines), content_type="application/xml")


@require_GET
def robots(request):
    body = "\n".join(
        [
            "User-agent: *",
            "Disallow: /cart/",
            "Disallow: /checkout/",
            "Disallow: /thanks/",
            "Disallow: /track/",
            "Disallow: /en/cart/",
            "Disallow: /en/checkout/",
            "Disallow: /en/thanks/",
            "Disallow: /en/track/",
            f"Sitemap: {request.build_absolute_uri('/sitemap.xml')}",
            "",
        ]
    )
    return HttpResponse(body, content_type="text/plain")


def request_lang(request):
    return "en" if request.path.startswith("/en/") else "ar"


def not_found(request, exception=None):
    lang = request_lang(request)
    return render(request, "storefront/404.html", base_context(request, lang, page_title=text_for(lang)["not_found_title"], noindex=True), status=404)


def csrf_failure(request, reason=""):
    lang = request_lang(request)
    t = text_for(lang)
    if getattr(request, "urlconf", None) != "config.store_urls":
        return JsonResponse({"detail": "CSRF verification failed"}, status=403)
    back = request.META.get("HTTP_REFERER") or "/"
    if not back.startswith(request.build_absolute_uri("/")):
        back = "/"
    return render(
        request,
        "storefront/404.html",
        base_context(request, lang, page_title=t["err_expired"], noindex=True, expired=True, back=back),
        status=403,
    )
