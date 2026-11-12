from io import BytesIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import Resolver404
from openpyxl import Workbook
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import Role, User
from apps.catalog.models import Product, StockMovement
from apps.catalog.services import apply_movement
from apps.shipping.live import forget_attempts
from apps.shipping.models import MaystroConfig

PASSWORD = "Vizora-Strong-2026!"
STATUS_CODES = {"in_transit": 8, "out_for_delivery": 31, "failed": 32, "delivered": 41, "returned": 51}


class RecordingClient(APIClient):
    hits = set()

    def request(self, **kwargs):
        response = super().request(**kwargs)
        try:
            route = response.resolver_match.route
        except Resolver404:
            return response
        if response.status_code < 400:
            RecordingClient.hits.add((kwargs["REQUEST_METHOD"].upper(), route))
        return response


def excel_file(rows, name="orders.xlsx"):
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return SimpleUploadedFile(name, output.getvalue())


class ApiTestCase(APITestCase):
    client_class = RecordingClient

    @classmethod
    def setUpTestData(cls):
        cls.admin = cls.make_user("admin_user", "أمين بوزيد", Role.ADMIN)
        cls.agent = cls.make_user("agent_user", "سارة قاسمي", Role.CONFIRMATION)
        cls.stock_user = cls.make_user("stock_user", "إلياس مناصري", Role.STOCK)
        cls.accountant = cls.make_user("accountant_user", "محمد المحاسب", Role.ACCOUNTANT)
        cls.product = Product.objects.create(name="المنتج الأول", sku="P-1", price=3900, cost=1400, low_stock_threshold=10)
        cls.product_b = Product.objects.create(name="المنتج الثاني", sku="P-2", price=5500, cost=2100, low_stock_threshold=10)
        apply_movement(cls.product.pk, StockMovement.Kind.IN, 100)
        apply_movement(cls.product_b.pk, StockMovement.Kind.IN, 100)

    @staticmethod
    def make_user(username, full_name, role):
        return User.objects.create_user(username=username, password=PASSWORD, full_name=full_name, role=role)

    def setUp(self):
        cache.clear()
        forget_attempts()
        waiting = patch("apps.shipping.maystro.NETWORK_RETRY_WAIT", 0)
        waiting.start()
        self.addCleanup(waiting.stop)
        self.as_user(self.admin)

    def as_user(self, user):
        token = AccessToken.for_user(user)
        token["sv"] = User.objects.get(pk=user.pk).session_version
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def anonymous(self):
        self.client.credentials()

    def assertCode(self, response, status_code, code=None):
        body = b"" if response.streaming else response.content[:500]
        self.assertEqual(response.status_code, status_code, body)
        if code is not None:
            self.assertEqual(response.data["code"], code, response.data)

    def order_payload(self, **overrides):
        payload = {
            "customer_name": "كريم مزيان",
            "phone": "0555 00 41 12",
            "wilaya": 16,
            "commune": "الدار البيضاء",
            "delivery_type": "home",
            "items": [{"product": self.product.pk, "quantity": 1}],
        }
        payload.update(overrides)
        return payload

    def new_order(self, **overrides):
        response = self.client.post("/api/orders/", self.order_payload(**overrides))
        self.assertCode(response, 201)
        return response.data

    def confirmed_order(self, **overrides):
        order = self.new_order(**overrides)
        response = self.client.post(f"/api/confirmation/orders/{order['id']}/outcome/", {"outcome": "confirmed"})
        self.assertCode(response, 200)
        return response.data

    def shipped_order(self, **overrides):
        order = self.confirmed_order(**overrides)
        response = self.client.post("/api/shipping/send/", {"order_ids": [order["id"]]})
        self.assertCode(response, 200)
        self.assertEqual(response.data["failed"], [])
        return order["id"], response.data["sent"][0]["tracking_number"]

    def webhook(self, tracking, status, last_update=None):
        config = MaystroConfig.load()
        message = {
            "event": "OrderStatusChanged",
            "instance_table": "Order",
            "payload": {
                "display_id_order": tracking,
                "status": STATUS_CODES.get(status, status),
                "last_update": last_update,
            },
        }
        return self.client.post(f"/api/shipping/webhook/{config.webhook_token}/", message)

    def delivered_order(self, **overrides):
        order_id, tracking = self.shipped_order(**overrides)
        self.assertCode(self.webhook(tracking, "delivered"), 200)
        return order_id, tracking

    def returned_order(self, **overrides):
        order_id, tracking = self.shipped_order(**overrides)
        self.assertCode(self.webhook(tracking, "returned"), 200)
        return order_id, tracking
