import json
import os
import re

from django.contrib.staticfiles import finders
from django.http import HttpResponse
from django.utils.html import escape
from django.views.decorators.http import require_GET

from apps.core.models import BusinessProfile

from . import catalog
from .models import StorePage
from .public import shop_config
from .text import LANGUAGES, pick, text_for

APP_INDEX = "storefront/app/index.html"
HEAD_MARK = "<!--app-head-->"
CONFIG_MARK = "<!--app-config-->"
FALLBACK_INDEX = '<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><!--app-head--></head><body><div id="root"></div><!--app-config--></body></html>'
ROUTES = [
    (re.compile(r"^$"), "home"),
    (re.compile(r"^products/$"), "products"),
    (re.compile(r"^p/(?P<slug>[-a-z0-9]+)/$"), "product"),
    (re.compile(r"^cart/$"), "cart"),
    (re.compile(r"^checkout/$"), "checkout"),
    (re.compile(r"^thanks/[-A-Za-z0-9]+/$"), "thanks"),
    (re.compile(r"^track/$"), "track"),
    (re.compile(r"^page/(?P<slug>[-a-z0-9]+)/$"), "page"),
]
PRIVATE = {"cart", "checkout", "thanks", "track"}
_index = {"mtime": None, "text": FALLBACK_INDEX}


def app_index():
    path = finders.find(APP_INDEX)
    if not path:
        return FALLBACK_INDEX
    mtime = os.path.getmtime(path)
    if _index["mtime"] != mtime:
        with open(path, encoding="utf-8") as handle:
            _index["text"] = handle.read()
        _index["mtime"] = mtime
    return _index["text"]


def split_lang(path):
    if path == "en" or path.startswith("en/"):
        return "en", path[3:]
    return "ar", path


def match_route(rest):
    for pattern, name in ROUTES:
        found = pattern.match(rest)
        if found:
            return name, found.groupdict()
    return None, {}


def json_script(data):
    return json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")


def page_meta(request, lang, rest):
    t = text_for(lang)
    name, params = match_route(rest)
    meta = {
        "status": 200,
        "title": f"Vizora Global · {t['site_tagline']}",
        "description": t["meta_description"],
        "image": request.build_absolute_uri("/static/storefront/img/og.jpg"),
        "noindex": name in PRIVATE,
        "structured": None,
    }
    if name is None:
        meta.update(status=404, title=f"{t['not_found_title']} · Vizora Global", noindex=True)
    elif name == "home":
        meta["structured"] = {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": "Vizora Global",
            "url": request.build_absolute_uri("/"),
            "logo": request.build_absolute_uri("/static/storefront/img/logo-512.png"),
            "telephone": BusinessProfile.load().phone,
        }
    elif name == "products":
        meta["title"] = f"{t['products_title']} · Vizora Global"
    elif name == "product":
        item = catalog.find(params["slug"])
        if item is None:
            meta.update(status=404, title=f"{t['not_found_title']} · Vizora Global", noindex=True)
        else:
            card = catalog.card(item, lang)
            meta["title"] = f"{card['title']} · Vizora Global"
            meta["description"] = card["summary"] or card["description"][:200] or t["meta_description"]
            if card["images"]:
                meta["image"] = request.build_absolute_uri(card["images"][0]["url"])
            meta["structured"] = {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": card["title"],
                "description": meta["description"],
                "brand": {"@type": "Brand", "name": "Vizora Global"},
                "image": [request.build_absolute_uri(image["url"]) for image in card["images"]] or [meta["image"]],
                "url": request.build_absolute_uri(request.path),
                "offers": {
                    "@type": "AggregateOffer",
                    "priceCurrency": "DZD",
                    "lowPrice": card["price"],
                    "highPrice": card["max_price"],
                    "availability": "https://schema.org/InStock" if card["orderable"] else "https://schema.org/OutOfStock",
                },
            }
    elif name == "page":
        entry = StorePage.objects.filter(slug=params["slug"]).first()
        if entry is None:
            meta.update(status=404, title=f"{t['not_found_title']} · Vizora Global", noindex=True)
        else:
            meta["title"] = f"{pick(entry, 'title', lang)} · Vizora Global"
    elif name in PRIVATE:
        meta["title"] = f"{t.get(name + '_title', t['cart_title'])} · Vizora Global"
    return meta


def head_html(request, lang, rest, meta):
    other = "en" if lang == "ar" else "ar"
    here = request.build_absolute_uri(("/en/" if lang == "en" else "/") + rest)
    there = request.build_absolute_uri(("/en/" if other == "en" else "/") + rest)
    tags = [
        f"<title>{escape(meta['title'])}</title>",
        f'<meta name="description" content="{escape(meta["description"])}">',
        f'<link rel="canonical" href="{escape(here)}">',
        f'<link rel="alternate" hreflang="{lang}" href="{escape(here)}">',
        f'<link rel="alternate" hreflang="{other}" href="{escape(there)}">',
        f'<link rel="alternate" hreflang="x-default" href="{escape(request.build_absolute_uri("/" + rest))}">',
        '<meta property="og:type" content="website">',
        '<meta property="og:site_name" content="Vizora Global">',
        f'<meta property="og:title" content="{escape(meta["title"])}">',
        f'<meta property="og:description" content="{escape(meta["description"])}">',
        f'<meta property="og:url" content="{escape(here)}">',
        f'<meta property="og:image" content="{escape(meta["image"])}">',
        f'<meta property="og:locale" content="{"ar_DZ" if lang == "ar" else "en_US"}">',
        '<meta name="twitter:card" content="summary_large_image">',
    ]
    if meta["noindex"]:
        tags.append('<meta name="robots" content="noindex">')
    if meta["structured"]:
        tags.append(f'<script type="application/ld+json">{json_script(meta["structured"])}</script>')
    return "\n".join(tags)


def render_app(request, path, status=None):
    lang, rest = split_lang(path)
    meta = page_meta(request, lang, rest)
    config = shop_config(lang)
    html = app_index()
    html = html.replace('<html lang="ar" dir="rtl">', f'<html lang="{lang}" dir="{text_for(lang)["dir"]}">', 1)
    html = html.replace(HEAD_MARK, head_html(request, lang, rest, meta), 1)
    html = html.replace(CONFIG_MARK, f'<script id="shop-config" type="application/json">{json_script(config)}</script>', 1)
    response = HttpResponse(html, status=status or meta["status"])
    response["Content-Language"] = lang
    response["Cache-Control"] = "no-cache"
    return response


@require_GET
def app(request, path=""):
    return render_app(request, path)


def not_found(request, exception=None):
    return render_app(request, request.path.lstrip("/"), status=404)


@require_GET
def sitemap(request):
    paths = ["", "products/"]
    paths += [f"p/{item.slug}/" for item in catalog.published()]
    paths += [f"page/{entry.slug}/" for entry in StorePage.objects.all()]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">']
    for rest in paths:
        urls = {"ar": request.build_absolute_uri("/" + rest), "en": request.build_absolute_uri("/en/" + rest)}
        for lang in LANGUAGES:
            lines.append(f"<url><loc>{escape(urls[lang])}</loc>")
            for alt in LANGUAGES:
                lines.append(f'<xhtml:link rel="alternate" hreflang="{alt}" href="{escape(urls[alt])}"/>')
            lines.append("</url>")
    lines.append("</urlset>")
    return HttpResponse("\n".join(lines), content_type="application/xml")


@require_GET
def robots(request):
    blocked = ["/cart/", "/checkout/", "/thanks/", "/track/", "/api/"]
    body = ["User-agent: *"]
    body += [f"Disallow: {path}" for path in blocked]
    body += [f"Disallow: /en{path}" for path in blocked if path != "/api/"]
    body += [f"Sitemap: {request.build_absolute_uri('/sitemap.xml')}", ""]
    return HttpResponse("\n".join(body), content_type="text/plain")
