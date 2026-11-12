import base64
import json
from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

import requests

from django.core.management import call_command
from django.utils import timezone

from apps.accounts.models import Role, RolePermission
from apps.core.models import AuditLog
from apps.shipping import live, maystro
from apps.shipping.models import MaystroConfig, MaystroOrder, MaystroPayment, MaystroProduct, Shipment

from .base import ApiTestCase

STORE_ORDERS = "https://backend.maystro-delivery.com/api/stores/orders/"
STORE_BALANCE = "https://backend.maystro-delivery.com/api/stores/payments/"
PAYMENTS = "https://orders-management.maystro-delivery.com/api/payments/"
FAKE_PHONE = "0550000000"
FAKE_NAME = "زبون تجريبي"


def reply(status_code=200, payload=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {} if payload is None else payload
    return response


def remote_order(index, status, product_price="3900.00", price=400, paid=False, refund=False, exchange=False, days_ago=1, wilaya="Alger", product=("p-1", "منتج أ"), total=None, shipped=True):
    moment = (timezone.now() - timedelta(days=days_ago)).isoformat()
    amount = int(float(product_price)) if product_price not in (None, "") else 0
    return {
        "id": f"00000000-0000-0000-0000-{index:012d}",
        "display_id": str(90000000 + index),
        "status": status,
        "product_price": product_price,
        "price": price,
        "total_price": total if total is not None else f"{amount + price}.00",
        "paid_to_store": paid,
        "is_refund": refund,
        "is_exchange": exchange,
        "delivery_type": 1,
        "wilaya": wilaya,
        "wilaya_id": 16,
        "commune_name": "Alger Centre",
        "commune_id": 554,
        "products": [{"product_id": product[0], "logistical_description": product[1], "quantity": 1}],
        "product_name": f"1 {product[1]};",
        "customer_name": FAKE_NAME,
        "customer_phone": FAKE_PHONE,
        "customer_phone2": "",
        "ordered_at": moment,
        "created_at": moment,
        "updated_at": moment,
        "shipped_at": moment if shipped else None,
        "delivered_at": moment if status == 41 else None,
        "abort_reason_name": None,
        "alert_reason_name": None,
    }


class FakeMaystro:
    def __init__(self, pages, products=None, stock=None, points=254.0, payments=None, balance=None):
        self.pages = pages
        self.payment_pages = payments if payments is not None else [[]]
        self.balance = balance if balance is not None else {"solde en cours": 0.0, "solde a payer": 0.0}
        self.products = products if products is not None else [
            {"id": "p-1", "display_id": 1, "product_id": "", "logistical_description": "منتج أ", "delivery_rate": 50.0, "cancelation_rate": 50.0, "deleted": False, "last_used": None},
        ]
        self.stock = stock if stock is not None else [{"warehouse": 1, "warehouse_name": "ALG", "quantity": 10, "defective_quantity": 0, "available_quantity": 8}]
        self.points = points
        self.calls = []

    def page_numbers(self):
        return [params.get("page") for method, url, params in self.calls if url.startswith(STORE_ORDERS)]

    def __call__(self, method, url, headers=None, timeout=None, params=None, json=None, **kwargs):
        params = dict(params or {})
        self.calls.append((method, url, params))
        if method != "GET":
            raise AssertionError(f"unexpected {method} {url}")
        if url.startswith(STORE_ORDERS):
            page = int(params.get("page", 1))
            results = self.pages[page - 1] if page <= len(self.pages) else []
            nxt = f"{STORE_ORDERS}?page={page + 1}" if page < len(self.pages) else None
            return reply(200, {"list": {"count": sum(len(p) for p in self.pages), "next": nxt, "previous": None, "results": results}})
        if url == STORE_BALANCE:
            return reply(200, self.balance)
        if url.startswith(PAYMENTS):
            page = int(params.get("page", 1))
            results = self.payment_pages[page - 1] if page <= len(self.payment_pages) else []
            nxt = f"{PAYMENTS}?page={page + 1}" if page < len(self.payment_pages) else None
            return reply(200, {"count": sum(len(p) for p in self.payment_pages), "next": nxt, "previous": None, "results": results})
        if "/api/stock/products/" in url:
            return reply(200, {"count": len(self.products), "next": None, "previous": None, "results": self.products})
        if "/api/stock/product-details/stock-details/" in url:
            return reply(200, self.stock)
        if "/api/base/delivery-options/" in url:
            return reply(200, [{"type": "home", "name": "Home", "price": 400, "min_total_price": 0, "required_points": 40.0, "current_points": self.points}])
        if "/api/base/wilayas/" in url:
            return reply(200, [{"display_id": 16, "code": 16, "name_lt": "Alger", "name_ar": "الجزائر", "country": 1, "center_commune": 554, "barcode": "ALG"}])
        if "/api/base/communes/" in url:
            return reply(200, [{"id": 554, "wilaya": 16, "name": "Alger Centre"}])
        raise AssertionError(f"unexpected url {url}")


def no_network(*args, **kwargs):
    raise AssertionError(f"unmocked Maystro call: {args[:2]}")


class MirrorTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        for patcher in (patch.object(maystro, "MIN_REQUEST_INTERVAL", 0), patch("apps.shipping.maystro.requests.request", side_effect=no_network)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.connect()

    def connect(self, on=True):
        config = MaystroConfig.load()
        config.set_token("synthetic-token" if on else "")
        config.balance_at = timezone.now()
        config.save()

    def sync(self, fake, full=True):
        with patch("apps.shipping.maystro.requests.request", side_effect=fake):
            return self.client.post("/api/shipping/maystro/sync/", {"full": full})

    def summary(self, **params):
        response = self.client.get("/api/shipping/maystro/summary/", params)
        self.assertCode(response, 200)
        return response.data

    def money_dataset(self):
        return [[
            remote_order(1, 41, "3900.00"),
            remote_order(2, 41, "5500.00", paid=True),
            remote_order(3, 41, "11900.00", price=1700),
            remote_order(4, 31, "3900.00"),
            remote_order(5, 33, "3900.00"),
            remote_order(6, 4, None, price=400, total="400.00"),
            remote_order(7, 52, "3900.00"),
            remote_order(8, 10, "3900.00"),
            remote_order(9, 50, "3900.00", shipped=False),
            remote_order(10, 41, "9999.00", refund=True),
            remote_order(11, 41, "2000.00", exchange=True, product=("p-2", "منتج ب")),
            remote_order(12, 50, "3900.00"),
        ]]


class MirrorSyncTests(MirrorTestCase):
    def test_full_sync_pages_and_is_idempotent(self):
        fake = FakeMaystro([[remote_order(1, 41), remote_order(2, 31)], [remote_order(3, 50)]])
        first = self.sync(fake)
        self.assertCode(first, 200)
        self.assertEqual((first.data["full"], first.data["pages"], first.data["fetched"], first.data["created"]), (True, 2, 3, 3))
        self.assertEqual(fake.page_numbers(), [1, 2])
        second = self.sync(FakeMaystro([[remote_order(1, 41), remote_order(2, 41)], [remote_order(3, 50)]]))
        self.assertEqual((second.data["created"], second.data["updated"]), (0, 3))
        self.assertEqual(MaystroOrder.objects.count(), 3)
        self.assertEqual(MaystroOrder.objects.get(display_id="90000002").status, 41)

    def test_full_sync_can_run_in_the_background(self):
        fake = FakeMaystro([[remote_order(1, 41), remote_order(2, 31)]])
        started = []

        class InlineThread:
            def __init__(self, target, args=(), daemon=None):
                self.run = lambda: target(*args)

            def start(self):
                started.append(MaystroConfig.load().mirror_running_since is not None)
                self.run()

        with patch("apps.shipping.maystro.requests.request", side_effect=fake), patch("apps.shipping.account.threading.Thread", InlineThread), patch("apps.shipping.account.connection.close"):
            response = self.client.post("/api/shipping/maystro/sync/", {"full": True, "background": True})
        self.assertCode(response, 202)
        self.assertEqual(response.data, {"started": True})
        self.assertEqual(started, [True])
        self.assertEqual(MaystroOrder.objects.count(), 2)
        sync_state = self.client.get("/api/shipping/maystro/summary/").data["sync"]
        self.assertEqual((sync_state["running"], sync_state["last_sync_ok"], sync_state["full_sync_done"]), (False, True, True))

    def test_background_sync_reports_failure_in_status(self):
        class InlineThread:
            def __init__(self, target, args=(), daemon=None):
                self.run = lambda: target(*args)

            def start(self):
                self.run()

        with patch("apps.shipping.maystro.requests.request", side_effect=requests.ConnectionError()), patch("apps.shipping.account.threading.Thread", InlineThread), patch("apps.shipping.account.connection.close"):
            response = self.client.post("/api/shipping/maystro/sync/", {"full": True, "background": True})
        self.assertCode(response, 202)
        config = MaystroConfig.load()
        self.assertFalse(config.mirror_sync_ok)
        self.assertIsNone(config.mirror_running_since)

    def test_sync_left_behind_by_a_stopped_server_is_reported_and_can_restart(self):
        MaystroConfig.objects.filter(pk=1).update(mirror_running_since=timezone.now() - timedelta(minutes=10), mirror_progress_done=400, mirror_progress_total=3684)
        state = self.client.get("/api/shipping/maystro/sync/").data
        self.assertEqual((state["running"], state["interrupted"]), (False, True))
        self.assertCode(self.sync(FakeMaystro([[remote_order(1, 41)]])), 200)
        state = self.client.get("/api/shipping/maystro/sync/").data
        self.assertEqual((state["running"], state["interrupted"], state["last_sync_ok"]), (False, False, True))

    def test_each_page_keeps_the_sync_alive(self):
        self.sync(FakeMaystro([[remote_order(1, 41)]]))
        beats = []
        fake = FakeMaystro([[remote_order(page * 2 + 1, 41, days_ago=1), remote_order(page * 2 + 2, 41, days_ago=1)] for page in range(4)])

        def watching(method, url, **kwargs):
            beats.append(MaystroConfig.objects.values_list("mirror_running_since", flat=True).get(pk=1))
            return fake(method, url, **kwargs)

        with patch("apps.shipping.maystro.requests.request", side_effect=watching):
            self.client.post("/api/shipping/maystro/sync/", {"full": False})
        self.assertGreater(len({beat for beat in beats if beat}), 1)

    def test_running_flag_is_visible_while_a_sync_is_in_progress(self):
        MaystroConfig.objects.filter(pk=1).update(mirror_running_since=timezone.now(), mirror_progress_done=40, mirror_progress_total=200)
        self.assertTrue(self.client.get("/api/shipping/maystro/summary/").data["sync"]["running"])
        status_response = self.client.get("/api/shipping/maystro/sync/")
        self.assertCode(status_response, 200)
        self.assertTrue(status_response.data["running"])
        self.assertEqual(status_response.data["progress"], {"done": 40, "total": 200})
        self.assertEqual(status_response.data, self.client.get("/api/shipping/maystro/summary/", {"period": "month"}).data["sync"])
        self.as_user(self.agent)
        self.assertCode(self.client.get("/api/shipping/maystro/sync/"), 403)
        self.as_user(self.admin)
        response = self.client.post("/api/shipping/maystro/sync/", {"full": True, "background": True})
        self.assertCode(response, 409, "sync_running")

    def test_full_sync_reads_every_page_once_and_reports_progress(self):
        pages = [[remote_order(page * 2 + 1, 41), remote_order(page * 2 + 2, 31)] for page in range(9)]
        fake = FakeMaystro(pages)
        result = self.sync(fake)
        self.assertCode(result, 200)
        self.assertEqual(sorted(fake.page_numbers()), list(range(1, 10)))
        self.assertEqual((result.data["pages"], result.data["fetched"], MaystroOrder.objects.count()), (9, 18, 18))
        sync_state = self.summary()["sync"]
        self.assertEqual(sync_state["progress"], {"done": 18, "total": 18})
        self.assertFalse(sync_state["running"])

    def test_full_sync_follows_pages_added_while_it_runs(self):
        fake = FakeMaystro([[remote_order(1, 41), remote_order(2, 41)], [remote_order(3, 41), remote_order(4, 41)], [remote_order(5, 41)]])
        original = fake.__call__

        def understated(method, url, **kwargs):
            response = original(method, url, **kwargs)
            body = response.json()
            if isinstance(body, dict) and "list" in body:
                body["list"]["count"] = 2
                response.json.return_value = body
            return response

        with patch("apps.shipping.maystro.requests.request", side_effect=understated):
            result = self.client.post("/api/shipping/maystro/sync/", {"full": True})
        self.assertCode(result, 200)
        self.assertEqual(sorted(fake.page_numbers()), [1, 2, 3])
        self.assertEqual(MaystroOrder.objects.count(), 5)

    def test_incremental_sync_reads_pages_one_by_one(self):
        self.sync(FakeMaystro([[remote_order(1, 41)]]))
        fake = FakeMaystro([[remote_order(5, 31, days_ago=1)], [remote_order(6, 41, days_ago=2)], [remote_order(7, 41, days_ago=90)], [remote_order(8, 41, days_ago=95)]])
        self.sync(fake, full=False)
        self.assertEqual(fake.page_numbers(), [1, 2, 3])

    def test_money_strings_are_stored_as_integer_dinars(self):
        self.sync(FakeMaystro([[remote_order(1, 41, "11900.00", price=1700)]]))
        row = MaystroOrder.objects.get()
        self.assertEqual((row.product_price, row.delivery_price, row.total_price), (11900, 1700, 13600))

    def test_null_prices_do_not_break_sync(self):
        self.assertCode(self.sync(FakeMaystro([[remote_order(1, 4, None, total=None)]])), 200)
        self.assertEqual(MaystroOrder.objects.get().product_price, 0)

    def test_mirror_keeps_customer_contact_only_while_the_parcel_is_on_the_road(self):
        self.sync(FakeMaystro([[remote_order(1, 41), remote_order(2, 50), remote_order(3, 31)]]))
        finished = json.dumps(list(MaystroOrder.objects.exclude(display_id="90000003").values()), default=str, ensure_ascii=False)
        self.assertNotIn(FAKE_PHONE, finished)
        self.assertNotIn(FAKE_NAME, finished)
        active = MaystroOrder.objects.get(display_id="90000003")
        self.assertEqual((active.customer_name, active.customer_phone), (FAKE_NAME, FAKE_PHONE))
        self.sync(FakeMaystro([[remote_order(1, 41), remote_order(2, 50), remote_order(3, 41)]]))
        delivered = MaystroOrder.objects.get(display_id="90000003")
        self.assertEqual((delivered.customer_name, delivered.customer_phone, delivered.address), ("", "", ""))

    def test_incremental_stops_after_old_page(self):
        self.sync(FakeMaystro([[remote_order(1, 41)]]))
        fake = FakeMaystro([[remote_order(5, 31, days_ago=1)], [remote_order(6, 41, days_ago=50)], [remote_order(7, 41, days_ago=90)]])
        result = self.sync(fake, full=False)
        self.assertCode(result, 200)
        self.assertFalse(result.data["full"])
        self.assertEqual(fake.page_numbers(), [1, 2])
        self.assertFalse(MaystroOrder.objects.filter(display_id="90000007").exists())

    def test_incremental_without_previous_full_runs_full(self):
        fake = FakeMaystro([[remote_order(1, 41, days_ago=1)], [remote_order(2, 41, days_ago=40)], [remote_order(3, 41, days_ago=80)]])
        result = self.sync(fake, full=False)
        self.assertTrue(result.data["full"])
        self.assertEqual(fake.page_numbers(), [1, 2, 3])

    def test_sync_only_uses_get_requests(self):
        fake = FakeMaystro(self.money_dataset())
        self.sync(fake)
        self.assertEqual({method for method, url, params in fake.calls}, {"GET"})

    def test_products_stock_and_points_are_refreshed(self):
        result = self.sync(FakeMaystro([[remote_order(1, 41)]], points=254.0))
        self.assertEqual(result.data["points"], 254.0)
        product = MaystroProduct.objects.get(maystro_id="p-1")
        self.assertEqual((product.name, product.delivery_rate), ("منتج أ", 50.0))
        self.assertEqual(product.stock[0]["available_quantity"], 8)
        self.assertEqual(MaystroConfig.load().current_points, 254.0)

    def test_links_crm_shipment_by_maystro_id(self):
        self.connect(False)
        _, tracking = self.shipped_order()
        self.connect()
        shipment = Shipment.objects.get(tracking_number=tracking)
        remote = remote_order(1, 31)
        Shipment.objects.filter(pk=shipment.pk).update(external_id=remote["id"])
        result = self.sync(FakeMaystro([[remote]]))
        self.assertEqual(result.data["linked"], 1)
        self.assertEqual(MaystroOrder.objects.get().shipment_id, shipment.pk)

    def test_requires_a_token(self):
        self.connect(False)
        self.assertCode(self.client.post("/api/shipping/maystro/sync/", {}), 409, "maystro_not_configured")

    def test_concurrent_sync_is_refused(self):
        MaystroConfig.objects.filter(pk=1).update(mirror_running_since=timezone.now() - timedelta(minutes=2))
        self.assertCode(self.sync(FakeMaystro([[]])), 409, "sync_running")
        MaystroConfig.objects.filter(pk=1).update(mirror_running_since=timezone.now() - timedelta(minutes=6))
        self.assertCode(self.sync(FakeMaystro([[]])), 200)

    def test_maystro_failure_is_recorded(self):
        with patch("apps.shipping.maystro.requests.request", return_value=reply(500, {"detail": "boom"})):
            response = self.client.post("/api/shipping/maystro/sync/", {"full": True})
        self.assertCode(response, 502)
        config = MaystroConfig.load()
        self.assertFalse(config.mirror_sync_ok)
        self.assertTrue(config.mirror_sync_error)
        self.assertIsNone(config.mirror_running_since)

    def test_management_command(self):
        with patch("apps.shipping.maystro.requests.request", side_effect=FakeMaystro([[remote_order(1, 41)]])):
            call_command("sync_maystro_orders", "--full", stdout=StringIO())
        self.assertEqual(MaystroOrder.objects.count(), 1)

    def test_config_exposes_store_id_and_mirror_state(self):
        self.assertCode(self.client.put("/api/shipping/config/", {"store_id": "synthetic-store"}), 200)
        self.sync(FakeMaystro([[remote_order(1, 41)]]))
        data = self.client.get("/api/shipping/config/").data
        self.assertEqual(data["store_id"], "synthetic-store")
        self.assertEqual(data["mirror_orders"], 1)
        self.assertTrue(data["mirror_sync_ok"])
        self.assertIsNotNone(data["mirror_full_sync_at"])
        self.assertNotIn("current_points", data)
        self.assertEqual(self.summary()["points"], 254.0)
        self.assertNotIn("token", data)


class MirrorSummaryTests(MirrorTestCase):
    REMOVED_MONEY_KEYS = {"held_by_maystro", "held_orders", "paid_to_store", "cod_collected", "return_fees_known"}

    def set_return_fee(self, fee):
        MaystroConfig.objects.filter(pk=MaystroConfig.load().pk).update(return_fee=fee)

    def pay(self, amount, days_ago=0):
        day = (timezone.localdate() - timedelta(days=days_ago)).isoformat()
        response = self.client.post("/api/shipping/maystro/payments/", {"amount": amount, "paid_on": day})
        self.assertCode(response, 201)
        return response.data

    def test_money_figures_follow_definitions(self):
        self.set_return_fee(300)
        self.sync(FakeMaystro(self.money_dataset()))
        data = self.summary()
        money = data["money"]
        self.assertEqual((money["delivered_value"], money["delivered_orders"]), (3900 + 5500 + 11900 + 2000, 4))
        self.assertEqual(money["delivered_total"], 4300 + 5900 + 13600 + 2400)
        self.assertEqual(money["delivery_fees"], 400 + 400 + 1700 + 400)
        self.assertEqual((money["in_delivery"], money["in_delivery_orders"]), (3900 + 3900 + 0, 3))
        self.assertEqual((money["paid_to_you"], money["payments_count"], money["paid_orders"]), (0, 0, 0))
        self.assertFalse(self.REMOVED_MONEY_KEYS & set(money))
        self.assertNotIn("estimated_return_fees", money)
        self.assertNotIn("payments", data)
        counts = data["counts"]
        self.assertEqual((counts["delivered"], counts["returned"], counts["cancelled"], counts["in_delivery"], counts["refunds"], counts["exchanges"]), (4, 3, 1, 3, 1, 1))
        self.assertEqual((counts["total"], counts["shipped"]), (11, 7))
        self.assertEqual(data["delivery_rate"], round(4 * 100 / 8, 1))
        self.assertEqual(data["shipped_delivery_rate"], round(4 * 100 / 7, 1))
        self.assertEqual(data["points"], 254.0)
        self.assertEqual(set(data["balance"]), {"current", "payable", "updated_at", "last_payment"})
        self.assertEqual(dict(data["balance"]), dict(self.client.get("/api/shipping/maystro/balance/").data))

    def test_paid_to_store_flag_is_not_money(self):
        self.sync(FakeMaystro([[remote_order(1, 41, "5500.00", paid=True)]]))
        data = self.summary()
        self.assertEqual((data["money"]["paid_to_you"], data["money"]["delivered_value"]), (0, 5500))

    def test_refunds_never_count_as_money(self):
        self.sync(FakeMaystro([[remote_order(1, 41, "9999.00", refund=True), remote_order(2, 52, "9999.00", refund=True)]]))
        data = self.summary()
        money = data["money"]
        self.assertEqual((money["delivered_value"], money["delivered_total"], money["delivery_fees"]), (0, 0, 0))
        self.assertEqual((data["counts"]["refunds"], data["counts"]["total"]), (2, 0))

    def test_status_50_is_one_row_like_maystro(self):
        self.sync(FakeMaystro(self.money_dataset()))
        data = self.summary()
        rows = {row["key"]: row for row in data["by_status"]}
        self.assertEqual([row["key"] for row in data["by_status"]], ["41", "50", "10", "31", "33", "4", "52"])
        self.assertEqual((rows["41"]["count"], rows["41"]["amount"], rows["41"]["status"]), (4, 23300, 41))
        self.assertEqual((rows["50"]["count"], rows["50"]["status"]), (2, 50))

    def test_return_fees_from_webhooks_survive_a_sync(self):
        rows = [remote_order(1, 52), remote_order(2, 41, "5000.00")]
        self.sync(FakeMaystro([rows]))
        MaystroOrder.objects.filter(display_id="90000001").update(return_fees=120)
        self.sync(FakeMaystro([rows]))
        self.assertEqual(MaystroOrder.objects.get(display_id="90000001").return_fees, 120)

    def test_breakdowns(self):
        self.sync(FakeMaystro(self.money_dataset()))
        data = self.summary()
        counts = [row["count"] for row in data["by_status"]]
        self.assertEqual(counts, sorted(counts, reverse=True))
        labels = {row["key"]: row["label"] for row in data["by_status"]}
        self.assertTrue(all(any("؀" <= char <= "ۿ" for char in label) for label in labels.values()))
        self.assertEqual(sum(row["count"] for row in data["by_status"]), data["counts"]["total"])
        products = {row["maystro_id"]: row for row in data["by_product"]}
        first = products["p-1"]
        self.assertEqual((first["orders"], first["delivered"], first["returned"], first["cancelled"]), (10, 3, 3, 1))
        self.assertEqual((first["delivery_rate"], first["shipped_delivery_rate"]), (round(300 / 7, 1), 50.0))
        self.assertEqual((products["p-2"]["orders"], products["p-2"]["delivery_rate"]), (1, 100.0))
        self.assertEqual(first["maystro_delivery_rate"], 50.0)
        self.assertEqual(first["available"], 8)
        self.assertEqual(data["by_product"][0]["maystro_id"], "p-1")
        wilaya = data["by_wilaya"][0]
        self.assertEqual((wilaya["wilaya"], wilaya["orders"], wilaya["delivered"], wilaya["returned"], wilaya["cancelled"]), ("Alger", 11, 4, 3, 1))
        self.assertEqual((wilaya["delivery_rate"], wilaya["shipped_delivery_rate"]), (50.0, round(400 / 7, 1)))
        self.assertEqual(sum(row["orders"] for row in data["by_day"]), 11)
        self.assertEqual(sum(row["delivered_value"] for row in data["by_day"]), 23300)
        self.assertEqual(sum(row["returned"] for row in data["by_day"]), 3)
        self.assertTrue(data["notes"])

    def test_wilayas_are_limited_to_ten(self):
        rows = [remote_order(index, 41, wilaya=f"W{index % 12:02d}") for index in range(1, 37)]
        self.sync(FakeMaystro([rows]))
        self.assertEqual(len(self.summary()["by_wilaya"]), 10)

    def test_empty_mirror(self):
        data = self.summary()
        self.assertEqual((data["money"]["delivered_value"], data["money"]["paid_to_you"]), (0, 0))
        self.assertEqual((data["balance"]["current"], data["balance"]["payable"], data["balance"]["last_payment"]), (None, None, None))
        self.assertEqual((data["delivery_rate"], data["shipped_delivery_rate"]), (0, 0))
        self.assertEqual(data["counts"]["total"], 0)
        self.assertFalse(data["sync"]["full_sync_done"])
        self.assertEqual((data["by_status"], data["by_product"], data["by_wilaya"], data["by_day"]), ([], [], [], []))

    def test_summary_answers_without_a_token(self):
        self.connect(False)
        self.summary()

    def test_period_filters(self):
        self.sync(FakeMaystro([[remote_order(1, 41, days_ago=0), remote_order(2, 41, days_ago=70)]]))
        self.assertEqual(self.summary()["counts"]["delivered"], 2)
        self.assertEqual(self.summary(period="month")["counts"]["delivered"], 1)
        today = timezone.localdate().isoformat()
        ranged = self.summary(date_from=today, date_to=today)
        self.assertEqual(ranged["counts"]["delivered"], 1)
        self.assertEqual(str(ranged["period"]["from"]), today)
        self.assertCode(self.client.get("/api/shipping/maystro/summary/", {"date_from": "nope"}), 400, "validation_error")
        self.assertCode(self.client.get("/api/shipping/maystro/summary/", {"period": "week"}), 400)

    def test_period_scopes_payments_but_not_balance(self):
        self.sync(FakeMaystro([[remote_order(1, 41, "9000.00", days_ago=0), remote_order(2, 41, "4000.00", days_ago=70)]]))
        MaystroPayment.objects.create(fingerprint="maystro:a", source="maystro", paid_on=timezone.now(), amount=1000, orders_count=1)
        MaystroPayment.objects.create(fingerprint="maystro:b", source="maystro", paid_on=timezone.now() - timedelta(days=70), amount=2500, orders_count=3)
        everything = self.summary()
        self.assertEqual((everything["money"]["paid_to_you"], everything["money"]["payments_count"], everything["money"]["paid_orders"]), (3500, 2, 4))
        month = self.summary(period="month")
        self.assertEqual((month["money"]["paid_to_you"], month["money"]["payments_count"], month["money"]["delivered_value"]), (1000, 1, 9000))
        self.assertEqual(dict(month["balance"]), dict(everything["balance"]))

    def test_orders_list_groups_search_and_privacy(self):
        self.sync(FakeMaystro(self.money_dataset()))
        url = "/api/shipping/maystro/orders/"
        expected = {"delivered": 4, "returned": 3, "cancelled": 1, "in_delivery": 3, "refunds": 1}
        for group, count in expected.items():
            self.assertEqual(self.client.get(url, {"group": group}).data["count"], count, group)
        for removed in ("held", "paid", "nope"):
            self.assertCode(self.client.get(url, {"group": removed}), 400, "validation_error")
        self.assertCode(self.client.get(url, {"status": "x"}), 400)
        self.assertEqual(self.client.get(url, {"search": "90000003"}).data["count"], 1)
        self.assertEqual(self.client.get(url, {"search": "منتج ب"}).data["count"], 1)
        self.assertEqual(self.client.get(url, {"status": 33}).data["count"], 1)
        item = self.client.get(url, {"search": "90000003"}).data["results"][0]
        self.assertEqual((item["product_price"], item["paid"], item["status"], item["outcome"]), (11900, False, 41, "delivered"))
        self.assertEqual(
            set(item),
            {
                "maystro_id", "display_id", "status", "status_label", "outcome", "product_names", "product_price", "delivery_price",
                "total_price", "paid", "wilaya", "commune_name", "ordered_at", "shipped_at", "delivered_at", "is_refund",
                "is_exchange", "shipment_id",
            },
        )
        cancelled = self.client.get(url, {"group": "cancelled"}).data["results"][0]
        returned = {row["display_id"]: row for row in self.client.get(url, {"group": "returned"}).data["results"]}
        self.assertEqual((cancelled["display_id"], cancelled["outcome"], cancelled["shipped_at"]), ("90000009", "cancelled", None))
        self.assertEqual(set(returned), {"90000007", "90000008", "90000012"})
        self.assertEqual({row["outcome"] for row in returned.values()}, {"returned"})
        self.assertNotEqual(returned["90000012"]["status_label"], cancelled["status_label"])
        listing = self.client.get(url).data
        self.assertEqual((listing["count"], set(listing)), (12, {"count", "next", "previous", "results"}))

    def test_orders_list_pages_by_twenty_newest_first(self):
        rows = [remote_order(index, 41, days_ago=index) for index in range(1, 26)]
        self.sync(FakeMaystro([rows]))
        first = self.client.get("/api/shipping/maystro/orders/").data
        self.assertEqual((first["count"], len(first["results"])), (25, 20))
        self.assertEqual(first["results"][0]["display_id"], "90000001")
        self.assertEqual(len(self.client.get("/api/shipping/maystro/orders/", {"page": 2}).data["results"]), 5)


class MaystroMoneyTests(MirrorTestCase):
    BALANCE = "/api/shipping/maystro/balance/"
    PAYMENTS_URL = "/api/shipping/maystro/payments/"

    def remote_payment(self, number, amount, orders, days_ago):
        moment = (timezone.now() - timedelta(days=days_ago)).isoformat()
        return {"id": f"pay-{number}", "payment_date": moment, "nb_orders": orders, "amount": f"{amount}.00"}

    def stale(self):
        MaystroConfig.objects.filter(pk=MaystroConfig.load().pk).update(balance_at=None)
        live.forget_attempts()

    def call(self, fake, path, **params):
        with patch("apps.shipping.maystro.requests.request", side_effect=fake):
            return self.client.get(path, params)

    def test_balance_and_payments_come_live_from_maystro(self):
        self.stale()
        payments = [[self.remote_payment(1, 60500, 5, 3), self.remote_payment(2, 169600, 17, 4)], [self.remote_payment(3, 505700, 50, 40)]]
        fake = FakeMaystro([[]], payments=payments, balance={"solde en cours": 322200.0, "solde a payer": 290650.0})
        data = self.call(fake, self.BALANCE).data
        self.assertEqual((data["current"], data["payable"]), (322200, 290650))
        self.assertEqual((data["last_payment"]["amount"], data["last_payment"]["orders_count"]), (60500, 5))
        self.assertIsNotNone(data["updated_at"])
        self.assertEqual(MaystroPayment.objects.filter(source="maystro").count(), 3)
        self.assertEqual({method for method, url, params in fake.calls}, {"GET"})

    def test_balance_is_asked_at_most_once_in_a_short_while(self):
        self.stale()
        fake = FakeMaystro([[]], balance={"solde en cours": 1000.0, "solde a payer": 500.0})
        self.call(fake, self.BALANCE)
        self.call(fake, self.PAYMENTS_URL)
        self.call(fake, "/api/shipping/maystro/summary/")
        balance_calls = [url for method, url, params in fake.calls if url == STORE_BALANCE]
        self.assertEqual(len(balance_calls), 1)

    def test_later_refresh_only_reads_new_payments(self):
        self.stale()
        first = [[self.remote_payment(2, 200, 2, 1)], [self.remote_payment(1, 100, 1, 9)]]
        self.call(FakeMaystro([[]], payments=first), self.BALANCE)
        self.stale()
        second = FakeMaystro([[]], payments=[[self.remote_payment(3, 300, 3, 0), self.remote_payment(2, 200, 2, 1)], [self.remote_payment(1, 100, 1, 9)]])
        self.call(second, self.BALANCE)
        pages = [params.get("page") for method, url, params in second.calls if url.startswith(PAYMENTS)]
        self.assertEqual(pages, [1])
        listing = self.client.get(self.PAYMENTS_URL).data
        self.assertEqual([row["amount"] for row in listing["results"]], [300, 200, 100])

    def test_offline_maystro_keeps_the_last_balance(self):
        MaystroConfig.objects.filter(pk=MaystroConfig.load().pk).update(balance_current=900, balance_payable=400)
        self.stale()
        with patch("apps.shipping.maystro.requests.request", side_effect=requests.ConnectionError()):
            data = self.client.get(self.BALANCE).data
        self.assertEqual((data["current"], data["payable"]), (900, 400))
        self.assertTrue(MaystroConfig.load().live_error)

    def test_full_sync_also_reads_payments_and_balance(self):
        fake = FakeMaystro([[remote_order(1, 41)]], payments=[[self.remote_payment(1, 4000, 2, 2)]], balance={"solde en cours": 7000.0, "solde a payer": 4000.0})
        result = self.sync(fake)
        self.assertEqual(result.data["payments"], 1)
        config = MaystroConfig.load()
        self.assertEqual((config.balance_current, config.balance_payable), (7000, 4000))

    def test_payments_by_month(self):
        payments = [[self.remote_payment(1, 60500, 5, 0), self.remote_payment(2, 169600, 17, 1), self.remote_payment(3, 505700, 50, 45)]]
        self.sync(FakeMaystro([[]], payments=payments))
        months = self.client.get("/api/shipping/maystro/payments/months/").data
        self.assertEqual(sum(row["amount"] for row in months), 735800)
        self.assertEqual(sum(row["orders"] for row in months), 72)
        self.assertEqual([row["month"] for row in months], sorted((row["month"] for row in months), reverse=True))
        oldest = months[-1]
        listed = self.client.get(self.PAYMENTS_URL, {"month": oldest["month"]}).data
        self.assertEqual(sum(row["amount"] for row in listed["results"]), oldest["amount"])

    def test_delivered_months_use_the_delivery_date(self):
        rows = [remote_order(1, 41, "5000.00", days_ago=40), remote_order(2, 41, "3000.00", days_ago=1), remote_order(3, 50, "4000.00", days_ago=1)]
        self.sync(FakeMaystro([rows]))
        MaystroOrder.objects.filter(display_id="90000001").update(delivered_at=timezone.now())
        months = self.client.get("/api/shipping/maystro/delivered/months/").data
        self.assertEqual(sum(row["orders"] for row in months), 2)
        current = timezone.localtime().strftime("%Y-%m")
        listed = self.client.get("/api/shipping/maystro/orders/", {"delivered_month": current}).data
        self.assertIn("90000001", {row["display_id"] for row in listed["results"]})


class MaystroWebhookTests(MirrorTestCase):
    def url(self):
        return f"/api/shipping/webhook/{MaystroConfig.load().webhook_token}/"

    def pubsub(self, event):
        data = base64.b64encode(json.dumps(event).encode("utf-8")).decode("ascii")
        return {"message": {"data": data, "messageId": "1"}, "subscription": "projects/x/subscriptions/y"}


    def test_store_payment_marks_orders_paid(self):
        rows = [remote_order(1, 41, "3900.00"), remote_order(2, 41, "5500.00"), remote_order(3, 41, "1000.00", refund=True)]
        self.sync(FakeMaystro([rows]))
        event = {"event": "StorePayment", "instance_table": "store", "instance_uuid": "s", "hook_trigger": {"name": "StorePayment"}, "payload": {"orders": [rows[0]["id"], rows[2]["id"]], "date": timezone.now().isoformat()}}
        first = self.client.post(self.url(), self.pubsub(event))
        self.assertEqual((first.data["result"], first.data["orders"]), ("payment_recorded", 2))
        payment = MaystroPayment.objects.get()
        self.assertEqual((payment.amount, payment.source, payment.orders_count), (3900, "webhook", 2))
        again = self.client.post(self.url(), self.pubsub(event))
        self.assertEqual((again.data["result"], again.data["orders"]), ("recorded", 2))
        self.assertEqual(MaystroPayment.objects.count(), 1)
        paid = self.client.get("/api/shipping/maystro/orders/", {"search": rows[0]["display_id"]}).data["results"][0]
        self.assertTrue(paid["paid"])

    def test_store_payment_accepts_plain_json(self):
        rows = [remote_order(1, 41, "3900.00")]
        self.sync(FakeMaystro([rows]))
        event = {"event": "StorePayment", "payload": {"orders": [rows[0]["id"]], "date": timezone.now().isoformat()}}
        self.assertEqual(self.client.post(self.url(), event, format="json").data["result"], "payment_recorded")

    def test_webhook_needs_the_secret_token(self):
        event = {"event": "StorePayment", "payload": {"orders": [], "date": timezone.now().isoformat()}}
        self.anonymous()
        self.assertCode(self.client.post("/api/shipping/webhook/wrong-token/", self.pubsub(event)), 403, "invalid_webhook_token")
        self.assertEqual(MaystroPayment.objects.count(), 0)

    def test_order_event_upserts_mirror_with_return_fees(self):
        MaystroConfig.objects.filter(pk=MaystroConfig.load().pk).update(return_fee=300)
        remote = remote_order(1, 52)
        payload = {**remote, "display_id_order": remote["display_id"], "external_order_id": None, "delivery_price": 400, "return_fees": 200, "products": remote["products"], "last_update": remote["updated_at"]}
        event = {"event": "OrderStatusChanged", "instance_table": "order", "instance_uuid": remote["id"], "hook_trigger": {"name": "all"}, "payload": payload}
        self.assertCode(self.client.post(self.url(), self.pubsub(event)), 200)
        row = MaystroOrder.objects.get(maystro_id=remote["id"])
        self.assertEqual((row.status, row.return_fees), (52, 200))
        self.assertIsNotNone(row.shipped_at)
        stored = json.dumps(list(MaystroOrder.objects.values()), default=str, ensure_ascii=False)
        self.assertNotIn(FAKE_PHONE, stored)
        self.assertNotIn(FAKE_NAME, stored)

    def test_unrelated_event_is_ignored(self):
        event = {"event": "DeliveryInfoUpdated", "payload": {"commune": 1}}
        self.assertEqual(self.client.post(self.url(), self.pubsub(event)).data["result"], "ignored")
        self.assertEqual(MaystroOrder.objects.count(), 0)


class MaystroAccessTests(MirrorTestCase):
    SYNC = ("post", "/api/shipping/maystro/sync/")
    MONEY = [
        ("get", "/api/shipping/maystro/summary/"),
        ("get", "/api/shipping/maystro/orders/"),
        ("get", "/api/shipping/maystro/balance/"),
        ("get", "/api/shipping/maystro/payments/"),
        ("get", "/api/shipping/maystro/payments/months/"),
        ("get", "/api/shipping/maystro/delivered/months/"),
    ]

    def grant(self, role, **sections):
        for section, allowed in sections.items():
            RolePermission.objects.update_or_create(role=role, section=section, defaults={"allowed": allowed})

    def manual_payment(self):
        return MaystroPayment.objects.create(fingerprint="manual-x", source="manual", paid_on=timezone.now(), amount=100)

    def test_anonymous_is_refused(self):
        payment = self.manual_payment()
        self.anonymous()
        for method, url in [self.SYNC, *self.MONEY]:
            self.assertCode(getattr(self.client, method)(url), 401)
        self.assertEqual(MaystroPayment.objects.count(), 1)

    def test_accountant_with_dues_can_read(self):
        self.as_user(self.accountant)
        self.assertCode(self.client.get("/api/shipping/maystro/summary/"), 200)

    def test_accountant_works_the_whole_money_page(self):
        self.grant(Role.ACCOUNTANT, dues=True, shipping=False)
        self.as_user(self.accountant)
        self.assertCode(self.sync(FakeMaystro([[remote_order(1, 41, "5000.00")]])), 200)
        for url in ("summary/", "orders/", "balance/", "payments/", "payments/months/", "delivered/months/"):
            self.assertCode(self.client.get(f"/api/shipping/maystro/{url}"), 200)

    def test_confirmation_agent_never_sees_money(self):
        payment = self.manual_payment()
        self.sync(FakeMaystro([[remote_order(1, 41, "5000.00")]]))
        self.as_user(self.agent)
        for method, url in self.MONEY:
            response = getattr(self.client, method)(url)
            self.assertCode(response, 403)
            self.assertNotIn("5000", response.content.decode("utf-8"))
        self.assertEqual(MaystroPayment.objects.count(), 1)

    def test_shipping_without_dues_may_sync_but_not_read_money(self):
        self.grant(Role.CONFIRMATION, shipping=True, dues=False)
        self.as_user(self.agent)
        result = self.sync(FakeMaystro([[remote_order(1, 41, "5000.00")]]))
        self.assertCode(result, 200)
        self.assertEqual(set(result.data), {"full", "pages", "fetched", "created", "updated", "linked", "products", "points", "payments", "synced_at"})
        self.assertNotIn("5000", result.content.decode("utf-8"))
        for method, url in self.MONEY:
            self.assertCode(getattr(self.client, method)(url), 403)

    def test_role_without_shipping_or_dues_is_refused(self):
        self.grant(Role.CONFIRMATION, shipping=False, dues=False)
        self.as_user(self.agent)
        for method, url in [self.SYNC, *self.MONEY]:
            self.assertCode(getattr(self.client, method)(url), 403)
