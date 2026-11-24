from django.utils.cache import add_never_cache_headers


class NoStoreApiMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/api/"):
            add_never_cache_headers(response)
            response["Pragma"] = "no-cache"
        return response


class AuditTrailMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        from .audit import record_unlogged_change

        record_unlogged_change(request, response)
        return response
