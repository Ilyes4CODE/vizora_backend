from django.core.cache import cache
from django.db.models import Prefetch

from apps.shipping.stock import remote_product, reserved_quantity

from .models import StoreImage, StoreOffer, StoreProduct
from .text import pick
from .options import option_rows

STOCK_CACHE_SECONDS = 60
LOW_STOCK = 10


def published():
    return (
        StoreProduct.objects.filter(is_published=True, product__is_active=True)
        .select_related("product")
        .prefetch_related(
            Prefetch("offers", queryset=StoreOffer.objects.filter(is_active=True)),
            Prefetch("images", queryset=StoreImage.objects.all()),
        )
    )


def stock_key(product_id):
    return f"store-stock-{product_id}"


def available_units(product, fresh=False):
    key = stock_key(product.pk)
    if not fresh:
        cached = cache.get(key)
        if cached is not None:
            return cached
    remote = remote_product(product)
    if remote is not None:
        lines = [line for line in remote.stock or [] if isinstance(line, dict)]
        total = sum(int(line.get("available_quantity") or 0) for line in lines)
    else:
        total = product.on_hand
    units = max(total - reserved_quantity(product), 0)
    cache.set(key, units, STOCK_CACHE_SECONDS)
    return units


def forget_stock(product_id):
    cache.delete(stock_key(product_id))


def image_rows(store_product, lang):
    title = pick(store_product, "title", lang)
    return [
        {"url": image.image.url, "width": image.width or 800, "height": image.height or 800, "alt": pick(image, "alt", lang) or title}
        for image in store_product.images.all()
        if image.image
    ]


def offer_rows(store_product, lang):
    return [
        {
            "id": offer.pk,
            "title": pick(offer, "title", lang),
            "details": pick(offer, "details", lang),
            "price": offer.price,
            "compare_price": offer.compare_price,
        }
        for offer in store_product.offers.all()
    ]


def card(store_product, lang):
    offers = offer_rows(store_product, lang)
    prices = [offer["price"] for offer in offers] or [store_product.product.price]
    units = available_units(store_product.product)
    if units <= 0:
        state = "out"
    elif units <= LOW_STOCK:
        state = "low"
    else:
        state = "ok"
    return {
        "id": store_product.pk,
        "slug": store_product.slug,
        "title": pick(store_product, "title", lang),
        "summary": pick(store_product, "summary", lang),
        "description": pick(store_product, "description", lang),
        "badge": pick(store_product, "badge", lang),
        "price": min(prices),
        "max_price": max(prices),
        "has_offers": len(offers) > 0,
        "several_prices": len(set(prices)) > 1,
        "compare_price": store_product.compare_price,
        "offers": offers,
        "options": option_rows(store_product.options, lang),
        "images": image_rows(store_product, lang),
        "available": units,
        "state": state,
        "orderable": units > 0,
        "max_quantity": max(1, min(store_product.max_quantity, units)) if units > 0 else 1,
        "is_featured": store_product.is_featured,
        "from_maystro": remote_product(store_product.product) is not None,
    }


def cards(lang, queryset=None):
    rows = [card(item, lang) for item in (queryset if queryset is not None else published())]
    return [row for row in rows if row["price"] > 0]


def find(slug):
    return published().filter(slug=slug).first()
