from apps.catalog.models import Product, StockMovement
from apps.orders.models import Order, OrderItem
from apps.shipping.maystro import LiveMaystroClient
from apps.shipping.models import MaystroConfig
from apps.storefront.models import StoreProduct

from .test_storefront import StoreTestCase

SIZES = {"kind": "size", "name": {"ar": "المقاس", "en": "Size"}, "values": [{"label": "M"}, {"label": "L"}, {"label": "XL", "available": False}]}
COLORS = {"kind": "color", "name": {"ar": "اللون", "en": "Colour"}, "values": [{"label": {"ar": "أسود", "en": "Black"}, "color": "#111111"}, {"label": {"ar": "أبيض", "en": "White"}, "color": "#ffffff"}]}


class ProductCreationTests(StoreTestCase):
    def test_new_products_are_active_even_from_a_file_upload_form(self):
        first = self.client.post("/api/products/", {"name": "قميص قطني", "sku": "", "price": 2500, "cost": 0, "low_stock_threshold": 5, "courier_ref": ""}, format="multipart")
        self.assertCode(first, 201)
        self.assertTrue(first.data["is_active"])
        self.assertIsNone(first.data["sku"])
        second = self.client.post("/api/products/", {"name": "سروال جينز", "sku": "", "price": 3200, "courier_ref": ""}, format="multipart")
        self.assertCode(second, 201)
        self.assertIn("سروال جينز", [row["name"] for row in self.client.get("/api/products/", {"is_active": "true"}).data])
        edited = self.client.patch(f"/api/products/{first.data['id']}/", {"price": 2700}, format="multipart")
        self.assertCode(edited, 200)
        self.assertTrue(edited.data["is_active"])

    def test_duplicates_get_clear_messages_instead_of_a_crash(self):
        self.client.post("/api/products/", {"name": "قميص قطني", "sku": "TS-01", "price": 2500}, format="multipart")
        same_name = self.client.post("/api/products/", {"name": "  قميص   قطني ", "price": 2500}, format="multipart")
        self.assertCode(same_name, 400)
        self.assertIn("نفس الاسم", str(same_name.data["errors"]["name"]))
        same_sku = self.client.post("/api/products/", {"name": "قميص آخر", "sku": "ts-01", "price": 2500}, format="multipart")
        self.assertCode(same_sku, 400)
        self.assertIn("sku", same_sku.data["errors"])
        self.assertCode(self.client.post("/api/products/", {"name": "قميص بلا سعر"}, format="multipart"), 400)
        self.assertCode(self.client.post("/api/products/", {"name": "قميص بسعر سالب", "price": -5}, format="multipart"), 400)

    def test_a_new_product_can_be_created_straight_from_the_website_tab(self):
        created = self.client.post("/api/store/products/", {"new_product_name": "تيشيرت صيفي", "new_product_price": 1900, "new_product_maystro": False, "title_en": "Summer tee"}, format="json")
        self.assertCode(created, 201)
        product = Product.objects.get(name="تيشيرت صيفي")
        self.assertTrue(product.is_active)
        self.assertEqual((product.price, product.on_hand, product.stock_source), (1900, 0, "maystro"))
        self.assertFalse(StockMovement.objects.filter(product=product).exists())
        self.assertEqual((created.data["title_ar"], created.data["slug"]), ("تيشيرت صيفي", "summer-tee"))
        missing = self.client.post("/api/store/products/", {"new_product_name": "منتج بدون Maystro", "new_product_price": 1900}, format="json")
        self.assertCode(missing, 400)
        self.assertEqual(missing.data["code"], "maystro_not_configured")
        self.assertCode(self.client.post("/api/store/products/", {"new_product_name": "تيشيرت صيفي", "new_product_price": 1900}, format="json"), 400)
        self.assertCode(self.client.post("/api/store/products/", {"new_product_name": "بلا سعر"}, format="json"), 400)
        self.assertCode(self.client.post("/api/store/products/", {}, format="json"), 400)
        self.assertCode(self.client.post("/api/store/products/", {"product": product.pk, "title_ar": "مكرر"}, format="json"), 400)


class ClothingOptionTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        response = self.client.patch(f"/api/store/products/{self.card.pk}/", {"options": [SIZES, COLORS]}, format="json")
        self.assertCode(response, 200)
        self.options = response.data["options"]

    def picked(self, size, colour):
        sizes, colours = self.options
        return {sizes["id"]: next(v["id"] for v in sizes["values"] if v["label"]["ar"] == size), colours["id"]: next(v["id"] for v in colours["values"] if v["label"]["ar"] == colour)}

    def test_options_are_checked_and_shown_in_both_languages(self):
        self.assertEqual([value["label"] for value in self.options[0]["values"]], [{"ar": "M", "en": "M"}, {"ar": "L", "en": "L"}, {"ar": "XL", "en": "XL"}])
        self.assertFalse(self.options[0]["values"][2]["available"])
        self.assertEqual(self.options[1]["values"][1]["color"], "#FFFFFF")
        english = self.client.get("/api/shop/products/visa-card/", {"lang": "en"}).data["options"]
        self.assertEqual((english[1]["name"], english[1]["values"][0]["label"]), ("Colour", "Black"))
        for bad in (
            [SIZES, COLORS, SIZES],
            [{"kind": "size", "values": []}],
            [{"kind": "size", "values": [{"label": "M"}, {"label": "m"}]}],
            [{"kind": "color", "values": [{"label": "أحمر", "color": "red"}]}],
            [{"kind": "size", "values": [{"label": ""}]}],
            "S,M",
        ):
            self.assertCode(self.client.patch(f"/api/store/products/{self.card.pk}/", {"options": bad}, format="json"), 400)
        self.assertCode(self.client.patch(f"/api/store/products/{self.card.pk}/", {"options": []}, format="json"), 200)
        self.assertEqual(self.client.get("/api/shop/products/visa-card/").data["options"], [])

    def test_customers_must_pick_an_available_size_and_colour(self):
        missing = self.place()
        self.assertCode(missing, 422)
        self.assertIn("المقاس", missing.data["errors"]["lines"])
        sold_out = self.place(lines=[{"product": "visa-card", "offer": self.offer_two.pk, "quantity": 1, "options": self.picked("XL", "أسود")}])
        self.assertCode(sold_out, 422)
        self.assertIn("نفد", sold_out.data["errors"]["lines"])
        response = self.place(
            lines=[
                {"product": "visa-card", "offer": self.offer_two.pk, "quantity": 1, "options": self.picked("M", "أسود")},
                {"product": "visa-card", "offer": self.offer_two.pk, "quantity": 2, "options": self.picked("L", "أبيض")},
            ]
        )
        self.assertCode(response, 201)
        order = Order.objects.get(reference=response.data["reference"])
        variants = sorted(order.items.values_list("variant", "quantity"))
        self.assertEqual(variants, [("المقاس: L · اللون: أبيض", 2), ("المقاس: M · اللون: أسود", 1)])
        self.assertEqual(order.subtotal, 3 * 11900)
        self.assertIn("المقاس: M · اللون: أسود", order.note)
        self.assertIn("(المقاس: M · اللون: أسود)", response.data["lines"][0]["title"] + response.data["lines"][1]["title"])
        detail = self.client.get(f"/api/orders/{order.pk}/").data
        self.assertIn("(المقاس: L · اللون: أبيض) × 2", detail["products"])
        self.assertEqual({item["variant"] for item in detail["items"]}, {"المقاس: M · اللون: أسود", "المقاس: L · اللون: أبيض"})

    def test_manual_orders_and_edits_keep_the_size(self):
        created = self.client.post(
            "/api/orders/",
            self.order_payload(items=[{"product": self.product.pk, "quantity": 1, "variant": "المقاس: XL"}, {"product": self.product.pk, "quantity": 1, "variant": "المقاس: S"}]),
            format="json",
        )
        self.assertCode(created, 201)
        order = Order.objects.get(pk=created.data["id"])
        self.assertEqual(sorted(order.items.values_list("variant", flat=True)), ["المقاس: S", "المقاس: XL"])
        item = order.items.get(variant="المقاس: S")
        item.unit_price = 1234
        item.save()
        edited = self.client.patch(f"/api/orders/{order.pk}/", {"items": [{"product": self.product.pk, "quantity": 3, "variant": "المقاس: S"}]}, format="json")
        self.assertCode(edited, 200)
        row = OrderItem.objects.get(order=order)
        self.assertEqual((row.variant, row.quantity, row.unit_price), ("المقاس: S", 3, 1234))

    def test_maystro_gets_the_size_in_the_driver_note(self):
        self.product.courier_ref = "ref-1"
        self.product.save()
        created = self.client.post("/api/orders/", self.order_payload(items=[{"product": self.product.pk, "quantity": 2, "variant": "المقاس: XL · اللون: أسود"}], note="اتصل قبل الوصول"), format="json")
        order = Order.objects.get(pk=created.data["id"])
        note = LiveMaystroClient(MaystroConfig.load()).driver_note(order)
        self.assertEqual(note, f"اتصل قبل الوصول | {self.product.name}: المقاس: XL · اللون: أسود × 2")
        self.assertEqual(StoreProduct.objects.get(pk=self.card.pk).options[0]["kind"], "size")
