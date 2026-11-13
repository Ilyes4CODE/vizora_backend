from django.apps import AppConfig


class StorefrontConfig(AppConfig):
    name = "apps.storefront"
    label = "storefront"

    def ready(self):
        from apps.orders.signals import order_confirmed, order_delivered

        from .conversions import on_confirmed, on_delivered

        order_confirmed.connect(on_confirmed, dispatch_uid="storefront-purchase-confirmed")
        order_delivered.connect(on_delivered, dispatch_uid="storefront-purchase-delivered")
