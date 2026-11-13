from django.urls import include, path

from . import views


def localized():
    return [
        path("", views.home, name="home"),
        path("products/", views.product_list, name="products"),
        path("p/<slug:slug>/", views.product_detail, name="product"),
        path("p/<slug:slug>/notify/", views.stock_alert, name="notify"),
        path("cart/", views.cart_view, name="cart"),
        path("cart/add/", views.cart_add, name="cart_add"),
        path("cart/update/", views.cart_update, name="cart_update"),
        path("checkout/", views.checkout, name="checkout"),
        path("thanks/<str:reference>/", views.thanks, name="thanks"),
        path("track/", views.track, name="track"),
        path("contact/", views.contact, name="contact"),
        path("page/<slug:slug>/", views.page, name="page"),
    ]


urlpatterns = [
    path("communes/<int:code>.json", views.communes_json, name="communes"),
    path("sitemap.xml", views.sitemap, name="sitemap"),
    path("robots.txt", views.robots, name="robots"),
    path("en/", include((localized(), "store"), namespace="en"), {"lang": "en"}),
    path("", include((localized(), "store"), namespace="ar"), {"lang": "ar"}),
]
