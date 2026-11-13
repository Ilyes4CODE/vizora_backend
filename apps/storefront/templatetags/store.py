import hashlib
from functools import lru_cache

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()

ILLUSTRATION_TYPES = ("webp", "png", "jpg")


@lru_cache(maxsize=256)
def file_version(path):
    found = finders.find(path)
    if not found:
        return ""
    with open(found, "rb") as handle:
        return hashlib.sha1(handle.read()).hexdigest()[:10]


@register.simple_tag
def asset(path):
    version = file_version(path)
    url = static(path)
    return f"{url}?v={version}" if version else url


@lru_cache(maxsize=128)
def illustration_path(name):
    for extension in ILLUSTRATION_TYPES:
        path = f"storefront/illustrations/{name}.{extension}"
        if finders.find(path):
            return path
    return ""


def illustration(name):
    path = illustration_path(name)
    return asset(path) if path else ""
