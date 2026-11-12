import re

from django.urls import URLResolver, get_resolver

PARAMETER = re.compile(r"\(\?P<\w+>[^)]+\)|<[^>]+>")
HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def walk(patterns, prefix=""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from walk(pattern.url_patterns, prefix + str(pattern.pattern))
        else:
            yield prefix + str(pattern.pattern), pattern.callback


def endpoints():
    found = []
    for route, callback in walk(get_resolver().url_patterns):
        view_class = getattr(callback, "cls", None)
        if view_class is None:
            continue
        actions = getattr(callback, "actions", None)
        names = list(actions) if actions else [name for name in HTTP_METHODS if hasattr(view_class, name)]
        for name in names:
            if name in HTTP_METHODS and name in view_class.http_method_names:
                found.append((name.upper(), route, callback))
    return found


def route_path(route):
    return "/" + PARAMETER.sub("1", route).replace("^", "").replace("$", "")
