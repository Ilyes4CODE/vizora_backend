from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import load_workbook

from apps.core.models import Wilaya
from apps.orders.models import ImportBatch, Order

from .base import ApiTestCase, excel_file


class OrderTests(ApiTestCase):
    def test_create_home_delivery(self):
        order = self.new_order()
        self.assertEqual(order["reference"], f"VZ-{10000 + order['id']}")
        self.assertEqual(order["phone"], "0555004112")
        self.assertEqual((order["subtotal"], order["delivery_fee"], order["total"]), (3900, 600, 4500))
        self.assertEqual(order["status"], "pending")
        self.assertEqual(order["status_label"], "بانتظار التأكيد")
        self.assertEqual(order["products"], "المنتج الأول × 1")
        self.assertEqual(len(order["events"]), 1)

    def test_create_desk_delivery_and_multiple_items(self):
        order = self.new_order(
            delivery_type="desk",
            items=[{"product": self.product.pk, "quantity": 2}, {"product": self.product_b.pk, "quantity": 1}],
        )
        self.assertEqual((order["subtotal"], order["delivery_fee"], order["total"]), (13300, 400, 13700))

    def test_same_product_lines_are_merged(self):
        order = self.new_order(items=[{"product": self.product.pk, "quantity": 1}, {"product": self.product.pk, "quantity": 2}])
        self.assertEqual(len(order["items"]), 1)
        self.assertEqual(order["items"][0]["quantity"], 3)

    def test_phone_formats(self):
        self.assertEqual(self.new_order(phone="+213 661 00 28 75")["phone"], "0661002875")
        self.assertEqual(self.new_order(phone="770001690")["phone"], "0770001690")

    def test_manual_duplicate_is_flagged_and_can_be_overridden(self):
        first = self.new_order()
        response = self.client.post("/api/orders/", self.order_payload())
        self.assertCode(response, 409, "duplicate_order")
        self.assertIn(first["reference"], response.data["detail"])
        self.assertEqual(Order.objects.count(), 1)
        self.assertCode(self.client.post("/api/orders/", self.order_payload(allow_duplicate=True)), 201)
        self.assertEqual(Order.objects.count(), 2)

    def test_same_customer_different_product_is_not_a_duplicate(self):
        self.new_order()
        self.new_order(items=[{"product": self.product_b.pk, "quantity": 1}])

    def test_cancelled_order_is_not_a_duplicate(self):
        order = self.new_order()
        self.client.post(f"/api/orders/{order['id']}/cancel/", {})
        self.new_order()

    def test_invalid_input(self):
        for overrides in (
            {"phone": "0555 00 41"},
            {"phone": "+33 6 00 00 00 00"},
            {"items": []},
            {"wilaya": 99},
            {"items": [{"product": 99999, "quantity": 1}]},
            {"items": [{"product": self.product.pk, "quantity": 0}]},
            {"delivery_type": "drone"},
        ):
            with self.subTest(overrides=overrides):
                self.assertCode(self.client.post("/api/orders/", self.order_payload(**overrides)), 400, "validation_error")

    def test_unavailable_delivery_type(self):
        Wilaya.objects.filter(pk=16).update(desk_available=False)
        self.assertCode(self.client.post("/api/orders/", self.order_payload(delivery_type="desk")), 400)

    def test_list_filters_and_search(self):
        self.new_order(customer_name="هاجر", phone="0661002875", wilaya=31)
        self.new_order()
        self.assertEqual(self.client.get("/api/orders/").data["count"], 2)
        self.assertEqual(self.client.get("/api/orders/", {"wilaya": 31}).data["count"], 1)
        self.assertEqual(self.client.get("/api/orders/", {"search": "هاجر"}).data["count"], 1)
        self.assertEqual(self.client.get("/api/orders/", {"search": "0661"}).data["count"], 1)
        self.assertEqual(self.client.get("/api/orders/", {"status": "confirmed"}).data["count"], 0)
        self.assertEqual(self.client.get("/api/orders/", {"product": self.product.pk}).data["count"], 2)
        self.assertEqual(self.client.get("/api/orders/", {"month": "2020-01"}).data["count"], 0)

    def test_pagination(self):
        for index in range(3):
            self.new_order(phone=f"055500000{index}")
        response = self.client.get("/api/orders/", {"page_size": 2})
        self.assertEqual(len(response.data["results"]), 2)
        self.assertIsNotNone(response.data["next"])

    def test_counts(self):
        self.new_order()
        self.confirmed_order(phone="0661002875")
        response = self.client.get("/api/orders/counts/", {"status": "pending"})
        self.assertCode(response, 200)
        self.assertEqual((response.data["all"], response.data["pending"], response.data["confirmed"]), (2, 1, 1))

    def test_retrieve_never_exposes_cost(self):
        order = self.new_order()
        response = self.client.get(f"/api/orders/{order['id']}/")
        self.assertCode(response, 200)
        self.assertNotIn("unit_cost", response.data["items"][0])

    def test_update_recalculates_totals(self):
        order = self.new_order()
        response = self.client.patch(
            f"/api/orders/{order['id']}/",
            {"delivery_type": "desk", "items": [{"product": self.product_b.pk, "quantity": 2}], "note": "اتصل مساءً"},
        )
        self.assertCode(response, 200)
        self.assertEqual((response.data["subtotal"], response.data["delivery_fee"], response.data["total"]), (11000, 400, 11400))
        self.assertEqual(response.data["note"], "اتصل مساءً")

    def test_update_of_confirmed_order_checks_stock(self):
        order = self.confirmed_order()
        response = self.client.patch(f"/api/orders/{order['id']}/", {"items": [{"product": self.product.pk, "quantity": 500}]})
        self.assertCode(response, 409, "insufficient_stock")
        response = self.client.patch(f"/api/orders/{order['id']}/", {"items": [{"product": self.product.pk, "quantity": 100}]})
        self.assertCode(response, 200)

    def test_shipped_order_is_locked(self):
        order_id, _ = self.shipped_order()
        self.assertCode(self.client.patch(f"/api/orders/{order_id}/", {"note": "x"}), 409, "order_locked")
        self.assertCode(self.client.post(f"/api/orders/{order_id}/cancel/", {}), 409, "order_locked")

    def test_cancel(self):
        order = self.new_order()
        response = self.client.post(f"/api/orders/{order['id']}/cancel/", {"reason": "customer"})
        self.assertCode(response, 200)
        self.assertEqual((response.data["status"], response.data["cancel_reason"]), ("cancelled", "customer"))

    def test_bulk_assign(self):
        first = self.new_order()
        second = self.new_order(phone="0661002875")
        response = self.client.post("/api/orders/bulk-assign/", {"order_ids": [first["id"], second["id"]], "user": self.agent.pk})
        self.assertCode(response, 200)
        self.assertEqual(response.data["updated"], 2)
        self.assertEqual(self.client.get("/api/orders/", {"assigned_to": self.agent.pk}).data["count"], 2)

    def test_delete_is_not_allowed(self):
        order = self.new_order()
        self.assertCode(self.client.delete(f"/api/orders/{order['id']}/"), 405)

    def test_export(self):
        self.new_order()
        response = self.client.get("/api/orders/export/")
        self.assertCode(response, 200)
        rows = list(load_workbook(BytesIO(response.content)).active.iter_rows(values_only=True))
        self.assertEqual(rows[0][0], "الطلبية")
        self.assertEqual(rows[1][2], "0555004112")


class OrderImportTests(ApiTestCase):
    HEADERS = ["Nom complet", "Telephone", "Wilaya", "Commune", "Produit", "Quantité"]

    def upload(self, rows):
        response = self.client.post("/api/orders/import/", {"file": excel_file(rows)}, format="multipart")
        self.assertCode(response, 201)
        return response.data

    def sample_rows(self):
        return [
            self.HEADERS,
            ["كريم مزيان", "0555 00 41 12", "الجزائر", "الدار البيضاء", "المنتج الأول", 1],
            ["هاجر", 661002875, "Oran", "السانية", "P-2", 2],
            ["رضا", "0770001690", "19 - سطيف", "", "المنتج الأول", None],
            ["مكرر", "0555004112", "16", "", "المنتج الأول", 1],
            ["ناقص", "0555 00 41", "الجزائر", "", "المنتج الأول", 1],
            ["أجنبي", "+33 6 00 00 00", "الجزائر", "", "المنتج الأول", 1],
            ["ولاية", "0550000001", "باريس", "", "المنتج الأول", 1],
            ["منتج", "0550000002", "الجزائر", "", "غير موجود", 1],
            ["كمية", "0550000003", "الجزائر", "", "المنتج الأول", "abc"],
            [None, None, None, None, None, None],
        ]

    def test_upload_suggests_mapping_and_checks_rows(self):
        batch = self.upload(self.sample_rows())
        self.assertEqual(batch["mapping"]["phone"], "Telephone")
        self.assertEqual(batch["mapping"]["quantity"], "Quantité")
        self.assertEqual(batch["result"]["valid"], 3)
        self.assertEqual(batch["result"]["duplicates"], 1)
        self.assertEqual(batch["result"]["invalid"], 5)
        codes = {issue["line"]: issue["code"] for issue in batch["result"]["issues"]}
        self.assertEqual(codes[5], "duplicate")
        self.assertEqual(codes[6], "phone_short")
        self.assertEqual(codes[7], "phone_foreign")
        self.assertEqual(codes[8], "unknown_wilaya")
        self.assertEqual(codes[9], "unknown_product")
        self.assertEqual(codes[10], "bad_quantity")

    def test_commit_creates_only_valid_orders_once(self):
        batch = self.upload(self.sample_rows())
        response = self.client.post(f"/api/orders/import/{batch['id']}/commit/")
        self.assertCode(response, 200)
        self.assertEqual(response.data["created_count"], 3)
        self.assertEqual(Order.objects.filter(source="excel").count(), 3)
        order = Order.objects.get(phone="0661002875")
        self.assertEqual((order.wilaya_id, order.subtotal), (31, 11000))
        self.assertCode(self.client.post(f"/api/orders/import/{batch['id']}/commit/"), 409, "import_already_committed")
        self.assertEqual(Order.objects.filter(source="excel").count(), 3)

    def test_existing_order_is_detected_as_duplicate(self):
        self.new_order()
        batch = self.upload([self.HEADERS, ["كريم", "0555004112", "الجزائر", "", "المنتج الأول", 1]])
        self.assertEqual((batch["result"]["valid"], batch["result"]["duplicates"]), (0, 1))

    def test_manual_column_mapping(self):
        batch = self.upload([["A", "B", "C", "D"], ["كريم", "0555004112", "الجزائر", "المنتج الأول"]])
        self.assertEqual(batch["mapping"], {})
        url = f"/api/orders/import/{batch['id']}/validate/"
        self.assertCode(self.client.post(url, {"mapping": {"phone": "B"}}), 400)
        self.assertCode(self.client.post(url, {"mapping": {"phone": "Z", "customer_name": "A", "wilaya": "C", "product": "D"}}), 400)
        self.assertCode(self.client.post(url, {"mapping": {"bogus": "A"}}), 400)
        response = self.client.post(url, {"mapping": {"customer_name": "A", "phone": "B", "wilaya": "C", "product": "D"}})
        self.assertCode(response, 200)
        self.assertEqual(response.data["result"]["valid"], 1)

    def test_commit_requires_complete_mapping(self):
        batch = self.upload([["A"], ["x"]])
        self.assertCode(self.client.post(f"/api/orders/import/{batch['id']}/commit/"), 400)

    def test_retrieve(self):
        batch = self.upload(self.sample_rows())
        response = self.client.get(f"/api/orders/import/{batch['id']}/")
        self.assertCode(response, 200)
        self.assertEqual(response.data["row_count"], 10)
        self.assertEqual(ImportBatch.objects.count(), 1)

    def test_template_download(self):
        response = self.client.get("/api/orders/import/template/")
        self.assertCode(response, 200)
        headers = next(load_workbook(BytesIO(response.content)).active.iter_rows(values_only=True))
        self.assertEqual(headers[:2], ("الاسم", "الهاتف"))

    def test_bad_files(self):
        url = "/api/orders/import/"
        self.assertCode(self.client.post(url, {}, format="multipart"), 400, "file_required")
        self.assertCode(self.client.post(url, {"file": SimpleUploadedFile("a.csv", b"x")}, format="multipart"), 400, "unsupported_file")
        self.assertCode(self.client.post(url, {"file": SimpleUploadedFile("a.xlsx", b"not excel")}, format="multipart"), 400, "unreadable_file")
        self.assertCode(self.client.post(url, {"file": excel_file([[None]])}, format="multipart"), 400, "empty_file")
