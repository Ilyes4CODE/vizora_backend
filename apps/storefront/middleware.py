from django.conf import settings

STORE_URLCONF = "config.store_urls"


class StoreHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.hosts = {host.lower() for host in settings.STORE_HOSTS}

    def __call__(self, request):
        host = request.META.get("HTTP_HOST", "").split(":")[0].lower()
        if host in self.hosts:
            request.urlconf = STORE_URLCONF
        return self.get_response(request)
