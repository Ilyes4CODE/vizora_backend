from unittest.mock import MagicMock, patch

from apps.catalog.models import Product
from apps.core.models import Commune
from apps.orders.models import Order
from apps.shipping import maystro
from apps.shipping.models import MaystroConfig, Shipment

from .base import ApiTestCase

API = "https://orders-management.maystro-delivery.com"
PRODUCT_UUID = "11111111-2222-3333-4444-555555555555"
ORDER_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def reply(status_code=200, payload=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {} if payload is None else payload
    return response


def no_network(*args, **kwargs):
    raise AssertionError(f"unmocked Maystro call: {args[:2]}")


class LiveClientTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        for patcher in (patch.object(maystro, "MIN_REQUEST_INTERVAL", 0), patch("apps.shipping.maystro.requests.request", side_effect=no_network)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.set_mode("live")
        Commune.objects.create(wilaya_id=16, name_ar="الدار البيضاء", name_fr="Dar El Beida", courier_ref="900")
        Product.objects.filter(pk=self.product.pk).update(courier_ref=PRODUCT_UUID)

    def set_mode(self, mode):
        config = MaystroConfig.load()
        config.mode = mode
        config.set_token("synthetic-token")
        config.save()

    def send(self, order):
        return self.client.post("/api/shipping/send/", {"order_ids": [order["id"]]})

    @patch("apps.shipping.maystro.requests.request")
    def test_create_order_follows_official_request_format(self, mocked):
        mocked.return_value = reply(201, {"id": ORDER_UUID, "external_id": "x", "tracking": "172316976800001", "success": True, "delivery_price": 400, "spent_points": None, "warehouse": "ALG", "errors": []})
        order = self.confirmed_order(items=[{"product": self.product.pk, "quantity": 2}], address="حي 5 جويلية", note="اتصل قبل الوصول")
        response = self.send(order)
        self.assertEqual(response.data["failed"], [])
        method, url = mocked.call_args[0]
        kwargs = mocked.call_args[1]
        self.assertEqual((method, url), ("POST", f"{API}/api/orders/"))
        self.assertTrue(url.endswith("/"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Token synthetic-token")
        self.assertEqual(
            kwargs["json"],
            {
                "external_id": order["reference"],
                "customer_name": "كريم مزيان",
                "customer_phone": "0555004112",
                "destination_text": "حي 5 جويلية",
                "commune": 900,
                "delivery_type": 1,
                "total_price": 8400,
                "express": False,
                "note_to_driver": "اتصل قبل الوصول",
                "details": [{"product": PRODUCT_UUID, "quantity": 2}],
            },
        )
        shipment = Shipment.objects.get(order_id=order["id"])
        self.assertEqual((shipment.tracking_number, shipment.external_id), ("172316976800001", ORDER_UUID))

    @patch("apps.shipping.maystro.requests.request")
    def test_stop_desk_and_missing_address_fallback(self, mocked):
        mocked.return_value = reply(201, {"id": ORDER_UUID, "tracking": "1", "success": True, "errors": []})
        self.send(self.confirmed_order(delivery_type="desk", address=""))
        body = mocked.call_args[1]["json"]
        self.assertEqual(body["delivery_type"], 2)
        self.assertTrue(body["destination_text"])
        self.assertIsNone(body["note_to_driver"])

    @patch("apps.shipping.maystro.requests.request")
    def test_order_error_codes_become_arabic(self, mocked):
        mocked.return_value = reply(400, {"errors": [{"detail": "wilaya or commune do not exist", "code": 40}]})
        order = self.confirmed_order()
        failed = self.send(order).data["failed"][0]
        self.assertEqual(failed["code"], "maystro_rejected")
        self.assertIn("(40)", failed["detail"])
        self.assertIn("البلدية", failed["detail"])
        self.assertEqual(Order.objects.get(pk=order["id"]).status, "confirmed")
        self.assertEqual(Product.objects.get(pk=self.product.pk).on_hand, 100)

    @patch("apps.shipping.maystro.requests.request")
    def test_unsuccessful_result_inside_201_is_a_failure(self, mocked):
        mocked.return_value = reply(201, [{"id": None, "tracking": None, "success": False, "errors": [{"detail": "dup", "code": 55}]}])
        order = self.confirmed_order()
        failed = self.send(order).data["failed"][0]
        self.assertEqual(failed["code"], "maystro_rejected")
        self.assertIn("(55)", failed["detail"])
        self.assertFalse(Shipment.objects.exists())

    @patch("apps.shipping.maystro.time.sleep")
    @patch("apps.shipping.maystro.requests.request")
    def test_rate_limit_waits_then_retries_once(self, mocked, sleeper):
        mocked.side_effect = [
            reply(429, {"detail": "Requête ralentie. Expected available in 3 seconds."}),
            reply(200, [{"display_id": 16}]),
        ]
        self.assertEqual(self.client.post("/api/shipping/config/test/").data, {"connected": True})
        self.assertEqual(mocked.call_count, 2)
        waits = [call.args[0] for call in sleeper.call_args_list if call.args and call.args[0] >= 1]
        self.assertTrue(waits and 3 <= waits[0] <= 60)

    @patch("apps.shipping.maystro.time.sleep")
    @patch("apps.shipping.maystro.requests.request")
    def test_rate_limit_wait_is_capped(self, mocked, sleeper):
        mocked.side_effect = [reply(429, {"detail": "Expected available in 900 seconds."}), reply(200, [])]
        with patch.object(maystro, "MAX_RETRY_WAIT", 7):
            self.client.post("/api/shipping/config/test/")
        self.assertLessEqual(max(call.args[0] for call in sleeper.call_args_list if call.args), 7)

    @patch("apps.shipping.maystro.requests.request")
    def test_ping_uses_documented_host(self, mocked):
        mocked.return_value = reply(200, [])
        self.client.post("/api/shipping/config/test/")
        self.assertEqual(mocked.call_args[0], ("GET", f"{API}/api/base/wilayas/"))

    @patch("apps.shipping.maystro.requests.request")
    def test_bad_token_message(self, mocked):
        mocked.return_value = reply(401, {"detail": "Token non valide."})
        response = self.client.post("/api/shipping/config/test/")
        self.assertCode(response, 502)
        self.assertIn("Maystro", response.data["detail"])

    def test_status_history_fallback_when_no_mirror(self):
        self.set_mode("simulated")
        order_id, tracking = self.shipped_order()
        self.set_mode("live")
        Shipment.objects.filter(tracking_number=tracking).update(external_id=ORDER_UUID)
        history = [{"status": 41, "created_at": "2026-11-10T10:00:00+01:00", "comment": None, "agent": None}, {"status": 31, "created_at": "2026-11-10T08:00:00+01:00", "comment": None, "agent": None}]
        with patch("apps.shipping.maystro.requests.request", return_value=reply(200, history)) as mocked:
            response = self.client.post("/api/shipping/sync/")
        self.assertCode(response, 200)
        urls = [call.args[1] for call in mocked.call_args_list]
        self.assertIn(f"{API}/api/orders/history-order/{ORDER_UUID}/", urls)
        self.assertEqual(Order.objects.get(pk=order_id).status, "delivered")


class SimulatedModeTests(ApiTestCase):
    def test_simulated_send_still_works_without_network(self):
        with patch("apps.shipping.maystro.requests.request") as mocked:
            order_id, tracking = self.shipped_order()
        mocked.assert_not_called()
        self.assertTrue(tracking.startswith("MD-"))

    def test_status_33_ready_for_pickup_is_desk(self):
        _, tracking = self.shipped_order()
        self.webhook(tracking, 33)
        self.assertEqual(Shipment.objects.get(tracking_number=tracking).stage, "desk")
