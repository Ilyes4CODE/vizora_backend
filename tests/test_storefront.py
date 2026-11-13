import json
import re
import shutil
import tempfile
import time
from io import BytesIO, StringIO
from unittest.mock import patch

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, override_settings
from PIL import Image

from apps.catalog.models import Product
from apps.core.models import Commune, Wilaya
from apps.orders.models import Order, OrderStatus, Source
from apps.storefront.models import ContactMessage, OrderOrigin, Review, StockAlert, StoreImage, StoreOffer, StorePage, StoreProduct, StoreSettings
from apps.storefront.ordering import FORM_SALT
from apps.storefront.setup import run_setup

from .base import ApiTestCase

STORE_HOST = "vizorawear.com"


def old_stamp(seconds=30):
    with patch("django.core.signing.time.time", return_value=time.time() - seconds):
        return signing.TimestampSigner(salt=FORM_SALT).sign("form")


def png_file(name="photo.png"):
    output = BytesIO()
    Image.new("RGB", (1800, 1200), (31, 107, 255)).save(output, "PNG")
    return SimpleUploadedFile(name, output.getvalue(), content_type="image/png")


def embedded_config(html):
    found = re.search(r'<script id="shop-config" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(found.group(1))


class StoreTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.store = Client(HTTP_HOST=STORE_HOST)
        Commune.objects.get_or_create(wilaya_id=16, name_ar="الدار البيضاء", defaults={"name_fr": "Dar El Beida"})
        self.card = StoreProduct.objects.create(
            product=self.product,
            slug="visa-card",
            is_published=True,
            is_featured=True,
            title_ar="بطاقة فيزا",
            title_en="Visa card",
            summary_ar="بطاقة مع التفعيل",
            summary_en="A card with activation",
        )
        self.offer_one = StoreOffer.objects.create(store_product=self.card, title_ar="العرض الأول", title_en="Offer 1", price=7900, sort_order=1)
        self.offer_two = StoreOffer.objects.create(store_product=self.card, title_ar="العرض الثاني", title_en="Offer 2", price=11900, sort_order=2)
        self.pack = StoreProduct.objects.create(product=self.product_b, slug="tidy-pack", is_published=True, title_ar="باك الترتيب", title_en="Tidy pack")

    def order_body(self, **overrides):
        body = {
            "lang": "ar",
            "stamp": old_stamp(),
            "website": "",
            "lines": [{"product": "visa-card", "offer": self.offer_two.pk, "quantity": 1}],
            "name": "كريم مزيان",
            "phone": "0555 00 41 12",
            "wilaya": "16",
            "commune": "الدار البيضاء",
            "delivery_type": "home",
            "address": "حي النصر رقم 12",
            "note": "",
            "visit": {},
        }
        body.update(overrides)
        return body

    def place(self, **overrides):
        return self.client.post("/api/shop/orders/", self.order_body(**overrides), format="json", HTTP_USER_AGENT="Phone")


class StoreShellTests(StoreTestCase):
    def test_store_host_serves_the_app_with_page_tags(self):
        home = self.store.get("/")
        self.assertEqual(home.status_code, 200)
        html = home.content.decode()
        self.assertIn('<html lang="ar" dir="rtl">', html)
        self.assertIn('<div id="root"></div>', html)
        self.assertIn('hreflang="en" href="http://vizorawear.com/en/"', html)
        self.assertIn('"@type": "Organization"', html)
        config = embedded_config(html)
        self.assertEqual(config["lang"], "ar")
        self.assertTrue(config["stamp"])
        english = self.store.get("/en/")
        self.assertIn('<html lang="en" dir="ltr">', english.content.decode())
        self.assertEqual(embedded_config(english.content.decode())["lang"], "en")
        self.assertEqual(english["Content-Language"], "en")

    def test_product_page_tags_for_previews_and_google(self):
        html = self.store.get("/p/visa-card/").content.decode()
        self.assertIn("<title>بطاقة فيزا · Vizora Global</title>", html)
        self.assertIn('property="og:description" content="بطاقة مع التفعيل"', html)
        self.assertIn('"@type": "Product"', html)
        self.assertIn('"highPrice": 11900', html)
        self.assertIn("InStock", html)
        self.assertIn("<title>Visa card · Vizora Global</title>", self.store.get("/en/p/visa-card/").content.decode())

    def test_unknown_pages_answer_404_with_the_app(self):
        for path in ("/p/nothing/", "/no-such-page/", "/en/page/missing/"):
            response = self.store.get(path)
            self.assertEqual(response.status_code, 404, path)
            self.assertIn('name="robots" content="noindex"', response.content.decode())
        self.assertEqual(self.store.get("/cart/").status_code, 200)
        self.assertIn('content="noindex"', self.store.get("/checkout/").content.decode())

    def test_api_host_does_not_serve_the_store(self):
        self.assertEqual(self.client.get("/").status_code, 404)
        self.assertEqual(self.store.get("/api/health/").status_code, 404)

    def test_sitemap_robots_and_static_files(self):
        run_setup()
        sitemap = self.store.get("/sitemap.xml").content.decode()
        self.assertIn("http://vizorawear.com/p/visa-card/", sitemap)
        self.assertIn("http://vizorawear.com/en/page/delivery/", sitemap)
        self.assertIn("Disallow: /checkout/", self.store.get("/robots.txt").content.decode())
        logo = self.store.get("/static/storefront/img/logo-96.webp")
        self.assertEqual(logo.status_code, 200)

    def test_built_app_is_used_when_present(self):
        built = self.store.get("/").content.decode()
        self.assertIn("/static/storefront/app/assets/", built)


class ShopApiTests(StoreTestCase):
    def test_config_products_wilayas_and_pages(self):
        config = self.client.get("/api/shop/config/", {"lang": "en"})
        self.assertCode(config, 200)
        self.assertEqual(config.data["business"]["phone_link"], "+213667277676")
        self.assertEqual(config.data["store"]["whatsapp_link"], "https://wa.me/213667277676")
        products = self.client.get("/api/shop/products/", {"lang": "en"})
        self.assertCode(products, 200)
        self.assertEqual([row["slug"] for row in products.data], ["visa-card", "tidy-pack"])
        self.assertEqual(products.data[0]["price"], 7900)
        self.assertTrue(products.data[0]["several_prices"])
        product = self.client.get("/api/shop/products/visa-card/")
        self.assertCode(product, 200)
        self.assertEqual([offer["price"] for offer in product.data["offers"]], [7900, 11900])
        self.assertEqual([row["slug"] for row in product.data["related"]], ["tidy-pack"])
        self.assertCode(self.client.get("/api/shop/products/nothing/"), 404)
        wilayas = self.client.get("/api/shop/wilayas/")
        self.assertCode(wilayas, 200)
        self.assertEqual(len(wilayas.data), Wilaya.objects.count())
        communes = self.client.get("/api/shop/wilayas/16/communes/")
        self.assertCode(communes, 200)
        self.assertIn({"ar": "الدار البيضاء", "fr": "Dar El Beida"}, communes.data)
        run_setup()
        page = self.client.get("/api/shop/pages/delivery/", {"lang": "en"})
        self.assertCode(page, 200)
        self.assertEqual(page.data["title"], "Delivery policy")
        self.assertCode(self.client.get("/api/shop/pages/missing/"), 404)

    def test_hidden_and_unpriced_products_are_left_out(self):
        self.pack.is_published = False
        self.pack.save()
        self.assertEqual([row["slug"] for row in self.client.get("/api/shop/products/").data], ["visa-card"])
        self.assertCode(self.client.get("/api/shop/products/tidy-pack/"), 404)
        self.pack.is_published = True
        self.pack.save()
        self.product_b.price = 0
        self.product_b.save()
        self.assertEqual([row["slug"] for row in self.client.get("/api/shop/products/").data], ["visa-card"])

    def test_order_reaches_the_confirmation_queue(self):
        response = self.place()
        self.assertCode(response, 201)
        order = Order.objects.get()
        self.assertEqual(response.data["reference"], order.reference)
        self.assertEqual(order.source, Source.WEBSITE)
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertEqual(order.phone, "0555004112")
        self.assertEqual(order.subtotal, 11900)
        self.assertEqual(order.delivery_fee, Wilaya.objects.get(pk=16).home_fee)
        self.assertEqual(response.data["total"], 11900 + order.delivery_fee)
        self.assertEqual(response.data["lines"], [{"title": "بطاقة فيزا", "quantity": 1, "total": 11900}])
        self.assertIn("العرض الثاني", order.note)
        origin = OrderOrigin.objects.get(order=order)
        self.assertEqual((origin.lang, origin.user_agent), ("ar", "Phone"))
        queue = self.client.get("/api/confirmation/queue/")
        self.assertIn(order.pk, [row["id"] for row in queue.data["results"]])
        listing = self.client.get("/api/orders/")
        self.assertEqual(listing.data["results"][0]["source"], "website")

    def test_desk_delivery_and_several_lines(self):
        response = self.place(
            delivery_type="desk",
            address="",
            lines=[{"product": "visa-card", "offer": self.offer_one.pk, "quantity": 2}, {"product": "tidy-pack", "quantity": 1}],
        )
        self.assertCode(response, 201)
        order = Order.objects.get()
        self.assertEqual(order.delivery_fee, Wilaya.objects.get(pk=16).desk_fee)
        self.assertEqual(order.subtotal, 15800 + self.product_b.price)
        self.assertEqual(order.items.count(), 2)

    def test_invalid_fields_come_back_translated(self):
        response = self.place(phone="12345", commune="مكان آخر", address="", name="a")
        self.assertEqual(response.status_code, 422)
        self.assertIn("اكتب رقم هاتف جزائري صحيح", response.data["errors"]["phone"])
        self.assertEqual(set(response.data["errors"]), {"phone", "commune", "address", "name", "form"})
        english = self.place(lang="en", lines=[{"product": "visa-card", "offer": 999, "quantity": 1}])
        self.assertEqual(english.data["errors"]["lines"], "Choose an offer.")
        too_many = self.place(lines=[{"product": "visa-card", "offer": self.offer_one.pk, "quantity": 99}])
        self.assertIn("lines", too_many.data["errors"])
        self.assertFalse(Order.objects.exists())

    def test_bots_are_dropped(self):
        self.assertEqual(self.place(website="http://spam").status_code, 422)
        self.assertEqual(self.place(stamp=signing.TimestampSigner(salt=FORM_SALT).sign("form")).status_code, 422)
        forged = self.place(stamp="forged")
        self.assertEqual(forged.data["errors"]["form"], "انتهت صلاحية الصفحة، أعد المحاولة.")
        self.assertFalse(Order.objects.exists())

    def test_same_customer_gets_the_open_order_back(self):
        first = self.place()
        again = self.place()
        self.assertCode(again, 200)
        self.assertTrue(again.data["existing"])
        self.assertEqual(again.data["reference"], first.data["reference"])
        self.assertEqual(Order.objects.count(), 1)

    def test_orders_per_hour_limit_and_closed_store(self):
        StoreSettings.objects.update_or_create(pk=1, defaults={"orders_per_hour": 1})
        self.place()
        limited = self.place(phone="0661 99 88 77")
        self.assertIn("طلبيات كثيرة", limited.data["errors"]["form"])
        StoreSettings.objects.update_or_create(pk=1, defaults={"orders_per_hour": 5, "is_open": False})
        closed = self.place(phone="0770 99 88 77")
        self.assertIn("لا يستقبل طلبيات", closed.data["errors"]["form"])
        self.assertEqual(Order.objects.count(), 1)

    def test_not_enough_stock_and_alerts(self):
        Product.objects.filter(pk=self.product_b.pk).update(on_hand=0)
        cache.clear()
        product = self.client.get("/api/shop/products/tidy-pack/")
        self.assertEqual((product.data["state"], product.data["orderable"]), ("out", False))
        response = self.place(lines=[{"product": "tidy-pack", "quantity": 1}])
        self.assertIn("غير متوفرة", response.data["errors"]["form"])
        alert = self.client.post("/api/shop/products/tidy-pack/notify/", {"phone": "0661 22 33 44", "stamp": old_stamp()}, format="json")
        self.assertCode(alert, 201)
        self.assertTrue(StockAlert.objects.filter(store_product=self.pack, phone="0661223344").exists())
        self.assertEqual(self.client.post("/api/shop/products/tidy-pack/notify/", {"phone": "1"}, format="json").status_code, 422)

    def test_campaign_from_ad_link_is_attached(self):
        campaign = self.client.post("/api/campaigns/", {"name": "fb-november", "platform": "facebook"})
        self.place(visit={"utm_source": "facebook", "utm_campaign": "fb-november", "referrer": "https://m.facebook.com/"})
        order = Order.objects.get()
        self.assertEqual(order.campaign_id, campaign.data["id"])
        self.assertEqual((order.web.utm_source, order.web.referrer), ("facebook", "https://m.facebook.com/"))

    def test_tracking(self):
        reference = self.place().data["reference"]
        found = self.client.post("/api/shop/track/", {"reference": reference.lower(), "phone": "0555004112"}, format="json")
        self.assertCode(found, 200)
        self.assertEqual([step["state"] for step in found.data["steps"]], ["current", "todo", "todo", "todo"])
        missing = self.client.post("/api/shop/track/", {"reference": reference, "phone": "0661000000", "lang": "en"}, format="json")
        self.assertEqual(missing.status_code, 404)
        self.assertIn("could not find", missing.data["detail"])
        order = Order.objects.get()
        self.client.post(f"/api/confirmation/orders/{order.pk}/outcome/", {"outcome": "cancelled"})
        cancelled = self.client.post("/api/shop/track/", {"reference": reference, "phone": "0555004112"}, format="json")
        self.assertTrue(cancelled.data["cancelled"])

    def test_contact_form(self):
        sent = self.client.post("/api/shop/contact/", {"stamp": old_stamp(), "name": "سمير", "phone": "0770 11 22 33", "message": "أريد الاستفسار"}, format="json")
        self.assertCode(sent, 201)
        self.assertEqual(ContactMessage.objects.get().phone, "0770112233")
        bad = self.client.post("/api/shop/contact/", {"stamp": old_stamp(), "name": "S", "phone": "1", "message": "", "lang": "en"}, format="json")
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(bad.data["errors"]["message"], "Write your message.")

    def test_public_api_needs_no_login(self):
        self.anonymous()
        self.assertCode(self.client.get("/api/shop/products/"), 200)
        self.assertCode(self.place(), 201)

    def test_editing_a_website_order_keeps_the_offer_price(self):
        self.place()
        order = Order.objects.get()
        response = self.client.patch(f"/api/orders/{order.pk}/", {"items": [{"product": self.product.pk, "quantity": 2}]})
        self.assertCode(response, 200)
        order.refresh_from_db()
        self.assertEqual(order.subtotal, 23800)

    def test_illustrations_are_listed_once_added(self):
        self.assertEqual(self.client.get("/api/shop/config/").data["illustrations"], {})
        with patch("apps.storefront.public.illustration_path", side_effect=lambda name: "x" if name == "hero-1" else ""), patch(
            "apps.storefront.public.illustration", return_value="/static/storefront/illustrations/hero-1.webp?v=1"
        ):
            self.assertEqual(list(self.client.get("/api/shop/config/").data["illustrations"]), ["hero-1"])


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class StoreAdminApiTests(StoreTestCase):
    def tearDown(self):
        super().tearDown()
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)

    def test_summary_and_settings(self):
        self.place()
        summary = self.client.get("/api/store/summary/")
        self.assertCode(summary, 200)
        self.assertEqual((summary.data["orders_today"], summary.data["orders_pending"]), (1, 1))
        self.assertEqual(summary.data["store_url"], "https://vizorawear.com")
        self.assertCode(self.client.get("/api/store/settings/"), 200)
        self.assertCode(self.client.patch("/api/store/settings/", {"facebook_pixel": "123456", "announcement_ar": "توصيل مجاني"}), 200)
        config = embedded_config(self.store.get("/").content.decode())
        self.assertEqual((config["store"]["facebook_pixel"], config["store"]["announcement"]), ("123456", "توصيل مجاني"))
        self.assertCode(self.client.patch("/api/store/settings/", {"orders_per_hour": 0}), 400)

    def test_products_offers_and_images(self):
        self.assertEqual(len(self.client.get("/api/store/products/").data), 2)
        extra = Product.objects.create(name="منتج جديد", price=0)
        created = self.client.post("/api/store/products/", {"product": extra.pk, "title_ar": "منتج جديد", "title_en": "New Thing"})
        self.assertCode(created, 201)
        self.assertEqual(created.data["slug"], "new-thing")
        self.assertFalse(created.data["is_published"])
        self.assertCode(self.client.patch(f"/api/store/products/{created.data['id']}/", {"is_published": True}), 400)
        offer = self.client.post("/api/store/offers/", {"store_product": created.data["id"], "title_ar": "عرض", "price": 4500})
        self.assertCode(offer, 201)
        self.assertCode(self.client.patch(f"/api/store/products/{created.data['id']}/", {"is_published": True, "slug": "new-thing-2"}), 200)
        self.assertCode(self.client.patch(f"/api/store/products/{created.data['id']}/", {"slug": "Bad Slug"}), 400)
        self.assertCode(self.client.get(f"/api/store/products/{created.data['id']}/"), 200)
        self.assertCode(self.client.patch(f"/api/store/offers/{offer.data['id']}/", {"price": 4900}), 200)
        self.assertEqual(self.client.get("/api/shop/products/new-thing-2/").data["price"], 4900)
        upload = self.client.post(f"/api/store/products/{created.data['id']}/images/", {"image": png_file(), "alt_ar": "صورة"}, format="multipart")
        self.assertCode(upload, 201)
        self.assertTrue(upload.data["url"].startswith("http://testserver/media/products/new-thing-2-"))
        image = StoreImage.objects.get()
        self.assertTrue(image.image.name.endswith(".webp"))
        self.assertEqual((image.width, image.height), (1400, 933))
        self.assertEqual(self.client.get("/api/shop/products/new-thing-2/").data["images"][0]["url"], image.image.url)
        self.assertEqual(self.store.get(image.image.url).status_code, 200)
        self.assertIn(image.image.url, self.store.get("/p/new-thing-2/").content.decode())
        self.assertCode(self.client.post(f"/api/store/products/{created.data['id']}/images/", {"image": SimpleUploadedFile("x.png", b"nope")}, format="multipart"), 400)
        self.assertCode(self.client.patch(f"/api/store/images/{image.pk}/", {"sort_order": 3}), 200)
        self.assertCode(self.client.delete(f"/api/store/images/{image.pk}/"), 204)
        self.assertCode(self.client.delete(f"/api/store/offers/{offer.data['id']}/"), 204)
        self.assertCode(self.client.delete(f"/api/store/products/{created.data['id']}/"), 204)
        self.assertTrue(Product.objects.filter(pk=extra.pk).exists())

    def test_pages_reviews_messages_alerts(self):
        run_setup()
        pages = self.client.get("/api/store/pages/")
        self.assertCode(pages, 200)
        returns = next(row for row in pages.data if row["slug"] == "returns")
        self.assertCode(self.client.patch(f"/api/store/pages/{returns['id']}/", {"body_ar": "نص جديد"}), 200)
        self.assertEqual(self.client.get("/api/shop/pages/returns/").data["body"], "نص جديد")
        review = self.client.post("/api/store/reviews/", {"name": "أمينة", "wilaya": "وهران", "text": "وصلتني البطاقة بسرعة", "rating": 5})
        self.assertCode(review, 201)
        self.assertEqual([row["text"] for row in self.client.get("/api/shop/config/").data["reviews"]], ["وصلتني البطاقة بسرعة"])
        self.assertCode(self.client.get("/api/store/reviews/"), 200)
        self.assertCode(self.client.patch(f"/api/store/reviews/{review.data['id']}/", {"is_published": False}), 200)
        self.assertEqual(self.client.get("/api/shop/config/").data["reviews"], [])
        self.assertCode(self.client.post("/api/store/reviews/", {"name": "x", "text": "y", "rating": 9}), 400)
        self.assertCode(self.client.delete(f"/api/store/reviews/{review.data['id']}/"), 204)
        message = ContactMessage.objects.create(name="سمير", phone="0770112233", message="سؤال")
        self.assertCode(self.client.get("/api/store/messages/"), 200)
        handled = self.client.patch(f"/api/store/messages/{message.pk}/", {"handled": True})
        self.assertCode(handled, 200)
        self.assertIsNotNone(handled.data["handled_at"])
        self.as_user(self.agent)
        self.assertCode(self.client.get("/api/store/messages/"), 200)
        self.assertCode(self.client.get("/api/store/products/"), 403)
        self.as_user(self.admin)
        self.assertCode(self.client.delete(f"/api/store/messages/{message.pk}/"), 204)
        alert = StockAlert.objects.create(store_product=self.pack, phone="0661223344")
        self.assertCode(self.client.get("/api/store/alerts/"), 200)
        self.assertCode(self.client.patch(f"/api/store/alerts/{alert.pk}/", {"notified": True}), 200)
        self.assertCode(self.client.delete(f"/api/store/alerts/{alert.pk}/"), 204)


class StoreSetupTests(StoreTestCase):
    def test_setup_command_publishes_real_products_and_pages(self):
        StoreProduct.objects.all().delete()
        visa = Product.objects.create(name="كارت فيزا (كاتالوج)", price=0)
        Product.objects.create(name="باك الترتيب والنظافة", price=0)
        call_command("setup_store", stdout=StringIO())
        item = StoreProduct.objects.get(product=visa)
        visa.refresh_from_db()
        self.assertEqual(visa.price, 7900)
        self.assertTrue(item.is_published)
        self.assertEqual(list(item.offers.values_list("price", flat=True)), [7900, 11900])
        self.assertEqual(StorePage.objects.count(), 4)
        self.assertTrue(StoreProduct.objects.filter(slug="tidy-clean-pack").exists())
        run_setup()
        self.assertEqual(StoreOffer.objects.filter(store_product=item).count(), 2)
        self.assertEqual(Review.objects.count(), 0)
