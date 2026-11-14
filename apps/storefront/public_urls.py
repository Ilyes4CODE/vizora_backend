from django.urls import path

from .public import (
    ShopCommunesView,
    ShopConfigView,
    ShopContactView,
    ShopLandingView,
    ShopLandingVisitView,
    ShopNotifyView,
    ShopOrderView,
    ShopPageView,
    ShopPreviewView,
    ShopProductsView,
    ShopProductView,
    ShopTrackView,
    ShopWilayasView,
)

urlpatterns = [
    path("shop/config/", ShopConfigView.as_view(), name="shop-config"),
    path("shop/products/", ShopProductsView.as_view(), name="shop-products"),
    path("shop/products/<slug:slug>/", ShopProductView.as_view(), name="shop-product"),
    path("shop/products/<slug:slug>/notify/", ShopNotifyView.as_view(), name="shop-notify"),
    path("shop/wilayas/", ShopWilayasView.as_view(), name="shop-wilayas"),
    path("shop/wilayas/<int:code>/communes/", ShopCommunesView.as_view(), name="shop-communes"),
    path("shop/pages/<slug:slug>/", ShopPageView.as_view(), name="shop-page"),
    path("shop/orders/", ShopOrderView.as_view(), name="shop-orders"),
    path("shop/track/", ShopTrackView.as_view(), name="shop-track"),
    path("shop/contact/", ShopContactView.as_view(), name="shop-contact"),
    path("shop/landing/<slug:slug>/", ShopLandingView.as_view(), name="shop-landing"),
    path("shop/landing/<slug:slug>/visit/", ShopLandingVisitView.as_view(), name="shop-landing-visit"),
    path("shop/design/preview/", ShopPreviewView.as_view(), name="shop-design-preview"),
]
