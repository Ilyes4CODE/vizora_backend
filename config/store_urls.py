from django.urls import include, path, re_path

from config.urls import product_image

urlpatterns = [
    re_path(r"^media/(?P<path>products/[\w\-][\w.\-]*)$", product_image),
    path("", include("apps.storefront.urls")),
]

handler404 = "apps.storefront.views.not_found"
