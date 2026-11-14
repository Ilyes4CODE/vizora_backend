import secrets
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from apps.orders.models import OrderStatus

from . import catalog
from .design import DesignError, clean_document, default_home, default_site, font_href, landing_template, theme_vars
from .models import LandingVisit, Layout, LayoutVersion, OrderOrigin, StoreSettings

KEEP_VERSIONS = 10
STATS_DAYS = 30
NAMES = {"site": "الشكل العام والقائمة", "home": "الصفحة الرئيسية"}


def ensure(kind):
    layout = Layout.objects.filter(slug=kind).first()
    if layout is not None:
        return layout
    data = default_site() if kind == "site" else default_home()
    try:
        with transaction.atomic():
            return Layout.objects.create(
                kind=kind, slug=kind, name=NAMES[kind], draft=data, published=data, is_live=True, published_at=timezone.now()
            )
    except IntegrityError:
        return Layout.objects.get(slug=kind)


def design_token():
    store = StoreSettings.load()
    if not store.design_token:
        store.design_token = secrets.token_urlsafe(24)
        store.save(update_fields=["design_token"])
    return store.design_token


def rotate_token():
    store = StoreSettings.load()
    store.design_token = secrets.token_urlsafe(24)
    store.save(update_fields=["design_token"])
    return store.design_token


def valid_token(value):
    token = StoreSettings.load().design_token
    return bool(value) and bool(token) and secrets.compare_digest(str(value), token)


def site_payload(data):
    theme = data.get("theme", {})
    return {**data, "vars": theme_vars(theme), "font": font_href(theme)}


def site_data(preview=False):
    layout = ensure("site")
    return site_payload(layout.draft if preview else layout.published)


def home_sections(preview=False):
    layout = ensure("home")
    return (layout.draft if preview else layout.published)["sections"]


def landing_for(slug, preview=False):
    layout = Layout.objects.filter(kind=Layout.Kind.LANDING, slug=slug).select_related("store_product__product").first()
    if layout is None:
        return None, None
    if preview:
        return layout, layout.draft
    if not layout.is_live or layout.published is None:
        return None, None
    return layout, layout.published


def landing_payload(layout, data):
    card = None
    if layout.store_product is not None:
        card = catalog.card(layout.store_product, layout.lang)
    return {"slug": layout.slug, "name": layout.name, "lang": layout.lang, "page": data["page"], "sections": data["sections"], "product": card}


def check_landing(layout):
    if layout.kind != Layout.Kind.LANDING:
        return
    if layout.store_product is None:
        raise DesignError("اختر المنتج الذي تبيعه هذه الصفحة", "product")
    if not layout.store_product.is_published:
        raise DesignError("المنتج غير منشور في الموقع، انشره أولاً من «المنتجات في الموقع»", "product")


@transaction.atomic
def publish(layout, user):
    data = clean_document(layout.kind, layout.draft, strict=True)
    check_landing(layout)
    now = timezone.now()
    first = layout.published is None
    layout.draft = data
    layout.published = data
    layout.published_at = now
    if first or layout.kind != Layout.Kind.LANDING:
        layout.is_live = True
    layout.save()
    LayoutVersion.objects.create(layout=layout, data=data, created_by=user, created_at=now)
    stale = list(layout.versions.values_list("pk", flat=True)[KEEP_VERSIONS:])
    if stale:
        LayoutVersion.objects.filter(pk__in=stale).delete()
    return layout


def save_draft(layout, draft):
    layout.draft = clean_document(layout.kind, draft)
    layout.save(update_fields=["draft", "updated_at"])
    return layout


def discard(layout):
    if layout.published is not None:
        layout.draft = layout.published
        layout.save(update_fields=["draft", "updated_at"])
    return layout


def restore(layout, version):
    layout.draft = clean_document(layout.kind, version.data)
    layout.save(update_fields=["draft", "updated_at"])
    return layout


def unique_slug(base):
    base = (base or "offer")[:50].strip("-") or "offer"
    slug = base
    index = 2
    while Layout.objects.filter(slug=slug).exists() or slug in ("site", "home", "new", "preview"):
        slug = f"{base}-{index}"
        index += 1
    return slug


def create_landing(name, store_product, lang, template, slug=None):
    data = landing_template(template, lang, store_product)
    return Layout.objects.create(
        kind=Layout.Kind.LANDING,
        slug=unique_slug(slug or store_product.slug),
        name=name,
        store_product=store_product,
        lang=lang,
        draft=data,
    )


def duplicate(layout):
    return Layout.objects.create(
        kind=layout.kind,
        slug=unique_slug(f"{layout.slug}-copy"),
        name=f"{layout.name} (نسخة)",
        store_product=layout.store_product,
        lang=layout.lang,
        draft=clean_document(layout.kind, layout.draft),
    )


def count_visit(layout):
    today = timezone.localdate()
    updated = LandingVisit.objects.filter(layout=layout, day=today).update(count=F("count") + 1)
    if not updated:
        try:
            with transaction.atomic():
                LandingVisit.objects.create(layout=layout, day=today, count=1)
        except IntegrityError:
            LandingVisit.objects.filter(layout=layout, day=today).update(count=F("count") + 1)


def landing_stats(layouts):
    since = timezone.now() - timedelta(days=STATS_DAYS)
    ids = [layout.pk for layout in layouts]
    views = dict(
        LandingVisit.objects.filter(layout_id__in=ids, day__gte=since.date()).values("layout_id").annotate(total=Sum("count")).values_list("layout_id", "total")
    )
    orders = {
        row["landing_id"]: row
        for row in OrderOrigin.objects.filter(landing_id__in=ids, created_at__gte=since)
        .values("landing_id")
        .annotate(
            orders=Count("id"),
            confirmed=Count("id", filter=Q(order__confirmed_at__isnull=False)),
            delivered=Count("id", filter=Q(order__status=OrderStatus.DELIVERED)),
            value=Sum("order__subtotal", filter=Q(order__confirmed_at__isnull=False)),
        )
    }
    result = {}
    for layout_id in ids:
        row = orders.get(layout_id, {})
        seen = views.get(layout_id) or 0
        placed = row.get("orders", 0)
        result[layout_id] = {
            "days": STATS_DAYS,
            "views": seen,
            "orders": placed,
            "confirmed": row.get("confirmed", 0),
            "delivered": row.get("delivered", 0),
            "confirmed_value": row.get("value") or 0,
            "conversion": round(placed * 100 / seen, 1) if seen else 0,
        }
    return result


def landing_from_visit(slug):
    if not slug:
        return None
    return Layout.objects.filter(kind=Layout.Kind.LANDING, slug=slug).first()
