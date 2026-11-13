import shutil
import tempfile
import time
from io import BytesIO, StringIO
from unittest.mock import patch

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

    def order_form(self, **overrides):
        data = {
            "stamp": old_stamp(),
            "website": "",
            "offer": str(self.offer_two.pk),
            "quantity": "1",
            "name": "كريم مزيان",
            "phone": "0555 00 41 12",
            "wilaya": "16",
            "commune": "الدار البيضاء",
            "delivery_type": "home",
            "address": "حي النصر رقم 12",
            "note": "",
        }
        data.update(overrides)
        return data


class StorePagesTests(StoreTestCase):
    def test_store_host_serves_the_website_in_both_languages(self):
        home = self.store.get("/")
        self.assertEqual(home.status_code, 200)
        html = home.content.decode()
        self.assertIn('lang="ar"', html)
        self.assertIn('dir="rtl"', html)
        self.assertIn("بطاقة فيزا", html)
        self.assertIn('hreflang="en"', html)
        english = self.store.get("/en/")
        self.assertEqual(english.status_code, 200)
        self.assertIn('dir="ltr"', english.content.decode())
        self.assertIn("Visa card", english.content.decode())
        self.assertEqual(english["Content-Language"], "en")

    def test_api_host_does_not_serve_the_website(self):
        self.assertEqual(self.client.get("/").status_code, 404)
        self.assertEqual(self.store.get("/api/health/").status_code, 404)

    def test_product_page_lists_offers_and_schema(self):
        response = self.store.get("/p/visa-card/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("11,900 دج", html)
        self.assertIn('"@type": "Product"', html)
        self.assertIn("InStock", html)
        self.assertIn('id="wilaya-data"', html)
        self.assertEqual(self.store.get("/en/p/visa-card/").status_code, 200)

    def test_unpublished_and_unknown_products_are_hidden(self):
        self.pack.is_published = False
        self.pack.save()
        self.assertEqual(self.store.get("/p/tidy-pack/").status_code, 404)
        self.assertNotIn("باك الترتيب", self.store.get("/products/").content.decode())
        self.assertEqual(self.store.get("/p/nothing/").status_code, 404)

    def test_products_without_price_are_not_listed(self):
        self.product_b.price = 0
        self.product_b.save()
        self.assertNotIn("باك الترتيب", self.store.get("/products/").content.decode())

    def test_policy_pages_sitemap_robots_and_communes(self):
        run_setup()
        page = self.store.get("/page/delivery/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Maystro", page.content.decode())
        self.assertEqual(self.store.get("/en/page/returns/").status_code, 200)
        sitemap = self.store.get("/sitemap.xml")
        self.assertEqual(sitemap.status_code, 200)
        self.assertIn("http://vizorawear.com/p/visa-card/", sitemap.content.decode())
        self.assertIn('hreflang="en"', sitemap.content.decode())
        robots = self.store.get("/robots.txt")
        self.assertIn("Disallow: /checkout/", robots.content.decode())
        communes = self.store.get("/communes/16.json").json()["communes"]
        self.assertIn({"ar": "الدار البيضاء", "fr": "Dar El Beida"}, communes)

    def test_static_files_are_served(self):
        response = self.store.get("/static/storefront/css/store.css")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"--navy", b"".join(response.streaming_content))

    def test_not_found_page_is_the_store_page(self):
        with override_settings(DEBUG=False):
            response = self.store.get("/en/missing-page/")
        self.assertEqual(response.status_code, 404)
        self.assertIn("Page not found", response.content.decode())

    def test_sold_out_product_shows_alert_form(self):
        Product.objects.filter(pk=self.product_b.pk).update(on_hand=0)
        cache.clear()
        html = self.store.get("/p/tidy-pack/").content.decode()
        self.assertIn("نفذ المخزون", html)
        self.assertIn("/p/tidy-pack/notify/", html)
        response = self.store.post("/p/tidy-pack/notify/", {"stamp": old_stamp(), "phone": "0661 22 33 44"})
        self.assertRedirects(response, "/p/tidy-pack/?notified=1", fetch_redirect_response=False)
        self.assertTrue(StockAlert.objects.filter(store_product=self.pack, phone="0661223344").exists())

    def test_illustrations_appear_once_added(self):
        html = self.store.get("/").content.decode()
        self.assertIn("pay-card", html)
        with patch("apps.storefront.templatetags.store.illustration_path", return_value="storefront/img/logo-96.webp"):
            html = self.store.get("/").content.decode()
        self.assertNotIn('class="pay-card"', html)


class StoreOrderTests(StoreTestCase):
    def test_order_from_product_page_reaches_the_confirmation_queue(self):
        response = self.store.post("/p/visa-card/", self.order_form(), HTTP_USER_AGENT="Phone")
        order = Order.objects.get()
        self.assertRedirects(response, f"/thanks/{order.reference}/", fetch_redirect_response=False)
        self.assertEqual(order.source, Source.WEBSITE)
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertEqual(order.phone, "0555004112")
        self.assertEqual(order.subtotal, 11900)
        self.assertEqual(order.delivery_fee, Wilaya.objects.get(pk=16).home_fee)
        self.assertEqual(order.total, 11900 + order.delivery_fee)
        self.assertIn("العرض الثاني", order.note)
        self.assertEqual(order.items.get().unit_price, 11900)
        origin = OrderOrigin.objects.get(order=order)
        self.assertEqual((origin.lang, origin.user_agent), ("ar", "Phone"))
        thanks = self.store.get(f"/thanks/{order.reference}/")
        self.assertEqual(thanks.status_code, 200)
        self.assertIn(order.reference, thanks.content.decode())
        queue = self.client.get("/api/confirmation/queue/")
        self.assertCode(queue, 200)
        self.assertIn(order.pk, [row["id"] for row in queue.data["results"]])
        listing = self.client.get("/api/orders/", {"source": "website"})
        self.assertCode(listing, 200)

    def test_desk_delivery_uses_desk_fee_and_skips_address(self):
        self.store.post("/p/visa-card/", self.order_form(delivery_type="desk", address="", offer=str(self.offer_one.pk), quantity="2"))
        order = Order.objects.get()
        self.assertEqual(order.delivery_fee, Wilaya.objects.get(pk=16).desk_fee)
        self.assertEqual(order.subtotal, 15800)

    def test_invalid_fields_are_shown_and_nothing_is_saved(self):
        response = self.store.post("/p/visa-card/", self.order_form(phone="12345", commune="مكان آخر", address="", name="a"))
        self.assertEqual(response.status_code, 422)
        html = response.content.decode()
        self.assertIn("اكتب رقم هاتف جزائري صحيح", html)
        self.assertIn("اختر البلدية", html)
        self.assertIn("اكتب عنوانك", html)
        self.assertFalse(Order.objects.exists())
        english = self.store.post("/en/p/visa-card/", self.order_form(offer="999"))
        self.assertIn("Choose an offer.", english.content.decode())

    def test_bots_are_dropped(self):
        response = self.store.post("/p/visa-card/", self.order_form(website="http://spam"))
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        fast = self.store.post("/p/visa-card/", self.order_form(stamp=signing.TimestampSigner(salt=FORM_SALT).sign("form")))
        self.assertRedirects(fast, "/", fetch_redirect_response=False)
        forged = self.store.post("/p/visa-card/", self.order_form(stamp="forged"))
        self.assertEqual(forged.status_code, 422)
        self.assertFalse(Order.objects.exists())

    def test_same_customer_does_not_create_a_second_open_order(self):
        self.store.post("/p/visa-card/", self.order_form())
        again = self.store.post("/p/visa-card/", self.order_form())
        order = Order.objects.get()
        self.assertRedirects(again, f"/thanks/{order.reference}/?existing=1", fetch_redirect_response=False)
        self.assertIn("بانتظار التأكيد", self.store.get(f"/thanks/{order.reference}/?existing=1").content.decode())

    def test_orders_per_hour_limit(self):
        settings = StoreSettings.load()
        settings.orders_per_hour = 1
        settings.save()
        self.store.post("/p/visa-card/", self.order_form())
        response = self.store.post("/p/visa-card/", self.order_form(phone="0661 99 88 77"))
        self.assertEqual(response.status_code, 422)
        self.assertIn("طلبيات كثيرة", response.content.decode())
        self.assertEqual(Order.objects.count(), 1)

    def test_closed_store_takes_no_orders(self):
        StoreSettings.objects.update_or_create(pk=1, defaults={"is_open": False})
        self.assertIn("لا يستقبل طلبيات", self.store.get("/p/visa-card/").content.decode())
        response = self.store.post("/p/visa-card/", self.order_form())
        self.assertEqual(response.status_code, 422)
        self.assertFalse(Order.objects.exists())

    def test_not_enough_stock(self):
        Product.objects.filter(pk=self.product.pk).update(on_hand=1)
        cache.clear()
        response = self.store.post("/p/visa-card/", self.order_form(quantity="2"))
        self.assertEqual(response.status_code, 422)
        self.assertFalse(Order.objects.exists())

    def test_thanks_page_needs_the_same_browser(self):
        self.store.post("/p/visa-card/", self.order_form())
        order = Order.objects.get()
        other = Client(HTTP_HOST=STORE_HOST)
        self.assertRedirects(other.get(f"/thanks/{order.reference}/"), "/track/", fetch_redirect_response=False)

    def test_campaign_from_ad_link_is_attached(self):
        campaign = self.client.post("/api/campaigns/", {"name": "fb-november", "platform": "facebook"})
        self.assertCode(campaign, 201)
        self.store.get("/?utm_source=facebook&utm_campaign=fb-november")
        self.store.post("/p/visa-card/", self.order_form())
        order = Order.objects.get()
        self.assertEqual(order.campaign_id, campaign.data["id"])
        self.assertEqual(order.web.utm_source, "facebook")

    def test_cart_and_checkout(self):
        self.store.post("/cart/add/", {"product": self.card.pk, "offer": self.offer_one.pk, "quantity": 2})
        response = self.store.post("/cart/add/", {"product": self.pack.pk, "quantity": 1})
        self.assertRedirects(response, "/cart/?added=1", fetch_redirect_response=False)
        cart = self.store.get("/cart/")
        self.assertIn("15,800 دج", cart.content.decode())
        self.store.post("/cart/update/", {f"qty-{self.card.pk}-{self.offer_one.pk}": 1})
        self.assertIn("7,900 دج", self.store.get("/cart/").content.decode())
        self.assertEqual(self.store.get("/checkout/").status_code, 200)
        form = self.order_form()
        form.pop("offer")
        form.pop("quantity")
        response = self.store.post("/checkout/", form)
        order = Order.objects.get()
        self.assertRedirects(response, f"/thanks/{order.reference}/", fetch_redirect_response=False)
        self.assertEqual(order.subtotal, 7900 + self.product_b.price)
        self.assertEqual(order.items.count(), 2)
        self.assertRedirects(self.store.get("/checkout/"), "/cart/", fetch_redirect_response=False)
        self.store.post("/cart/add/", {"product": self.pack.pk, "quantity": 1})
        self.store.post("/cart/update/", {"remove": f"{self.pack.pk}-0"})
        self.assertIn("سلتك فارغة", self.store.get("/cart/").content.decode())

    def test_tracking_page(self):
        self.store.post("/p/visa-card/", self.order_form())
        order = Order.objects.get()
        found = self.store.post("/track/", {"reference": order.reference.lower(), "phone": "0555004112"})
        self.assertIn("وصلتنا الطلبية", found.content.decode())
        self.assertNotIn("لم نجد", found.content.decode())
        missing = self.store.post("/en/track/", {"reference": order.reference, "phone": "0661000000"})
        self.assertIn("could not find", missing.content.decode())
        self.client.post(f"/api/confirmation/orders/{order.pk}/outcome/", {"outcome": "cancelled"})
        cancelled = self.store.post("/track/", {"reference": order.reference, "phone": "0555004112"})
        self.assertIn("أُلغيت", cancelled.content.decode())

    def test_contact_form(self):
        response = self.store.post("/contact/", {"stamp": old_stamp(), "name": "سمير", "phone": "0770 11 22 33", "message": "أريد الاستفسار"})
        self.assertRedirects(response, "/?sent=1#contact", fetch_redirect_response=False)
        self.assertEqual(ContactMessage.objects.get().phone, "0770112233")
        bad = self.store.post("/en/contact/", {"stamp": old_stamp(), "name": "S", "phone": "1", "message": ""})
        self.assertEqual(bad.status_code, 422)
        self.assertIn("Write your message.", bad.content.decode())

    def test_csrf_failure_shows_friendly_page(self):
        strict = Client(HTTP_HOST=STORE_HOST, enforce_csrf_checks=True)
        response = strict.post("/p/visa-card/", self.order_form())
        self.assertEqual(response.status_code, 403)
        self.assertIn("انتهت صلاحية الصفحة", response.content.decode())

    def test_editing_a_website_order_keeps_the_offer_price(self):
        self.store.post("/p/visa-card/", self.order_form())
        order = Order.objects.get()
        response = self.client.patch(f"/api/orders/{order.pk}/", {"items": [{"product": self.product.pk, "quantity": 2}]})
        self.assertCode(response, 200)
        order.refresh_from_db()
        self.assertEqual(order.subtotal, 23800)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class StoreAdminApiTests(StoreTestCase):
    def tearDown(self):
        super().tearDown()
        from django.conf import settings

        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)

    def test_summary_and_settings(self):
        self.store.post("/p/visa-card/", self.order_form())
        summary = self.client.get("/api/store/summary/")
        self.assertCode(summary, 200)
        self.assertEqual((summary.data["orders_today"], summary.data["orders_pending"]), (1, 1))
        self.assertEqual(summary.data["store_url"], "https://vizorawear.com")
        self.assertCode(self.client.get("/api/store/settings/"), 200)
        updated = self.client.patch("/api/store/settings/", {"facebook_pixel": "123456", "announcement_ar": "توصيل مجاني"})
        self.assertCode(updated, 200)
        html = self.store.get("/").content.decode()
        self.assertIn("fbq('init','123456')", html)
        self.assertIn("توصيل مجاني", html)
        self.assertCode(self.client.patch("/api/store/settings/", {"orders_per_hour": 0}), 400)

    def test_products_offers_and_images(self):
        listing = self.client.get("/api/store/products/")
        self.assertCode(listing, 200)
        self.assertEqual(len(listing.data), 2)
        extra = Product.objects.create(name="منتج جديد", price=0)
        created = self.client.post("/api/store/products/", {"product": extra.pk, "title_ar": "منتج جديد", "title_en": "New Thing"})
        self.assertCode(created, 201)
        self.assertEqual(created.data["slug"], "new-thing")
        self.assertFalse(created.data["is_published"])
        refused = self.client.patch(f"/api/store/products/{created.data['id']}/", {"is_published": True})
        self.assertCode(refused, 400)
        offer = self.client.post("/api/store/offers/", {"store_product": created.data["id"], "title_ar": "عرض", "price": 4500})
        self.assertCode(offer, 201)
        published = self.client.patch(f"/api/store/products/{created.data['id']}/", {"is_published": True, "slug": "new-thing-2"})
        self.assertCode(published, 200)
        self.assertCode(self.client.patch(f"/api/store/products/{created.data['id']}/", {"slug": "Bad Slug"}), 400)
        self.assertCode(self.client.get(f"/api/store/products/{created.data['id']}/"), 200)
        self.assertCode(self.client.patch(f"/api/store/offers/{offer.data['id']}/", {"price": 4900}), 200)
        self.assertEqual(self.store.get("/p/new-thing-2/").status_code, 200)
        upload = self.client.post(f"/api/store/products/{created.data['id']}/images/", {"image": png_file(), "alt_ar": "صورة"}, format="multipart")
        self.assertCode(upload, 201)
        image = StoreImage.objects.get()
        self.assertTrue(image.image.name.endswith(".webp"))
        self.assertEqual((image.width, image.height), (1400, 933))
        self.assertIn(image.image.url, self.store.get("/p/new-thing-2/").content.decode())
        self.assertEqual(self.store.get(image.image.url).status_code, 200)
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
        self.assertIn("نص جديد", self.store.get("/page/returns/").content.decode())
        review = self.client.post("/api/store/reviews/", {"name": "أمينة", "wilaya": "وهران", "text": "وصلتني البطاقة بسرعة", "rating": 5})
        self.assertCode(review, 201)
        self.assertIn("وصلتني البطاقة بسرعة", self.store.get("/").content.decode())
        self.assertCode(self.client.get("/api/store/reviews/"), 200)
        self.assertCode(self.client.patch(f"/api/store/reviews/{review.data['id']}/", {"is_published": False}), 200)
        self.assertNotIn("وصلتني البطاقة بسرعة", self.store.get("/").content.decode())
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
        self.assertIn("بطاقة فيزا (كاتالوج)", self.store.get("/").content.decode())
        self.assertEqual(Review.objects.count(), 0)
