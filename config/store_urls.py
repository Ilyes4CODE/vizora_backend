from django.urls import include, path, re_path

from apps.storefront import views
from config.urls import product_image

urlpatterns = [
    re_path(r"^media/(?P<path>products/[\w\-][\w.\-]*)$", product_image),
    path("api/", include("apps.storefront.public_urls")),
    path("sitemap.xml", views.sitemap, name="sitemap"),
    path("robots.txt", views.robots, name="robots"),
    re_path(r"^(?!api/|static/|media/)(?P<path>.*)$", views.app, name="app"),
]

handler404 = "apps.storefront.views.not_found"
