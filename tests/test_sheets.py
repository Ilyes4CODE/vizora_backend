from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from apps.core.models import Commune
from apps.orders.models import Order, SheetRow, SheetSource
from apps.orders.sheets import export_url

from .base import ApiTestCase

PUBLISHED = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTHB9AZb/pubhtml?gid=0&single=true"
SHARED = "https://docs.google.com/spreadsheets/d/1pkiBdNLatVAGrPHEw3sSkv2bx/edit#gid=123"
STORE_HEADER = ["Date Time", "Order N°", "Full Name", "Province", "Province Code", "City", "Address", "Phone", "Product Title", "Quantity", "Total Price", "Note"]


class FakeResponse:
    def __init__(self, text, status=200, kind="text/tab-separated-values"):
        self.content = text.encode("utf-8")
        self.status_code = status
        self.headers = {"Content-Type": kind}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, size):
        yield self.content


def tsv(rows):
    return "\n".join("\t".join(str(cell) for cell in row) for row in rows)


def store_row(number, name, phone, province, code, city, product="P-1", quantity=1, total="3900"):
    return ["22/11/2026 19:25", f"#{number}", name, province, code, city, "حي 5", phone, product, quantity, total, ""]


class SheetTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.sheet = [STORE_HEADER]
        fetching = patch("apps.orders.sheets.requests.get", side_effect=lambda *args, **kwargs: self.reply())
        self.fetch = fetching.start()
        self.addCleanup(fetching.stop)
        self.response = None
        Commune.objects.create(wilaya_id=16, name_ar="الرحمانية", name_fr="Rahmania")

    def reply(self):
        return self.response or FakeResponse(tsv(self.sheet))

    def link(self, **extra):
        payload = {"name": "طلبيات المتجر", "url": PUBLISHED, "start_row": 2, **extra}
        return self.client.post("/api/orders/sheets/", payload)

    def orders_from_sheet(self):
        return Order.objects.filter(source="sheet").order_by("id")

    def test_links_are_turned_into_data_links(self):
        self.assertEqual(export_url(PUBLISHED), "https://docs.google.com/spreadsheets/d/e/2PACX-1vTHB9AZb/pub?output=tsv&gid=0&single=true")
        self.assertEqual(export_url(SHARED), "https://docs.google.com/spreadsheets/d/1pkiBdNLatVAGrPHEw3sSkv2bx/export?format=tsv&gid=123")
        self.assertEqual(
            export_url("https://docs.google.com/spreadsheets/d/e/2PACX-1vTHB9AZb/pub?gid=0&single=true&output=tsv"),
            "https://docs.google.com/spreadsheets/d/e/2PACX-1vTHB9AZb/pub?output=tsv&gid=0&single=true",
        )
        for bad in ("http://docs.google.com/spreadsheets/d/x/edit", "https://evil.example/spreadsheets/d/x", "https://docs.google.com/document/d/x/edit", ""):
            with self.subTest(url=bad):
                response = self.link(url=bad)
                self.assertEqual(response.status_code, 400)
                self.assertIn(response.data["code"], ("sheet_url_invalid", "validation_error"))
        self.assertFalse(SheetSource.objects.exists())

    def test_store_sheet_with_newest_orders_on_top_starts_from_the_chosen_row(self):
        self.sheet += [
            store_row(12615, "حياة لحلاوي", "+213559665984", "Alger", "16", "RAHMANIA", quantity=3, total="11900.01"),
            store_row(12614, "Hamiche Mouloud", "+213665820399", "Tizi Ouzou", "15", "YAKOURENE"),
            store_row(12613, "تكليت صهيب", "+213781439133", "Alger", "16", "EL ACHOUR"),
            store_row(12612, "محمد أمين", "+213775800012", "Relizane", "48", "EL OULDJA"),
        ]
        response = self.link(start_row=4)
        self.assertCode(response, 201)
        self.assertEqual(response.data["last_result"]["created"], 2)
        self.assertEqual(response.data["last_result"]["skipped"], 2)
        self.assertEqual(response.data["imported_count"], 2)
        first, second = self.orders_from_sheet()
        self.assertEqual((first.customer_name, first.phone, first.wilaya_id, first.commune), ("تكليت صهيب", "0781439133", 16, "EL ACHOUR"))
        self.assertEqual((second.wilaya_id, second.subtotal, second.status, second.source), (48, 3900, "pending", "sheet"))
        self.assertIn("رقم الطلب في الشيت: #12613", first.note)
        self.assertIn("Google Sheet «طلبيات المتجر» · السطر 4", first.events.first().message)
        self.assertIsNotNone(first.assigned_to_id)

        self.sheet.insert(1, store_row(12616, "زبون جديد", "0661002875", "Alger", "16", "Rahmania", quantity=3, total="11 900,00"))
        source_id = response.data["id"]
        result = self.client.post(f"/api/orders/sheets/{source_id}/sync/").data["last_result"]
        self.assertEqual((result["created"], result["skipped"], result["duplicates"]), (1, 0, 0))
        newest = self.orders_from_sheet().last()
        self.assertEqual((newest.customer_name, newest.commune, newest.subtotal, newest.total), ("زبون جديد", "الرحمانية", 11900, 12500))
        self.assertEqual(sorted((item.quantity, item.unit_price) for item in newest.items.all()), [(1, 3966), (2, 3967)])
        again = self.client.post(f"/api/orders/sheets/{source_id}/sync/").data
        self.assertEqual((again["last_result"]["created"], again["imported_count"]), (0, 3))
        self.assertEqual(self.orders_from_sheet().count(), 3)

    def test_video_sheet_layout_with_rows_added_at_the_bottom(self):
        self.sheet = [
            ["product", "variant", "full name", "phone", "wilaya", "commune", "quantity", "total", "sku"],
            ["oil", "", "محمد", "213672971217", "المسيلة", "زرزور", "1", "14990", "P-1"],
            ["boots", "noire/41", "علي", "213781850671", "الجزائر", "دالي ابراهيم", "1", "6490", "P-2"],
        ]
        response = self.link()
        self.assertCode(response, 201)
        self.assertEqual(response.data["last_result"]["created"], 2)
        boots = self.orders_from_sheet().last()
        self.assertEqual((boots.phone, boots.wilaya_id, boots.subtotal), ("0781850671", 16, 6490))
        self.assertEqual((boots.items.get().product_id, boots.items.get().variant), (self.product_b.pk, "noire/41"))
        self.sheet.append(["oil", "", "hicham2", "213658895708", "40", "Khanchla", "2", "29980", "P-1"])
        result = self.client.post(f"/api/orders/sheets/{response.data['id']}/sync/").data["last_result"]
        self.assertEqual(result["created"], 1)
        last = self.orders_from_sheet().last()
        self.assertEqual((last.customer_name, last.wilaya_id, last.items.get().quantity, last.subtotal), ("hicham2", 40, 2, 29980))

    def test_rows_with_problems_wait_until_they_are_fixed(self):
        self.sheet += [
            store_row(1, "بلا ولاية", "0555004112", "Atlantis", "", "x"),
            store_row(2, "رقم ناقص", "0555", "Alger", "16", "x"),
            store_row(3, "منتج مجهول", "0661002875", "Oran", "31", "x", product="شيء آخر"),
        ]
        response = self.link()
        result = response.data["last_result"]
        self.assertEqual((result["created"], result["waiting_count"]), (0, 3))
        self.assertEqual([item["row"] for item in result["waiting"]], [2, 3, 4])
        self.assertIn("الولاية غير معروفة", result["waiting"][0]["problems"])
        self.assertIn("رقم الهاتف ناقص", result["waiting"][1]["problems"])
        self.sheet[1] = store_row(1, "بلا ولاية", "0555004112", "Alger", "16", "x")
        result = self.client.patch(f"/api/orders/sheets/{response.data['id']}/", {"default_product": self.product.pk}).data["last_result"]
        self.assertEqual((result["created"], result["waiting_count"]), (2, 1))
        self.assertEqual(self.orders_from_sheet().last().items.get().product_id, self.product.pk)

    def test_an_order_already_in_the_system_is_not_created_twice(self):
        existing = self.new_order(phone="0555004112")
        self.sheet += [store_row(9, "كريم مزيان", "0555004112", "Alger", "16", "x")]
        result = self.link().data["last_result"]
        self.assertEqual((result["created"], result["duplicates"]), (0, 1))
        self.assertEqual(result["duplicate_rows"][0]["reference"], existing["reference"])
        self.assertEqual(SheetRow.objects.get().status, "duplicate")

    def test_total_can_include_the_delivery_fee(self):
        self.sheet += [store_row(1, "كريم", "0555004112", "Alger", "16", "x", total="4500")]
        self.link(total_includes_delivery=True)
        order = self.orders_from_sheet().get()
        self.assertEqual((order.subtotal, order.delivery_fee, order.total), (3900, 600, 4500))

    def test_a_sheet_that_is_not_shared_or_badly_named_is_refused_clearly(self):
        self.response = FakeResponse("<!DOCTYPE html><html>Sign in</html>", kind="text/html")
        response = self.link()
        self.assertCode(response, 400, "sheet_not_shared")
        self.response = FakeResponse("", status=404)
        self.assertCode(self.link(), 400, "sheet_not_shared")
        self.response = FakeResponse(tsv([["a", "b"], ["1", "2"]]))
        response = self.link()
        self.assertCode(response, 400, "sheet_columns_missing")
        self.assertIn("الاسم", response.data["detail"])
        self.assertFalse(SheetSource.objects.exists())

    def test_preview_shows_columns_and_rows_to_pick_the_start(self):
        self.sheet += [store_row(1, "كريم", "0555004112", "Alger", "16", "x"), store_row(2, "", "", "", "", "")]
        self.sheet.append(store_row(3, "هاجر", "0661002875", "Nowhere", "", "x"))
        response = self.client.post("/api/orders/sheets/preview/", {"url": SHARED})
        self.assertCode(response, 200)
        data = response.data
        self.assertEqual((data["header_row"], data["total_rows"], data["first_row"], data["last_row"]), (1, 3, 2, 4))
        fields = {column["field"] for column in data["columns"]}
        self.assertTrue({"customer_name", "phone", "wilaya", "wilaya_code", "product", "quantity", "total", "order_number", "note", "date"} <= fields)
        self.assertEqual(data["rows"][0]["wilaya"], "الجزائر")
        self.assertEqual(data["rows"][0]["problems"], [])
        self.assertIn("الولاية غير معروفة", data["rows"][2]["problems"])
        self.assertIn("/export?format=tsv&gid=123", self.fetch.call_args.args[0])

    def test_changing_the_start_row_and_pausing(self):
        self.sheet += [store_row(n, f"زبون {n}", f"05550041{n:02d}", "Alger", "16", "x") for n in range(1, 6)]
        source = self.link(start_row=5).data
        self.assertEqual(source["last_result"]["created"], 2)
        result = self.client.patch(f"/api/orders/sheets/{source['id']}/", {"start_row": 3}).data["last_result"]
        self.assertEqual(result["created"], 0)
        self.assertEqual(self.orders_from_sheet().count(), 2)
        paused = self.client.patch(f"/api/orders/sheets/{source['id']}/", {"is_active": False}).data
        self.assertFalse(paused["is_active"])
        self.sheet.append(store_row(9, "زبون 9", "0555004199", "Alger", "16", "x"))
        SheetSource.objects.update(last_synced_at=timezone.now() - timedelta(minutes=10))
        with override_settings(SHEETS_SYNC_INLINE=True):
            self.client.get("/api/orders/")
        self.assertEqual(self.orders_from_sheet().count(), 2)
        self.assertEqual(self.client.get(f"/api/orders/sheets/{source['id']}/").data["name"], "طلبيات المتجر")

    @override_settings(SHEETS_SYNC_INLINE=True)
    def test_new_rows_arrive_by_themselves(self):
        self.sheet += [store_row(1, "كريم", "0555004112", "Alger", "16", "x")]
        source_id = self.link().data["id"]
        self.sheet.insert(1, store_row(2, "هاجر", "0661002875", "Oran", "31", "x"))
        self.client.get("/api/orders/")
        self.assertEqual(self.orders_from_sheet().count(), 1)
        SheetSource.objects.update(last_synced_at=timezone.now() - timedelta(minutes=3))
        self.client.get("/api/confirmation/counts/")
        self.assertEqual(self.orders_from_sheet().count(), 2)
        self.sheet.insert(1, store_row(3, "سارة", "0770001690", "Oran", "31", "x"))
        SheetSource.objects.update(last_synced_at=timezone.now() - timedelta(minutes=3))
        output = StringIO()
        call_command("run_maintenance", stdout=output)
        self.assertIn("sheet طلبيات المتجر: +1", output.getvalue())
        self.assertEqual(self.orders_from_sheet().count(), 3)
        self.assertEqual(self.client.get("/api/orders/sheets/").data[0]["imported_count"], 3)
        self.assertCode(self.client.delete(f"/api/orders/sheets/{source_id}/"), 204)
        self.assertEqual(self.orders_from_sheet().count(), 3)
        self.assertFalse(SheetRow.objects.exists())

    def test_sheet_errors_are_kept_on_the_source(self):
        self.sheet += [store_row(1, "كريم", "0555004112", "Alger", "16", "x")]
        source_id = self.link().data["id"]
        self.response = FakeResponse("", status=500)
        self.assertCode(self.client.post(f"/api/orders/sheets/{source_id}/sync/"), 502, "sheet_unreachable")
        source = self.client.get(f"/api/orders/sheets/{source_id}/").data
        self.assertFalse(source["last_sync_ok"])
        self.assertIn("Google Sheets", source["last_error"])

    def test_agents_without_orders_access_cannot_link_sheets(self):
        from apps.accounts.models import RolePermission

        RolePermission.objects.filter(role=self.agent.role, section="orders").update(allowed=False)
        self.as_user(self.agent)
        self.assertCode(self.client.get("/api/orders/sheets/"), 403)
