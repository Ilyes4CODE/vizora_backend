import base64
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import requests
from django.core.management import call_command
from django.utils import timezone

from apps.catalog.models import Product
from apps.core.models import Commune, Wilaya
from apps.orders.models import Order
from apps.shipping.maystro import STATUS_LABELS
from apps.shipping.models import MaystroConfig, MaystroOrder, MaystroPayment, Shipment, default_status_map

from .base import ApiTestCase


class SendingTests(ApiTestCase):
    def test_send_confirmed_order(self):
        order = self.confirmed_order(items=[{"product": self.product.pk, "quantity": 3}])
        response = self.client.post("/api/shipping/send/", {"order_ids": [order["id"]]})
        self.assertCode(response, 200)
        self.assertEqual(response.data["failed"], [])
        self.assertEqual(Order.objects.get(pk=order["id"]).status, "shipping")
        self.assertEqual(Product.objects.get(pk=self.product.pk).on_hand, 97)

    def test_send_reports_each_failure(self):
        pending = self.new_order()
        response = self.client.post("/api/shipping/send/", {"order_ids": [pending["id"], 99999]})
        codes = [item["code"] for item in response.data["failed"]]
        self.assertEqual(codes, ["invalid_transition", "not_found"])

    def test_order_cannot_be_sent_twice(self):
        order_id, _ = self.shipped_order()
        response = self.client.post("/api/shipping/send/", {"order_ids": [order_id]})
        self.assertEqual(response.data["failed"][0]["code"], "invalid_transition")
        self.assertEqual(Shipment.objects.count(), 1)
        self.assertEqual(Product.objects.get(pk=self.product.pk).on_hand, 99)

    def test_send_all_confirmed(self):
        self.confirmed_order()
        self.confirmed_order(phone="0661002875")
        self.new_order(phone="0770001690")
        response = self.client.post("/api/shipping/send/", {"all_confirmed": True})
        self.assertEqual(len(response.data["sent"]), 2)

    def test_send_all_ignores_orders_a_colleague_just_sent(self):
        self.confirmed_order()
        outcome = {"sent": [], "failed": [{"order": 1, "code": "invalid_transition", "detail": "x"}, {"order": 2, "code": "maystro_rejected", "detail": "y"}]}
        with patch("apps.shipping.views.send_orders", return_value=outcome):
            response = self.client.post("/api/shipping/send/", {"all_confirmed": True})
        self.assertEqual([item["code"] for item in response.data["failed"]], ["maystro_rejected"])

    def test_send_requires_selection(self):
        self.assertCode(self.client.post("/api/shipping/send/", {}), 400)


class WebhookTests(ApiTestCase):
    def url(self):
        return f"/api/shipping/webhook/{MaystroConfig.load().webhook_token}/"

    def message(self, tracking, status):
        return {"event": "OrderStatusChanged", "payload": {"display_id_order": tracking, "status": status}}

    def test_token_is_verified(self):
        self.anonymous()
        response = self.client.post("/api/shipping/webhook/wrong/", self.message("x", 41))
        self.assertCode(response, 403, "invalid_webhook_token")

    def test_lifecycle_to_delivery(self):
        order_id, tracking = self.shipped_order()
        self.anonymous()
        self.assertEqual(self.webhook(tracking, "out_for_delivery").data["result"], "applied")
        shipment = Shipment.objects.get(tracking_number=tracking)
        self.assertEqual((shipment.stage, shipment.delivery_attempts), ("out", 1))
        self.assertEqual(self.webhook(tracking, "delivered").data["result"], "applied")
        order = Order.objects.get(pk=order_id)
        self.assertEqual(order.status, "delivered")
        self.assertIsNotNone(order.delivered_at)

    def test_assigned_then_shipped_counts_one_attempt(self):
        _, tracking = self.shipped_order()
        self.webhook(tracking, 22)
        self.webhook(tracking, 31)
        self.assertEqual(Shipment.objects.get(tracking_number=tracking).delivery_attempts, 1)

    def test_stop_desk_parcel_shows_at_desk(self):
        _, tracking = self.shipped_order(delivery_type="desk")
        self.webhook(tracking, 31)
        self.assertEqual(Shipment.objects.get(tracking_number=tracking).stage, "desk")

    def test_postponed_and_alerted_need_attention(self):
        for index, status in enumerate((32, 42, 12)):
            _, tracking = self.shipped_order(phone=f"055500000{index}")
            self.webhook(tracking, status)
            self.assertEqual(Shipment.objects.get(tracking_number=tracking).stage, "failed")

    def test_return_applies_fee(self):
        order_id, tracking = self.shipped_order()
        self.webhook(tracking, "returned")
        self.assertEqual(Order.objects.get(pk=order_id).status, "returned")
        self.assertEqual(Shipment.objects.get(tracking_number=tracking).return_fee, 350)

    def test_aborted_cancels_order_without_fee(self):
        order_id, tracking = self.shipped_order()
        self.webhook(tracking, 50)
        order = Order.objects.get(pk=order_id)
        shipment = Shipment.objects.get(tracking_number=tracking)
        self.assertEqual((order.status, order.cancel_reason), ("cancelled", "courier_aborted"))
        self.assertEqual((shipment.stage, shipment.return_fee), ("aborted", 0))
        response = self.client.post(f"/api/shipping/parcels/{shipment.pk}/receive-return/", {"condition": "good"})
        self.assertCode(response, 200)
        self.assertEqual(Product.objects.get(pk=self.product.pk).on_hand, 100)

    def test_timeline_uses_arabic_status_names(self):
        _, tracking = self.shipped_order()
        self.webhook(tracking, 8)
        parcel = Shipment.objects.get(tracking_number=tracking)
        detail = self.client.get(f"/api/shipping/parcels/{parcel.pk}/")
        self.assertEqual(detail.data["events"][0]["message"], "بانتظار النقل")

    def test_every_documented_status_is_mapped_and_named(self):
        documented = {"4", "5", "6", "8", "9", "10", "11", "12", "15", "22", "31", "32", "33", "41", "42", "50", "51", "52", "53"}
        self.assertEqual(set(default_status_map()), documented)
        self.assertEqual(set(STATUS_LABELS), documented)

    def test_duplicate_event_is_idempotent(self):
        _, tracking = self.shipped_order()
        moment = timezone.now().isoformat()
        self.assertEqual(self.webhook(tracking, "out_for_delivery", moment).data["result"], "applied")
        self.assertEqual(self.webhook(tracking, "out_for_delivery", moment).data["result"], "recorded")
        self.assertEqual(Shipment.objects.get(tracking_number=tracking).delivery_attempts, 1)

    def test_older_event_does_not_rewind(self):
        _, tracking = self.shipped_order()
        now = timezone.now()
        self.webhook(tracking, "out_for_delivery", now.isoformat())
        older = (now - timedelta(hours=5)).isoformat()
        self.assertEqual(self.webhook(tracking, "in_transit", older).data["result"], "recorded")
        self.assertEqual(Shipment.objects.get(tracking_number=tracking).stage, "out")

    def test_final_status_cannot_change(self):
        order_id, tracking = self.delivered_order()
        self.assertEqual(self.webhook(tracking, "returned").data["result"], "recorded")
        self.assertEqual(Order.objects.get(pk=order_id).status, "delivered")

    def test_unknown_status_is_recorded_without_change(self):
        _, tracking = self.shipped_order()
        self.assertEqual(self.webhook(tracking, 777).data["result"], "recorded")
        shipment = Shipment.objects.get(tracking_number=tracking)
        self.assertEqual((shipment.stage, shipment.raw_status), ("transit", "777"))

    def test_unknown_tracking_is_ignored(self):
        self.assertEqual(self.webhook("MD-0", "delivered").data["result"], "ignored")

    def test_lookup_by_maystro_uuid(self):
        _, tracking = self.shipped_order()
        Shipment.objects.filter(tracking_number=tracking).update(external_id="uuid-1")
        response = self.client.post(self.url(), {"payload": {"id": "uuid-1", "status": 41}})
        self.assertEqual(response.data["result"], "applied")

    def test_inventory_event_is_ignored(self):
        response = self.client.post(self.url(), {"event": "InventoryMovement", "payload": {"free_quantity": 3}})
        self.assertEqual(response.data["result"], "ignored")

    def test_missing_payload(self):
        self.assertCode(self.client.post(self.url(), {}), 400, "invalid_webhook_payload")
        self.assertCode(self.client.post(self.url(), "not-valid-!!", content_type="text/plain"), 400, "invalid_webhook_payload")

    def encoded(self, tracking, layers):
        value = json.dumps(self.message(tracking, 41))
        for _ in range(layers):
            value = base64.b64encode(value.encode("utf-8")).decode("ascii")
        return value

    def test_base64_once_and_twice(self):
        for index, layers in enumerate((1, 2)):
            _, tracking = self.shipped_order(phone=f"066100000{index}")
            response = self.client.post(self.url(), self.encoded(tracking, layers), content_type="application/json")
            self.assertEqual(response.data["result"], "applied")

    def test_pubsub_style_envelope(self):
        _, tracking = self.shipped_order()
        response = self.client.post(self.url(), {"message": {"data": self.encoded(tracking, 1)}})
        self.assertEqual(response.data["result"], "applied")


class ParcelTests(ApiTestCase):
    def test_list_detail_and_filter(self):
        _, tracking = self.shipped_order()
        listing = self.client.get("/api/shipping/parcels/")
        self.assertCode(listing, 200)
        parcel = listing.data["results"][0]
        self.assertEqual(parcel["tracking_number"], tracking)
        self.assertEqual(parcel["display_stage_label"], "في الطريق")
        self.assertEqual(parcel["amount"], 4500)
        detail = self.client.get(f"/api/shipping/parcels/{parcel['id']}/")
        self.assertCode(detail, 200)
        self.assertEqual(detail.data["events"], [])
        self.assertEqual(self.client.get("/api/shipping/parcels/", {"stage": "out"}).data["count"], 0)
        self.assertEqual(self.client.get("/api/shipping/parcels/", {"search": tracking}).data["count"], 1)

    def test_delivered_parcels_leave_active_list(self):
        self.delivered_order()
        self.assertEqual(self.client.get("/api/shipping/parcels/").data["count"], 0)
        self.assertEqual(self.client.get("/api/shipping/parcels/", {"stage": "delivered"}).data["count"], 1)

    def test_counts_and_stuck_detection(self):
        _, first = self.shipped_order()
        _, second = self.shipped_order(phone="0661002875")
        _, third = self.shipped_order(phone="0770001690")
        self.webhook(first, "out_for_delivery")
        self.webhook(second, "failed")
        Shipment.objects.filter(tracking_number=third).update(last_update_at=timezone.now() - timedelta(hours=49))
        response = self.client.get("/api/shipping/parcels/counts/")
        self.assertCode(response, 200)
        self.assertEqual(response.data, {"all": 3, "transit": 0, "out": 1, "desk": 0, "stuck": 2})
        stuck = self.client.get("/api/shipping/parcels/", {"stage": "stuck"})
        self.assertEqual(stuck.data["count"], 2)
        self.assertEqual(stuck.data["results"][0]["display_stage"], "stuck")

    def test_manual_status(self):
        order_id, tracking = self.shipped_order()
        parcel = Shipment.objects.get(tracking_number=tracking)
        response = self.client.post(f"/api/shipping/parcels/{parcel.pk}/status/", {"stage": "delivered", "message": "يدوي"})
        self.assertCode(response, 200)
        self.assertEqual(response.data["stage"], "delivered")
        self.assertEqual(Order.objects.get(pk=order_id).status, "delivered")
        self.assertCode(self.client.post(f"/api/shipping/parcels/{parcel.pk}/status/", {"stage": "nope"}), 400)

    def test_receive_good_return_restocks(self):
        _, tracking = self.returned_order()
        parcel = Shipment.objects.get(tracking_number=tracking)
        url = f"/api/shipping/parcels/{parcel.pk}/receive-return/"
        self.assertCode(self.client.post(url, {"condition": "broken"}), 400)
        self.assertEqual(Product.objects.get(pk=self.product.pk).on_hand, 99)
        self.assertCode(self.client.post(url, {"condition": "good"}), 200)
        self.assertEqual(Product.objects.get(pk=self.product.pk).on_hand, 100)
        self.assertCode(self.client.post(url, {"condition": "good"}), 409, "return_already_received")

    def test_receive_damaged_return_does_not_restock(self):
        _, tracking = self.returned_order()
        parcel = Shipment.objects.get(tracking_number=tracking)
        self.assertCode(self.client.post(f"/api/shipping/parcels/{parcel.pk}/receive-return/", {"condition": "damaged"}), 200)
        self.assertEqual(Product.objects.get(pk=self.product.pk).on_hand, 99)

    def test_cannot_receive_active_parcel(self):
        _, tracking = self.shipped_order()
        parcel = Shipment.objects.get(tracking_number=tracking)
        self.assertCode(self.client.post(f"/api/shipping/parcels/{parcel.pk}/receive-return/", {"condition": "good"}), 409, "invalid_transition")


class ConfigAndLiveClientTests(ApiTestCase):
    PRODUCT_UUID = "90a570bd-dd80-4d80-b0a2-597e9524504d"
    MAYSTRO_ORDER = {"id": "03e1f7c4-7d40-4f63-a6d2-304dec93568f", "tracking": "000217543", "success": True, "delivery_price": 400}

    def enable_live(self, token="secret-token"):
        config = MaystroConfig.load()
        config.mode = "live"
        config.set_token(token)
        config.save()
        return config

    def link_catalog(self):
        Commune.objects.create(wilaya_id=16, name_ar="الدار البيضاء", name_fr="Dar El Beida", courier_ref="900")
        Product.objects.filter(pk=self.product.pk).update(courier_ref=self.PRODUCT_UUID)

    def fake_response(self, status_code=200, payload=None):
        response = MagicMock()
        response.status_code = status_code
        response.json.return_value = {} if payload is None else payload
        return response

    def send(self, order):
        return self.client.post("/api/shipping/send/", {"order_ids": [order["id"]]})

    def test_config_read_update_masks_token(self):
        response = self.client.get("/api/shipping/config/")
        self.assertCode(response, 200)
        self.assertEqual(response.data["mode"], "simulated")
        self.assertFalse(response.data["has_token"])
        self.assertEqual(response.data["status_map"]["41"], "delivered")
        self.assertTrue(response.data["webhook_path"].startswith("/api/shipping/webhook/"))
        updated = self.client.put("/api/shipping/config/", {"token": "abc123", "return_fee": 400})
        self.assertCode(updated, 200)
        self.assertTrue(updated.data["has_token"])
        self.assertNotIn("token", updated.data)
        config = MaystroConfig.load()
        self.assertEqual(config.get_token(), "abc123")
        self.assertNotIn("abc123", config.encrypted_token)

    def test_status_map_validated(self):
        self.assertCode(self.client.put("/api/shipping/config/", {"status_map": {"4": "nowhere"}}), 400)
        self.assertCode(self.client.put("/api/shipping/config/", {"status_map": {"41": "delivered"}}), 200)

    def test_simulated_connection_and_sync(self):
        self.assertEqual(self.client.post("/api/shipping/config/test/").data, {"connected": True})
        self.shipped_order()
        response = self.client.post("/api/shipping/sync/")
        self.assertCode(response, 200)
        self.assertEqual((response.data["checked"], response.data["changed"]), (1, 0))
        self.assertTrue(MaystroConfig.load().last_sync_ok)

    def test_live_mode_requires_token(self):
        self.enable_live(token="")
        self.assertCode(self.client.post("/api/shipping/config/test/"), 502, "maystro_not_configured")

    @patch("apps.shipping.maystro.requests.request")
    def test_connection_test_uses_documented_endpoint(self, mocked):
        self.enable_live()
        mocked.return_value = self.fake_response(payload=[{"display_id": 1, "code": 1, "name": "Adrar"}])
        self.assertEqual(self.client.post("/api/shipping/config/test/").data, {"connected": True})
        args, kwargs = mocked.call_args
        self.assertEqual(args, ("GET", "https://orders-management.maystro-delivery.com/api/base/wilayas/"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Token secret-token")

    @patch("apps.shipping.maystro.requests.request")
    def test_live_create_matches_documented_format(self, mocked):
        self.enable_live()
        self.link_catalog()
        mocked.return_value = self.fake_response(201, self.MAYSTRO_ORDER)
        order = self.confirmed_order(address="حي 20 أوت", note="مساءً")
        response = self.send(order)
        self.assertEqual(response.data["sent"][0]["tracking_number"], "000217543")
        args, kwargs = mocked.call_args
        self.assertEqual(args, ("POST", "https://orders-management.maystro-delivery.com/api/orders/"))
        self.assertEqual(
            kwargs["json"],
            {
                "external_id": order["reference"],
                "customer_name": "كريم مزيان",
                "customer_phone": "0555004112",
                "destination_text": "حي 20 أوت",
                "commune": 900,
                "delivery_type": 1,
                "total_price": 4500,
                "express": False,
                "note_to_driver": "مساءً",
                "details": [{"product": self.PRODUCT_UUID, "quantity": 1}],
            },
        )
        shipment = Shipment.objects.get(tracking_number="000217543")
        self.assertEqual(shipment.external_id, self.MAYSTRO_ORDER["id"])

    @patch("apps.shipping.maystro.requests.request")
    def test_stop_desk_code_and_french_commune_name(self, mocked):
        self.enable_live()
        self.link_catalog()
        mocked.return_value = self.fake_response(201, self.MAYSTRO_ORDER)
        self.send(self.confirmed_order(delivery_type="desk", commune="dar el beida"))
        payload = mocked.call_args[1]["json"]
        self.assertEqual((payload["delivery_type"], payload["commune"], payload["total_price"]), (2, 900, 4300))

    def assert_failed_without_side_effects(self, response, order, code):
        self.assertEqual(response.data["failed"][0]["code"], code)
        self.assertEqual(Order.objects.get(pk=order["id"]).status, "confirmed")
        self.assertEqual(Product.objects.get(pk=self.product.pk).on_hand, 100)
        self.assertEqual(Shipment.objects.count(), 0)

    def test_unlinked_commune_blocks_sending(self):
        self.enable_live()
        Product.objects.filter(pk=self.product.pk).update(courier_ref=self.PRODUCT_UUID)
        order = self.confirmed_order()
        self.assert_failed_without_side_effects(self.send(order), order, "commune_not_linked")

    def test_unlinked_product_blocks_sending(self):
        self.enable_live()
        Commune.objects.create(wilaya_id=16, name_ar="الدار البيضاء", courier_ref="900")
        order = self.confirmed_order()
        self.assert_failed_without_side_effects(self.send(order), order, "product_not_linked")

    @patch("apps.shipping.maystro.requests.request")
    def test_rejected_order_rolls_back_stock(self, mocked):
        self.enable_live()
        self.link_catalog()
        mocked.return_value = self.fake_response(400, {"detail": "wilaya invalid", "status_code": 400})
        order = self.confirmed_order()
        response = self.send(order)
        self.assert_failed_without_side_effects(response, order, "maystro_rejected")
        self.assertIn("wilaya invalid", response.data["failed"][0]["detail"])

    @patch("apps.shipping.maystro.requests.request")
    def test_unreachable_courier(self, mocked):
        self.enable_live()
        mocked.side_effect = requests.ConnectionError()
        self.assertCode(self.client.post("/api/shipping/config/test/"), 502, "maystro_unreachable")
        self.assertEqual(mocked.call_count, 4)

    @patch("apps.shipping.maystro.requests.request")
    def test_reading_survives_a_dropped_connection(self, mocked):
        self.enable_live()
        mocked.side_effect = [requests.ConnectionError(), self.fake_response(503), self.fake_response(payload=[])]
        self.assertEqual(self.client.post("/api/shipping/config/test/").data, {"connected": True})
        self.assertEqual(mocked.call_count, 3)

    @patch("apps.shipping.maystro.requests.request")
    def test_creating_a_parcel_is_never_retried(self, mocked):
        self.enable_live()
        self.link_catalog()
        mocked.side_effect = requests.ConnectionError()
        order = self.confirmed_order()
        response = self.send(order)
        self.assertEqual(response.data["failed"][0]["code"], "maystro_unreachable")
        self.assertEqual([call.args[0] for call in mocked.call_args_list].count("POST"), 1)
        self.assertEqual(Order.objects.get(pk=order["id"]).status, "confirmed")

    @patch("apps.shipping.maystro.requests.request")
    def test_missing_display_id_in_response(self, mocked):
        self.enable_live()
        self.link_catalog()
        mocked.return_value = self.fake_response(201, {"success": True, "delivery_price": 400})
        order = self.confirmed_order()
        self.assert_failed_without_side_effects(self.send(order), order, "maystro_bad_response")

    def test_live_sync_reads_status_by_uuid_once(self):
        order_id, tracking = self.shipped_order()
        Shipment.objects.filter(tracking_number=tracking).update(external_id="uuid-77")
        self.enable_live()
        with patch("apps.shipping.maystro.requests.request") as mocked:
            mocked.return_value = self.fake_response(payload=[{"status": 31, "created_at": timezone.now().isoformat()}, {"status": 9, "created_at": (timezone.now() - timedelta(hours=2)).isoformat()}])
            first = self.client.post("/api/shipping/sync/")
            second = self.client.post("/api/shipping/sync/")
        self.assertEqual(mocked.call_args[0], ("GET", "https://orders-management.maystro-delivery.com/api/orders/history-order/uuid-77/"))
        self.assertEqual((first.data["checked"], first.data["changed"]), (1, 1))
        self.assertEqual((second.data["checked"], second.data["changed"]), (1, 0))
        shipment = Shipment.objects.get(tracking_number=tracking)
        self.assertEqual((shipment.stage, shipment.events.count()), ("out", 1))
        self.assertEqual(Order.objects.get(pk=order_id).status, "shipping")

    def test_sync_failure_is_recorded(self):
        self.shipped_order()
        self.enable_live()
        with patch("apps.shipping.maystro.requests.request", side_effect=requests.Timeout()):
            self.assertCode(self.client.post("/api/shipping/sync/"), 502, "maystro_unreachable")
        config = MaystroConfig.load()
        self.assertFalse(config.last_sync_ok)
        self.assertTrue(config.last_sync_error)

    PRODUCTS_URL = "https://orders-management.maystro-delivery.com/api/stock/products/"

    def catalog_reply(self, method, url, **kwargs):
        params = kwargs.get("params") or {}
        if url.endswith("/api/base/wilayas/"):
            return self.fake_response(
                payload=[
                    {"display_id": 1, "code": 1, "name": "Adrar", "center_commune": 2},
                    {"display_id": 16, "code": 16, "name": "Alger", "center_commune": 900},
                ]
            )
        if url.endswith("/api/base/communes/"):
            communes = {1: [{"id": 2, "wilaya": 1, "name": "Tamest"}], 16: [{"id": 900, "wilaya": 16, "name": "Dar El Beida"}]}
            return self.fake_response(payload=communes[params["wilaya"]])
        if url.endswith("/api/base/delivery-options/"):
            options = {2: [{"type": "home", "price": 700}], 900: [{"type": "home", "price": 700}, {"type": "stopdesk", "price": 450}]}
            return self.fake_response(payload=options[params["commune"]])
        if url == self.PRODUCTS_URL:
            return self.fake_response(
                payload={"next": f"{self.PRODUCTS_URL}?page=2", "results": [{"id": self.PRODUCT_UUID, "logistical_description": "المنتج الأول", "product_id": ""}]}
            )
        if url == f"{self.PRODUCTS_URL}?page=2":
            return self.fake_response(payload={"next": None, "results": [{"id": "other", "logistical_description": "x", "product_id": "z"}]})
        raise AssertionError(url)

    def test_catalog_sync_links_everything(self):
        self.enable_live()
        with patch("apps.shipping.maystro.requests.request", side_effect=self.catalog_reply):
            response = self.client.post("/api/shipping/config/sync-catalog/")
        self.assertCode(response, 200)
        self.assertEqual(response.data, {"wilayas": 2, "communes": 2, "products_linked": 1, "products_unlinked": ["المنتج الثاني"]})
        algiers = Wilaya.objects.get(pk=16)
        self.assertEqual((algiers.courier_ref, algiers.home_fee, algiers.desk_fee, algiers.desk_available), ("16", 700, 450, True))
        adrar = Wilaya.objects.get(pk=1)
        self.assertEqual((adrar.home_available, adrar.desk_available), (True, False))
        self.assertFalse(Wilaya.objects.get(pk=11).home_available)
        self.assertEqual(Commune.objects.get(wilaya_id=16).courier_ref, "900")
        self.assertEqual(Product.objects.get(pk=self.product.pk).courier_ref, self.PRODUCT_UUID)

    def test_catalog_sync_requires_live_mode(self):
        self.assertCode(self.client.post("/api/shipping/config/sync-catalog/"), 409, "maystro_simulated")

    def test_catalog_sync_reports_failure(self):
        self.enable_live()
        with patch("apps.shipping.maystro.requests.request", side_effect=requests.Timeout()):
            self.assertCode(self.client.post("/api/shipping/config/sync-catalog/"), 502, "maystro_unreachable")

    def test_sync_command(self):
        self.shipped_order()
        with open("NUL", "w", encoding="utf-8") as sink:
            call_command("sync_maystro", stdout=sink)
        self.assertIsNotNone(MaystroConfig.load().last_sync_at)


class MaystroMoneyTests(ApiTestCase):
    def mirror(self, number, status, price, shipped=True, delivered_days_ago=None, return_fees=None, refund=False):
        now = timezone.now()
        return MaystroOrder.objects.create(
            maystro_id=f"m-{number}",
            display_id=f"D{number}",
            status=status,
            product_price=price,
            delivery_price=400,
            total_price=price + 400,
            return_fees=return_fees,
            is_refund=refund,
            wilaya="Alger",
            products=[{"product_id": "p-1", "name": "منتج تجريبي", "quantity": 1}],
            ordered_at=now - timedelta(days=12),
            shipped_at=now - timedelta(days=11) if shipped else None,
            delivered_at=now - timedelta(days=delivered_days_ago) if delivered_days_ago is not None else None,
        )

    def seed(self):
        MaystroConfig.objects.filter(pk=MaystroConfig.load().pk).update(return_fee=300)
        self.mirror(1, 41, 5000, delivered_days_ago=10)
        self.mirror(2, 41, 3000, delivered_days_ago=2)
        self.mirror(3, 50, 4000)
        self.mirror(4, 50, 4500, shipped=False)
        self.mirror(5, 52, 2500, return_fees=200)
        self.mirror(6, 31, 6000)
        self.mirror(7, 41, 9000, delivered_days_ago=1, refund=True)

    def test_balance_is_delivered_minus_payments_minus_return_fees(self):
        self.seed()
        added = self.client.post("/api/shipping/maystro/payments/", {"amount": 4000, "paid_on": timezone.localdate().isoformat(), "note": "تحويل"})
        self.assertCode(added, 201)
        self.assertEqual((added.data["source"], added.data["amount"]), ("manual", 4000))
        data = self.client.get("/api/shipping/maystro/balance/").data
        self.assertEqual((data["delivered_value"], data["delivered_orders"]), (8000, 2))
        self.assertEqual((data["returned_orders"], data["estimated_return_fees"], data["return_fees_known_orders"]), (2, 500, 1))
        self.assertEqual((data["paid_to_you"], data["estimated_held"], data["is_estimate"]), (4000, 3500, True))
        listing = self.client.get("/api/shipping/maystro/payments/")
        self.assertCode(listing, 200)
        self.assertEqual(listing.data["results"][0]["created_by_name"], "أمين بوزيد")

    def test_status_50_splits_into_returned_and_cancelled(self):
        self.seed()
        data = self.client.get("/api/shipping/maystro/summary/").data
        counts = data["counts"]
        self.assertEqual((counts["delivered"], counts["returned"], counts["cancelled"], counts["shipped"], counts["refunds"]), (2, 2, 1, 4, 1))
        self.assertEqual((data["delivery_rate"], data["shipped_delivery_rate"]), (40.0, 50.0))
        keys = {row["key"] for row in data["by_status"]}
        self.assertTrue({"50_returned", "50_cancelled", "41", "52", "31"} <= keys)
        self.assertEqual(data["money"]["delivered_value"], 8000)
        self.assertEqual(data["money"]["in_delivery"], 6000)
        self.assertEqual(data["by_product"][0]["shipped_delivery_rate"], 50.0)
        orders = self.client.get("/api/shipping/maystro/orders/", {"group": "returned"}).data["results"]
        self.assertEqual({row["display_id"] for row in orders}, {"D3", "D5"})
        self.assertEqual({row["outcome"] for row in orders}, {"returned"})

    def test_start_date_excludes_older_history(self):
        self.seed()
        old_day = (timezone.localdate() - timedelta(days=30)).isoformat()
        self.client.post("/api/shipping/maystro/payments/", {"amount": 1000, "paid_on": old_day})
        start = (timezone.localdate() - timedelta(days=5)).isoformat()
        response = self.client.put("/api/shipping/maystro/balance/", {"start_date": start}, format="json")
        self.assertCode(response, 200)
        self.assertEqual(str(response.data["start_date"]), start)
        self.assertEqual((response.data["delivered_value"], response.data["paid_to_you"], response.data["returned_orders"]), (3000, 0, 0))
        cleared = self.client.put("/api/shipping/maystro/balance/", {"start_date": None}, format="json")
        self.assertEqual((cleared.data["start_date"], cleared.data["paid_to_you"]), (None, 1000))

    def test_only_manual_payments_can_be_deleted(self):
        manual = self.client.post("/api/shipping/maystro/payments/", {"amount": 700, "paid_on": "2026-11-01"}).data
        self.assertCode(self.client.delete(f"/api/shipping/maystro/payments/{manual['id']}/"), 204)
        webhook = MaystroPayment.objects.create(fingerprint="f" * 64, paid_on=timezone.now(), amount=100)
        self.assertCode(self.client.delete(f"/api/shipping/maystro/payments/{webhook.pk}/"), 409, "payment_locked")

    def test_payment_validation(self):
        self.assertCode(self.client.post("/api/shipping/maystro/payments/", {"amount": 0, "paid_on": "2026-11-01"}), 400)
        self.assertCode(self.client.post("/api/shipping/maystro/payments/", {"amount": 10, "paid_on": "nope"}), 400)

    def test_money_requires_dues_section(self):
        self.as_user(self.agent)
        for method, url in (
            ("get", "/api/shipping/maystro/summary/"),
            ("get", "/api/shipping/maystro/orders/"),
            ("get", "/api/shipping/maystro/balance/"),
            ("get", "/api/shipping/maystro/payments/"),
            ("post", "/api/shipping/maystro/payments/"),
        ):
            self.assertCode(getattr(self.client, method)(url), 403)
        self.assertNotIn("current_points", self.client.get("/api/shipping/config/").data)
        self.as_user(self.accountant)
        self.assertCode(self.client.get("/api/shipping/maystro/summary/"), 200)
        self.assertCode(self.client.get("/api/shipping/maystro/balance/"), 200)
