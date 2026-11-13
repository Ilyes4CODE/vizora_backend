from django.conf import settings
from django.urls import include, path, re_path
from django.views.static import serve
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView


def product_image(request, path):
    return serve(request, path, document_root=settings.MEDIA_ROOT)


urlpatterns = [
    path("api/", include("apps.core.urls")),
    path("api/", include("apps.accounts.urls")),
    path("api/", include("apps.catalog.urls")),
    path("api/", include("apps.finance.urls")),
    path("api/", include("apps.orders.urls")),
    path("api/", include("apps.shipping.urls")),
    path("api/", include("apps.reports.urls")),
    path("api/", include("apps.storefront.api_urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    re_path(r"^media/(?P<path>products/[\w\-][\w.\-]*)$", product_image),
]
