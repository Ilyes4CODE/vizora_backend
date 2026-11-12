from datetime import timedelta
from unittest.mock import patch

import requests

from django.utils import timezone

from apps.catalog.models import Product
from apps.orders.models import Order
from apps.shipping import live
from apps.shipping.models import MaystroConfig, MaystroOrder, MaystroProduct, ParcelCall, Shipment

from .test_maystro_mirror import FAKE_NAME, FAKE_PHONE, STORE_ORDERS, FakeMaystro, MirrorTestCase, remote_order, reply

HISTORY = "/api/orders/history-order/"


def alerted(index, reason="Le client ne répond pas", status=32, delivery_type=1, hours_ago=1):
    row = remote_order(index, status)
    moment = (timezone.now() - timedelta(hours=hours_ago)).isoformat()
    row.update({"alert_reason_name": reason, "alerted_at": moment, "updated_at": moment, "delivery_type": delivery_type, "destination_text": "حي النصر"})
    return row


class LiveMaystro(FakeMaystro):
    def __init__(self, rows, histories=None, **kwargs):
        super().__init__([rows], **kwargs)
        self.rows = rows
        self.histories = histories or {}

    def __call__(self, method, url, headers=None, timeout=None, params=None, json=None, **kwargs):
        params = dict(params or {})
        if url.startswith(STORE_ORDERS) and params.get("status"):
            self.calls.append((method, url, params))
            wanted = {int(value) for value in str(params["status"]).split(",")}
            rows = [row for row in self.rows if row["status"] in wanted]
            return self.reply({"list": {"count": len(rows), "next": None, "previous": None, "results": rows}})
        if HISTORY in url:
            self.calls.append((method, url, params))
            key = url.rstrip("/").rsplit("/", 1)[-1]
            return self.reply(self.histories.get(key, []))
        return super().__call__(method, url, headers=headers, timeout=timeout, params=params, json=json, **kwargs)

    @staticmethod
    def reply(payload):
        return reply(200, payload)

    def status_filters(self):
        return [params.get("status") for method, url, params in self.calls if url.startswith(STORE_ORDERS) and params.get("status")]

    def history_calls(self):
        return [url for method, url, params in self.calls if HISTORY in url]


class LiveTestCase(MirrorTestCase):
    def call(self, fake, method, path, data=None):
        with patch("apps.shipping.maystro.requests.request", side_effect=fake):
            return getattr(self.client, method)(path, data or {})


class LiveParcelTests(LiveTestCase):
    def test_parcels_come_from_maystro_active_orders(self):
        fake = LiveMaystro([alerted(1), remote_order(2, 9), remote_order(3, 33), remote_order(4, 41)])
        response = self.call(fake, "get", "/api/shipping/parcels/")
        self.assertCode(response, 200)
        self.assertEqual(fake.status_filters(), ["4,5,6,8,9,11,12,15,22,31,32,33,42"])
        self.assertEqual(response.data["count"], 3)
        first = next(row for row in response.data["results"] if row["tracking_number"] == "90000001")
        self.assertEqual((first["kind"], first["customer_name"], first["phone"], first["display_stage"]), ("maystro", FAKE_NAME, FAKE_PHONE, "stuck"))
        self.assertEqual(first["alert_reason"], "الزبون لا يرد")
        self.assertIn("الزبون لا يرد", first["status_label"])
        self.assertIsNone(first["order_id"])
        self.assertEqual(first["products"], "1 منتج أ")

    def test_counts_split_parcels_by_stage(self):
        rows = [alerted(1), remote_order(2, 9), remote_order(3, 33), remote_order(4, 31), alerted(5, status=31, delivery_type=2)]
        response = self.call(LiveMaystro(rows), "get", "/api/shipping/parcels/counts/")
        self.assertCode(response, 200)
        data = response.data
        self.assertEqual((data["all"], data["transit"], data["out"], data["desk"], data["stuck"]), (5, 1, 1, 2, 1))
        self.assertTrue(data["live"])
        self.assertIsNotNone(data["refreshed_at"])

    def test_maystro_is_asked_at_most_once_in_a_short_while(self):
        fake = LiveMaystro([remote_order(1, 31)])
        self.call(fake, "get", "/api/shipping/parcels/")
        self.call(fake, "get", "/api/shipping/parcels/counts/")
        self.call(fake, "get", "/api/confirmation/queue/", {"stage": "suivi"})
        self.assertEqual(len(fake.status_filters()), 1)
        MaystroConfig.objects.filter(pk=1).update(live_parcels_at=timezone.now() - timedelta(minutes=5))
        live.forget_attempts()
        self.call(fake, "get", "/api/shipping/parcels/")
        self.assertEqual(len(fake.status_filters()), 2)

    def test_parcels_that_left_the_road_are_settled_and_forget_the_customer(self):
        self.call(LiveMaystro([remote_order(1, 31), remote_order(2, 9)]), "get", "/api/shipping/parcels/")
        MaystroConfig.objects.filter(pk=1).update(live_parcels_at=timezone.now() - timedelta(minutes=5))
        live.forget_attempts()
        uuid = remote_order(1, 31)["id"]
        fake = LiveMaystro([remote_order(2, 9)], histories={uuid: [{"status": 41, "created_at": timezone.now().isoformat(), "comment": None}]})
        response = self.call(fake, "get", "/api/shipping/parcels/")
        self.assertEqual([row["tracking_number"] for row in response.data["results"]], ["90000002"])
        self.assertEqual(len(fake.history_calls()), 1)
        settled = MaystroOrder.objects.get(maystro_id=uuid)
        self.assertEqual((settled.status, settled.customer_name, settled.customer_phone), (41, "", ""))
        self.assertIsNotNone(settled.delivered_at)

    def test_delivered_tab_reads_the_mirror(self):
        rows = [remote_order(1, 41), remote_order(2, 31)]
        self.sync(FakeMaystro([rows]))
        response = self.call(LiveMaystro(rows), "get", "/api/shipping/parcels/", {"stage": "delivered"})
        self.assertEqual([row["tracking_number"] for row in response.data["results"]], ["90000001"])
        self.assertEqual(response.data["results"][0]["customer_name"], "")

    def test_search_by_customer_phone(self):
        other = remote_order(2, 31)
        other["customer_phone"] = "0661000000"
        response = self.call(LiveMaystro([remote_order(1, 31), other]), "get", "/api/shipping/parcels/", {"search": "0661"})
        self.assertEqual([row["tracking_number"] for row in response.data["results"]], ["90000002"])

    def test_parcel_detail_reads_history_from_maystro(self):
        row = alerted(1)
        history = [
            {"status": 32, "created_at": timezone.now().isoformat(), "comment": "Le client ne répond pas"},
            {"status": 31, "created_at": (timezone.now() - timedelta(hours=3)).isoformat(), "comment": None},
        ]
        fake = LiveMaystro([row], histories={row["id"]: history})
        self.call(fake, "get", "/api/shipping/parcels/")
        parcel = MaystroOrder.objects.get()
        response = self.call(fake, "get", f"/api/shipping/parcels/{parcel.pk}/")
        self.assertCode(response, 200)
        self.assertEqual([event["raw_status"] for event in response.data["events"]], ["32", "31"])
        self.assertIn("الزبون لا يرد", response.data["events"][0]["message"])
        self.assertEqual(response.data["events_error"], "")

    def test_offline_maystro_keeps_the_last_list_and_says_why(self):
        self.call(LiveMaystro([remote_order(1, 31)]), "get", "/api/shipping/parcels/")
        MaystroConfig.objects.filter(pk=1).update(live_parcels_at=timezone.now() - timedelta(minutes=5))
        live.forget_attempts()
        with patch("apps.shipping.maystro.requests.request", side_effect=requests.ConnectionError()) as mocked:
            response = self.client.get("/api/shipping/parcels/")
            counts = self.client.get("/api/shipping/parcels/counts/")
        self.assertCode(response, 200)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(response.data["count"], 1)
        self.assertTrue(counts.data["error"])

    def test_sync_now_refreshes_and_reports_failures(self):
        fake = LiveMaystro([remote_order(1, 31)])
        self.assertCode(self.call(fake, "post", "/api/shipping/sync/"), 200)
        self.assertEqual(len(fake.status_filters()), 1)
        with patch("apps.shipping.maystro.requests.request", side_effect=requests.ConnectionError()):
            self.assertCode(self.client.post("/api/shipping/sync/"), 502, "maystro_unreachable")
        self.assertFalse(MaystroConfig.load().last_sync_ok)

    def test_status_comes_from_maystro_and_cannot_be_set_by_hand(self):
        self.call(LiveMaystro([remote_order(1, 31)]), "get", "/api/shipping/parcels/")
        parcel = MaystroOrder.objects.get()
        self.assertCode(self.client.post(f"/api/shipping/parcels/{parcel.pk}/status/", {"stage": "delivered"}), 409, "status_from_maystro")
        self.assertCode(self.client.post(f"/api/shipping/parcels/{parcel.pk}/receive-return/", {"condition": "good"}), 409, "parcel_not_linked")

    def test_orders_sent_from_vizora_follow_the_maystro_list(self):
        self.set_mode("simulated")
        order_id, tracking = self.shipped_order()
        self.set_mode("live")
        shipment = Shipment.objects.get(tracking_number=tracking)
        row = remote_order(1, 33)
        Shipment.objects.filter(pk=shipment.pk).update(external_id=row["id"])
        response = self.call(LiveMaystro([row]), "get", "/api/shipping/parcels/")
        self.assertEqual(response.data["results"][0]["order_id"], order_id)
        shipment.refresh_from_db()
        self.assertEqual(shipment.stage, "desk")

    def test_linked_return_is_received_through_the_live_list(self):
        self.set_mode("simulated")
        order_id, tracking = self.shipped_order()
        self.set_mode("live")
        row = remote_order(1, 31)
        Shipment.objects.filter(tracking_number=tracking).update(external_id=row["id"], stage="returned")
        self.call(LiveMaystro([row]), "get", "/api/shipping/parcels/")
        parcel = MaystroOrder.objects.get()
        response = self.client.post(f"/api/shipping/parcels/{parcel.pk}/receive-return/", {"condition": "damaged"})
        self.assertCode(response, 200)
        self.assertEqual(response.data["return_condition"], "damaged")

    def test_dashboard_counts_maystro_parcels(self):
        response = self.call(LiveMaystro([alerted(1), remote_order(2, 9)]), "get", "/api/shipping/parcels/")
        self.assertCode(response, 200)
        today = self.client.get("/api/dashboard/").data["today"]
        self.assertEqual((today["in_shipping"], today["stuck_parcels"], today["suivi_due"]), (2, 1, 1))


class LiveSuiviTests(LiveTestCase):
    def queue(self, fake):
        response = self.call(fake, "get", "/api/confirmation/queue/", {"stage": "suivi"})
        self.assertCode(response, 200)
        return response.data["results"]

    def test_suivi_lists_parcels_that_need_a_call_alerts_first(self):
        fake = LiveMaystro([remote_order(1, 31), alerted(2), remote_order(3, 9), remote_order(4, 33)])
        items = self.queue(fake)
        self.assertEqual([item["reference"] for item in items], ["90000002", "90000004", "90000001"])
        first = items[0]
        self.assertEqual((first["kind"], first["customer_name"], first["phone"], first["status"]), ("maystro", FAKE_NAME, FAKE_PHONE, "shipping"))
        self.assertEqual(first["shipment"]["tracking_number"], "90000002")
        self.assertIn("الزبون لا يرد", first["shipment"]["stage_label"])
        counts = self.call(fake, "get", "/api/confirmation/counts/")
        self.assertEqual(counts.data["suivi"], 3)

    def test_recorded_call_hides_the_parcel_until_tomorrow(self):
        fake = LiveMaystro([alerted(1)])
        parcel_id = self.queue(fake)[0]["id"]
        self.as_user(self.agent)
        response = self.client.post(f"/api/confirmation/parcels/{parcel_id}/outcome/", {"outcome": "will_receive", "note": "يستلم مساءً"})
        self.assertCode(response, 200)
        self.assertEqual(response.data["suivi_attempts"], 1)
        self.assertEqual(response.data["events"][0]["note"], "يستلم مساءً")
        self.assertIn("سارة قاسمي", response.data["events"][0]["message"])
        self.assertEqual(self.queue(fake), [])
        call = ParcelCall.objects.get()
        self.assertEqual((call.outcome, call.user_id), ("will_receive", self.agent.pk))

    def test_new_maystro_status_brings_the_parcel_back(self):
        parcel_id = self.queue(LiveMaystro([remote_order(1, 31)]))[0]["id"]
        self.assertCode(self.client.post(f"/api/confirmation/parcels/{parcel_id}/outcome/", {"outcome": "will_receive"}), 200)
        MaystroConfig.objects.filter(pk=1).update(live_parcels_at=timezone.now() - timedelta(minutes=5))
        live.forget_attempts()
        items = self.queue(LiveMaystro([alerted(1)]))
        self.assertEqual([item["id"] for item in items], [parcel_id])

    def test_postponed_call_needs_a_time(self):
        parcel_id = self.queue(LiveMaystro([alerted(1)]))[0]["id"]
        self.assertCode(self.client.post(f"/api/confirmation/parcels/{parcel_id}/outcome/", {"outcome": "postponed"}), 400)
        self.assertCode(self.client.post(f"/api/confirmation/parcels/{parcel_id}/outcome/", {"outcome": "confirmed"}), 400)
        later = (timezone.now() + timedelta(hours=3)).isoformat()
        response = self.client.post(f"/api/confirmation/parcels/{parcel_id}/outcome/", {"outcome": "postponed", "next_call_at": later})
        self.assertCode(response, 200)
        self.assertIsNotNone(MaystroOrder.objects.get(pk=parcel_id).suivi_next_at)

    def test_call_on_a_parcel_sent_from_vizora_lands_in_the_order_log(self):
        self.set_mode("simulated")
        order_id, tracking = self.shipped_order()
        self.set_mode("live")
        row = remote_order(1, 31)
        Shipment.objects.filter(tracking_number=tracking).update(external_id=row["id"])
        parcel_id = self.queue(LiveMaystro([row]))[0]["id"]
        self.assertCode(self.client.post(f"/api/confirmation/parcels/{parcel_id}/outcome/", {"outcome": "no_answer"}), 200)
        self.assertTrue(Order.objects.get(pk=order_id).events.filter(stage="suivi", outcome="no_answer").exists())

    def test_simulated_mode_keeps_the_local_suivi(self):
        self.set_mode("simulated")
        with patch("apps.shipping.maystro.requests.request") as mocked:
            response = self.client.get("/api/confirmation/queue/", {"stage": "suivi"})
        self.assertCode(response, 200)
        mocked.assert_not_called()


class LiveStockTests(LiveTestCase):
    STOCK = [
        {"warehouse": 1, "warehouse_name": "ALG", "quantity": 40, "defective_quantity": 2, "incoming_quantity": 5, "outgoing_quantity": 0, "available_quantity": 30},
        {"warehouse": 2, "warehouse_name": "ORN", "quantity": 0, "defective_quantity": 0, "incoming_quantity": 0, "outgoing_quantity": 0, "available_quantity": 0},
    ]
    REMOTE = [
        {"id": "p-1", "display_id": 1, "product_id": "P-1", "logistical_description": "المنتج الأول", "delivery_rate": 50.0, "cancelation_rate": 10.0, "deleted": False, "last_used": None, "low_stock_level": 5},
        {"id": "p-9", "display_id": 9, "product_id": "", "logistical_description": "منتج جديد عند Maystro", "delivery_rate": 0.0, "cancelation_rate": 0.0, "deleted": False, "last_used": None, "low_stock_level": 7},
        {"id": "p-0", "display_id": 10, "product_id": "", "logistical_description": "منتج محذوف", "delivery_rate": 0.0, "cancelation_rate": 0.0, "deleted": True, "last_used": None, "low_stock_level": 5},
    ]

    def fake(self):
        return LiveMaystro([remote_order(1, 31, product=("p-1", "المنتج الأول"))], products=self.REMOTE, stock=self.STOCK)

    def products(self, fake=None):
        response = self.call(fake or self.fake(), "get", "/api/products/", {"is_active": True})
        self.assertCode(response, 200)
        return {item["name"]: item for item in response.data}

    def test_stock_is_read_from_maystro_warehouses(self):
        fake = self.fake()
        self.call(fake, "get", "/api/shipping/parcels/")
        products = self.products(fake)
        first = products["المنتج الأول"]
        self.assertEqual(first["stock_source"], "maystro")
        self.assertEqual((first["on_hand"], first["available"], first["defective"], first["incoming"], first["at_courier"]), (40, 30, 2, 5, 1))
        self.assertEqual(first["warehouses"], [{"name": "ALG", "quantity": 40, "available": 30, "defective": 2, "incoming": 5}])
        self.assertIsNotNone(first["stock_synced_at"])
        self.assertEqual(Product.objects.get(name="المنتج الأول").courier_ref, "p-1")

    def test_maystro_products_missing_here_are_added(self):
        products = self.products()
        added = products["منتج جديد عند Maystro"]
        self.assertEqual((added["price"], added["low_stock_threshold"], added["courier_ref"], added["stock_source"]), (0, 7, "p-9", "maystro"))
        self.assertNotIn("منتج محذوف", products)
        self.products()
        self.assertEqual(Product.objects.filter(courier_ref="p-9").count(), 1)

    def test_manual_stock_changes_are_refused_for_maystro_products(self):
        self.products()
        response = self.client.post(f"/api/products/{self.product.pk}/stock-in/", {"quantity": 5})
        self.assertCode(response, 409, "stock_from_maystro")
        self.assertCode(self.client.post(f"/api/products/{self.product_b.pk}/stock-in/", {"quantity": 5}), 201)

    def test_confirming_more_than_maystro_holds_is_refused(self):
        self.products()
        order = self.new_order(items=[{"product": self.product.pk, "quantity": 31}])
        response = self.client.post(f"/api/confirmation/orders/{order['id']}/outcome/", {"outcome": "confirmed"})
        self.assertCode(response, 409, "insufficient_stock")
        smaller = self.new_order(phone="0661 00 41 13", items=[{"product": self.product.pk, "quantity": 30}])
        self.assertCode(self.client.post(f"/api/confirmation/orders/{smaller['id']}/outcome/", {"outcome": "confirmed"}), 200)

    def test_sync_button_pulls_stock_now(self):
        self.products()
        changed = [dict(self.STOCK[0], available_quantity=12)]
        fake = LiveMaystro([], products=self.REMOTE, stock=changed)
        response = self.call(fake, "post", "/api/products/sync-maystro/")
        self.assertCode(response, 200)
        first = next(item for item in response.data["products"] if item["name"] == "المنتج الأول")
        self.assertEqual(first["available"], 12)
        self.assertIsNotNone(response.data["synced_at"])

    def test_stale_stock_refreshes_in_the_background(self):
        self.products()
        MaystroConfig.objects.filter(pk=1).update(live_stock_at=timezone.now() - timedelta(minutes=10))
        live.forget_attempts()
        started = []
        with patch("apps.shipping.live.threading.Thread") as thread:
            thread.return_value.start.side_effect = lambda: started.append(True)
            with patch("apps.shipping.maystro.requests.request") as mocked:
                response = self.client.get("/api/products/")
        self.assertCode(response, 200)
        mocked.assert_not_called()
        self.assertEqual(started, [True])

    def test_sync_button_needs_live_mode(self):
        self.set_mode("simulated")
        self.assertCode(self.client.post("/api/products/sync-maystro/"), 409, "maystro_simulated")
        self.assertEqual(self.client.get("/api/products/").data[0]["stock_source"], "local")

    def test_unlinked_or_deleted_products_keep_local_stock(self):
        MaystroProduct.objects.create(maystro_id="gone", name="x", deleted=True)
        Product.objects.filter(pk=self.product_b.pk).update(courier_ref="gone")
        with patch("apps.shipping.maystro.requests.request", side_effect=requests.ConnectionError()):
            response = self.client.get("/api/products/")
        item = next(row for row in response.data if row["id"] == self.product_b.pk)
        self.assertEqual((item["stock_source"], item["on_hand"]), ("local", 100))
