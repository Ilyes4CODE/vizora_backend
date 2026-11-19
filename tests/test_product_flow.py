import shutil
import tempfile
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import override_settings

from apps.catalog.models import Product
from apps.shipping.maystro import MaystroError
from apps.shipping.models import MaystroConfig, MaystroProduct
from apps.storefront.models import StoreImage, StoreProduct

from .test_storefront import StoreTestCase, png_file

REMOTE_ID = "7f1c2d3e-0000-4000-8000-00000000a001"


def remote(name, maystro_id=REMOTE_ID, reference=""):
    return {"id": maystro_id, "display_id": 9780201417291, "logistical_description": name, "product_id": reference, "deleted": False, "low_stock_level": 5}


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ProductFlowTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        config = MaystroConfig.load()
        config.set_token("test-token")
        config.store_id = "ebb00000-0000-4000-8000-000000000001"
        config.save()
        self.created = patch("apps.shipping.maystro.LiveMaystroClient.create_product", side_effect=lambda name, **kwargs: remote(name, reference=kwargs.get("reference") or ""))
        self.create_product = self.created.start()
        self.addCleanup(self.created.stop)

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)

    def new_product(self, **overrides):
        body = {
            "name": "تيشيرت صيفي",
            "sku": "",
            "price": 2200,
            "cost": 900,
            "low_stock_threshold": 5,
            "courier_ref": "",
            "stock_mode": "local",
            "initial_stock": 15,
            "create_in_maystro": "true",
            "add_to_store": "true",
            "title_en": "Summer tee",
            "image": png_file(),
        }
        body.update(overrides)
        return self.client.post("/api/products/", body, format="multipart")

    def test_one_form_creates_the_product_in_maystro_the_app_and_the_store(self):
        response = self.new_product()
        self.assertCode(response, 201)
        self.create_product.assert_called_once()
        self.assertEqual(self.create_product.call_args.args[0], "تيشيرت صيفي")
        product = Product.objects.get(name="تيشيرت صيفي")
        self.assertEqual((product.courier_ref, product.stock_source, product.on_hand, product.price, product.cost), (REMOTE_ID, "maystro", 0, 2200, 900))
        self.assertEqual(MaystroProduct.objects.get(maystro_id=REMOTE_ID, name="تيشيرت صيفي").display_id, 9780201417291)
        self.assertEqual((response.data["available"], response.data["stock_source"], response.data["stock_mode"]), (0, "maystro", "maystro"))
        item = StoreProduct.objects.get(product=product)
        self.assertEqual((item.slug, item.title_ar, item.title_en, item.is_published), ("summer-tee", "تيشيرت صيفي", "Summer tee", True))
        self.assertEqual(response.data["in_store"]["slug"], "summer-tee")
        self.assertTrue(StoreImage.objects.get(store_product=item).image.name.endswith(".webp"))
        card = self.client.get("/api/shop/products/summer-tee/").data
        self.assertEqual((card["price"], card["available"], card["orderable"]), (2200, 0, False))
        MaystroProduct.objects.filter(maystro_id=REMOTE_ID).update(stock=[{"warehouse": 15, "warehouse_name": "Oued Smar", "quantity": 120, "available_quantity": 120}])
        cache.clear()
        card = self.client.get("/api/shop/products/summer-tee/").data
        self.assertEqual((card["available"], card["orderable"]), (120, True))

    def test_the_form_knows_whether_maystro_is_connected(self):
        self.assertEqual(self.client.get("/api/products/maystro-status/").data, {"connected": True, "store_id_set": True})
        config = MaystroConfig.load()
        config.encrypted_token = ""
        config.save()
        self.assertFalse(self.client.get("/api/products/maystro-status/").data["connected"])

    def test_the_store_and_maystro_steps_are_optional(self):
        response = self.new_product(create_in_maystro="false", add_to_store="false", name="منتج داخلي", title_en="")
        self.assertCode(response, 201)
        self.create_product.assert_not_called()
        product = Product.objects.get(name="منتج داخلي")
        self.assertEqual(product.courier_ref, "")
        self.assertFalse(StoreProduct.objects.filter(product=product).exists())
        self.assertIsNone(response.data["in_store"])
        self.assertCode(self.new_product(name="منتج بلا سعر", price=0, image=png_file()), 400)

    def test_an_existing_maystro_product_with_the_same_name_is_linked_instead(self):
        self.create_product.side_effect = MaystroError("A product with this logistical description already exists", "maystro_rejected", 400)
        with patch("apps.shipping.maystro.LiveMaystroClient.products", return_value=[remote("Other"), remote("  تيشيرت صيفي ", maystro_id="linked-1")]):
            response = self.new_product()
        self.assertCode(response, 201)
        self.assertEqual(Product.objects.get(name="تيشيرت صيفي").courier_ref, "linked-1")

    def test_nothing_is_created_when_maystro_refuses_or_is_not_connected(self):
        self.create_product.side_effect = MaystroError("تعذّر الاتصال بخادم Maystro", "maystro_unreachable", 503)
        before = Product.objects.count()
        failed = self.new_product()
        self.assertCode(failed, 502)
        self.assertIn("Maystro", failed.data["detail"])
        self.assertEqual(Product.objects.count(), before)
        self.create_product.side_effect = None
        config = MaystroConfig.load()
        config.encrypted_token = ""
        config.save()
        missing = self.new_product()
        self.assertCode(missing, 400)
        self.assertEqual(missing.data["code"], "maystro_not_configured")
        self.assertEqual(Product.objects.count(), before)

    def test_a_maystro_product_already_linked_here_is_refused(self):
        self.product.courier_ref = REMOTE_ID
        self.product.save()
        response = self.new_product()
        self.assertCode(response, 400)
        self.assertIn(self.product.name, response.data["detail"])

    def test_stock_can_be_counted_here_even_for_a_maystro_product(self):
        MaystroProduct.objects.create(maystro_id="m-1", name=self.product.name, stock=[{"warehouse": 15, "warehouse_name": "Oued Smar", "quantity": 21012, "available_quantity": 21012}])
        self.product.courier_ref = "m-1"
        self.product.save()
        listed = self.client.get(f"/api/products/{self.product.pk}/").data
        self.assertEqual((listed["stock_source"], listed["available"], listed["stock_mode"]), ("maystro", 21012, "maystro"))
        self.assertCode(self.client.post(f"/api/products/{self.product.pk}/stock-in/", {"quantity": 5}), 409)
        switched = self.client.patch(f"/api/products/{self.product.pk}/", {"stock_mode": "local"}, format="multipart")
        self.assertCode(switched, 200)
        self.assertEqual((switched.data["stock_source"], switched.data["courier_ref"]), ("local", "m-1"))
        self.assertCode(self.client.post(f"/api/products/{self.product.pk}/stock-in/", {"quantity": 40}), 201)
        self.assertEqual(self.client.get(f"/api/products/{self.product.pk}/").data["available"], 40)

    def test_the_website_new_product_can_be_created_in_maystro_too(self):
        response = self.client.post("/api/store/products/", {"new_product_name": "سروال جينز", "new_product_price": 3500, "new_product_stock": 8, "new_product_maystro": True, "title_en": "Jeans"}, format="json")
        self.assertCode(response, 201)
        product = Product.objects.get(name="سروال جينز")
        self.assertEqual((product.courier_ref, product.stock_source, product.on_hand), (REMOTE_ID, "maystro", 0))
        self.assertEqual(response.data["available"], 0)

    def test_a_product_from_maystro_goes_on_the_store_once_it_has_a_price(self):
        adopted = Product.objects.create(name="منتج من Maystro", price=0, courier_ref="m-adopted")
        local = Product.objects.create(name="منتج داخلي بلا سعر", price=0)
        self.assertCode(self.client.patch(f"/api/products/{adopted.pk}/", {"cost": 300}, format="multipart"), 200)
        self.assertFalse(StoreProduct.objects.filter(product=adopted).exists())
        priced = self.client.patch(f"/api/products/{adopted.pk}/", {"price": 1800}, format="multipart")
        self.assertCode(priced, 200)
        item = StoreProduct.objects.get(product=adopted)
        self.assertEqual((item.title_ar, item.is_published), ("منتج من Maystro", True))
        self.assertEqual(priced.data["in_store"]["id"], item.pk)
        self.client.patch(f"/api/products/{local.pk}/", {"price": 900}, format="multipart")
        self.assertFalse(StoreProduct.objects.filter(product=local).exists())

    def test_the_store_and_dashboard_keep_maystro_stock_fresh(self):
        with patch("apps.shipping.live.refresh_stock_soon") as refresh:
            self.assertCode(self.client.get("/api/shop/products/"), 200)
            self.assertCode(self.client.get("/api/shop/products/visa-card/"), 200)
            self.assertCode(self.client.get("/api/dashboard/"), 200)
        self.assertEqual(refresh.call_count, 3)

    def test_background_check_waits_for_the_first_full_sync(self):
        from django.utils import timezone

        from apps.shipping.live import refresh_stock_soon

        with patch("apps.shipping.live.refresh_stock") as refresh:
            self.assertFalse(refresh_stock_soon())
            refresh.assert_not_called()
            MaystroConfig.objects.update(live_stock_at=timezone.now())
            refresh_stock_soon()
            refresh.assert_called_once_with(background=True)

    def test_api_answers_and_store_pages_are_never_cached(self):
        for response in (self.client.get("/api/health/"), self.client.get("/api/products/"), self.client.get("/api/shop/products/"), self.store.get("/")):
            self.assertIn("no-store", response["Cache-Control"])
            self.assertIn("private", response["Cache-Control"])

