import copy
import shutil
import tempfile

from django.conf import settings
from django.test import override_settings

from apps.storefront.models import Layout, LayoutVersion, OrderOrigin

from .test_storefront import StoreTestCase, embedded_config, png_file


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class DesignTestCase(StoreTestCase):
    def tearDown(self):
        super().tearDown()
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)

    def design(self):
        response = self.client.get("/api/store/design/")
        self.assertCode(response, 200)
        return response.data

    def layout(self, slug):
        row = next(row for row in self.design()["layouts"] if row["slug"] == slug)
        return self.client.get(f"/api/store/design/layouts/{row['id']}/").data

    def save(self, layout, draft, code=200):
        response = self.client.patch(f"/api/store/design/layouts/{layout['id']}/", {"draft": draft}, format="json")
        self.assertCode(response, code)
        return response.data

    def publish(self, layout, code=200):
        response = self.client.post(f"/api/store/design/layouts/{layout['id']}/publish/")
        self.assertCode(response, code)
        return response.data

    def landing(self, template="quick", lang="ar", **extra):
        body = {"name": "عرض البطاقة", "product": self.card.pk, "lang": lang, "template": template, **extra}
        response = self.client.post("/api/store/design/layouts/", body, format="json")
        self.assertCode(response, 201)
        return response.data


class DefaultDesignTests(DesignTestCase):
    def test_defaults_reproduce_the_current_store(self):
        data = self.design()
        self.assertEqual([row["kind"] for row in data["layouts"]], ["site", "home"])
        self.assertTrue(all(not row["has_changes"] for row in data["layouts"]))
        self.assertEqual({row["type"] for row in data["schema"]["sections"]} >= {"hero", "features", "order_form", "product_hero"}, True)
        self.assertTrue(data["preview"]["token"])
        home = self.layout("home")
        self.assertEqual(
            [section["type"] for section in home["published"]["sections"]],
            ["hero", "features", "products", "steps", "promo", "reviews", "faq", "contact"],
        )
        hero = home["published"]["sections"][0]["settings"]["slides"][0]
        self.assertEqual(hero["title"]["ar"], "بطاقة فيزا جاهزة للاستعمال، تصلك حتى باب الدار")
        self.assertEqual(hero["title"]["en"], "A ready-to-use Visa card, delivered to your door")
        site = self.layout("site")["published"]
        self.assertEqual([link["link"] for link in site["menu"]["links"]], ["/", "/products/", "/#services", "/track/", "/#faq", "/#contact"])
        config = self.client.get("/api/shop/config/").data
        self.assertEqual(config["design"]["vars"]["--blue"], "#1F6BFF")
        self.assertEqual(len(config["home"]), 8)
        html = self.store.get("/").content.decode()
        self.assertIn('<style id="theme-vars">:root{--blue:#1F6BFF', html)
        self.assertEqual(embedded_config(html)["home"][0]["type"], "hero")


class DraftAndPublishTests(DesignTestCase):
    def test_drafts_stay_private_until_published(self):
        home = self.layout("home")
        draft = copy.deepcopy(home["draft"])
        draft["sections"][0]["settings"]["slides"][0]["title"] = {"ar": "عنوان جديد", "en": "New title"}
        draft["sections"].insert(1, draft["sections"].pop(4))
        draft["sections"][2]["hidden"] = True
        saved = self.save(home, draft)
        self.assertTrue(saved["has_changes"])
        self.assertEqual(self.client.get("/api/shop/config/").data["home"][0]["settings"]["slides"][0]["title"]["ar"], "بطاقة فيزا جاهزة للاستعمال، تصلك حتى باب الدار")
        token = self.design()["preview"]["token"]
        self.assertCode(self.client.get("/api/shop/design/preview/", {"token": "wrong"}), 403)
        preview = self.client.get("/api/shop/design/preview/", {"token": token})
        self.assertCode(preview, 200)
        self.assertEqual([row["type"] for row in preview.data["home"]][:3], ["hero", "promo", "features"])
        framed = self.store.get(f"/?preview={token}")
        self.assertNotIn("X-Frame-Options", framed.headers)
        self.assertIn("frame-ancestors 'self' app://vizora", framed["Content-Security-Policy"])
        self.assertIn("no-store", framed["Cache-Control"])
        self.assertIn('content="noindex"', framed.content.decode())
        self.assertEqual(embedded_config(framed.content.decode())["home"][0]["settings"]["slides"][0]["title"]["ar"], "عنوان جديد")
        self.assertEqual(self.store.get("/?preview=wrong")["X-Frame-Options"], "DENY")
        published = self.publish(home)
        self.assertFalse(published["has_changes"])
        public = self.client.get("/api/shop/config/").data["home"]
        self.assertEqual(public[0]["settings"]["slides"][0]["title"]["en"], "New title")
        self.assertTrue(public[2]["hidden"])
        versions = self.client.get(f"/api/store/design/layouts/{home['id']}/versions/")
        self.assertCode(versions, 200)
        self.assertEqual(len(versions.data), 1)
        draft["sections"][0]["settings"]["slides"][0]["title"] = {"ar": "ثالث", "en": "Third"}
        self.save(home, draft)
        self.assertCode(self.client.post(f"/api/store/design/layouts/{home['id']}/discard/"), 200)
        self.assertEqual(self.layout("home")["draft"]["sections"][0]["settings"]["slides"][0]["title"]["ar"], "عنوان جديد")
        restored = self.client.post(f"/api/store/design/layouts/{home['id']}/versions/{versions.data[0]['id']}/restore/")
        self.assertCode(restored, 200)
        for _ in range(11):
            self.publish(home)
        self.assertEqual(LayoutVersion.objects.filter(layout_id=home["id"]).count(), 10)

    def test_theme_menu_and_footer(self):
        site = self.layout("site")
        draft = copy.deepcopy(site["draft"])
        draft["theme"].update(palette="custom", primary="#0e9f6e", dark="#06281f", font="tajawal", radius="soft")
        draft["menu"]["links"] = draft["menu"]["links"][:2]
        draft["footer"]["about"] = {"ar": "نبذة", "en": "About"}
        self.save(site, draft)
        self.publish(site)
        design = self.client.get("/api/shop/config/").data["design"]
        self.assertEqual((design["vars"]["--blue"], design["vars"]["--radius"]), ("#0E9F6E", "10px"))
        self.assertIn("Tajawal", design["font"])
        self.assertEqual(len(design["menu"]["links"]), 2)
        html = self.store.get("/").content.decode()
        self.assertIn("--blue:#0E9F6E", html)
        self.assertIn("family=Tajawal", html)
        draft["theme"].update(primary="#F5F5F5")
        error = self.save(site, draft, 400)
        self.assertIn("theme.primary", error["errors"])
        draft["theme"].update(primary="#0E9F6E", dark="#9AA0A6")
        self.assertIn("theme.dark", self.save(site, draft, 400)["errors"])
        draft["theme"].update(palette="royal", primary="#FFFFFF", dark="#FFFFFF")
        self.assertEqual(self.save(site, draft)["draft"]["theme"]["primary"], "#7C3AED")

    def test_unsafe_or_wrong_content_is_refused(self):
        home = self.layout("home")
        cases = [
            ("javascript:alert(1)", "button_link"),
            ("https://evil.example/x.png", "image"),
        ]
        for value, key in cases:
            draft = copy.deepcopy(home["draft"])
            draft["sections"][0]["settings"]["slides"][0][key] = value
            response = self.save(home, draft, 400)
            self.assertIn(f"sections.0.settings.slides.0.{key}", response["errors"])
        draft = copy.deepcopy(home["draft"])
        draft["sections"].append({"type": "order_form", "settings": {}})
        self.assertIn("غير متاح", self.save(home, draft, 400)["detail"])
        draft = copy.deepcopy(home["draft"])
        draft["sections"].append({"type": "script", "settings": {}})
        self.save(home, draft, 400)
        draft = copy.deepcopy(home["draft"])
        draft["sections"][0]["settings"]["slides"] = draft["sections"][0]["settings"]["slides"] * 2
        self.assertIn("5", self.save(home, draft, 400)["detail"])
        draft = copy.deepcopy(home["draft"])
        draft["sections"].append({"type": "video", "settings": {"url": "https://vimeo.com/1"}})
        self.save(home, draft, 400)
        draft["sections"][-1]["settings"]["url"] = "https://youtu.be/dQw4w9WgXcQ"
        draft["sections"][-1]["anchor"] = "Bad Anchor"
        self.save(home, draft, 400)
        draft["sections"][-1]["anchor"] = "video"
        saved = self.save(home, draft)
        self.assertEqual(saved["draft"]["sections"][-1]["settings"]["title"], {"ar": "", "en": ""})

    def test_images_upload_and_library(self):
        upload = self.client.post("/api/store/design/images/", {"image": png_file()}, format="multipart")
        self.assertCode(upload, 201)
        self.assertTrue(upload.data["url"].startswith("/media/design/design-"))
        self.assertTrue(upload.data["url"].endswith(".webp"))
        self.assertEqual(self.store.get(upload.data["url"]).status_code, 200)
        library = self.client.get("/api/store/design/images/")
        self.assertCode(library, 200)
        self.assertEqual(library.data[0]["url"], upload.data["url"])
        self.assertTrue(any(row["group"] == "illustrations" for row in library.data))
        home = self.layout("home")
        draft = copy.deepcopy(home["draft"])
        draft["sections"][0]["settings"]["slides"][0]["image"] = upload.data["url"]
        self.save(home, draft)

    def test_staff_without_store_access_are_refused(self):
        self.as_user(self.agent)
        self.assertCode(self.client.get("/api/store/design/"), 403)
        self.assertCode(self.client.post("/api/store/design/token/"), 403)


class LandingTests(DesignTestCase):
    def test_landing_lifecycle_from_draft_to_orders_and_numbers(self):
        created = self.landing(slug="visa-offer")
        self.assertEqual(created["slug"], "visa-offer")
        self.assertEqual([section["type"] for section in created["draft"]["sections"]], ["product_hero", "trust", "order_form", "faq"])
        self.assertTrue(created["draft"]["page"]["hide_menu"])
        self.assertFalse(created["is_live"])
        self.assertCode(self.client.get("/api/shop/landing/visa-offer/"), 404)
        self.assertEqual(self.store.get("/l/visa-offer/").status_code, 404)
        token = self.design()["preview"]["token"]
        preview = self.client.get("/api/shop/landing/visa-offer/", {"preview": token})
        self.assertCode(preview, 200)
        self.assertTrue(preview.data["preview"])
        draft = copy.deepcopy(created["draft"])
        draft["sections"] = [section for section in draft["sections"] if section["type"] != "order_form"]
        self.save(created, draft)
        self.assertIn("استمارة الطلب", self.publish(created, 400)["detail"])
        draft = copy.deepcopy(created["draft"])
        draft["page"]["seo_title"] = {"ar": "عرض <خاص>", "en": ""}
        self.save(created, draft)
        live = self.publish(created)
        self.assertTrue(live["is_live"])
        page = self.client.get("/api/shop/landing/visa-offer/")
        self.assertCode(page, 200)
        self.assertEqual(page.data["product"]["slug"], "visa-card")
        self.assertEqual(page.data["lang"], "ar")
        html = self.store.get("/l/visa-offer/").content.decode()
        self.assertIn("<title>عرض &lt;خاص&gt;</title>", html)
        self.assertNotIn("hreflang", html)
        self.assertCode(self.client.post("/api/shop/landing/visa-offer/visit/"), 200)
        self.store.post("/api/shop/landing/visa-offer/visit/")
        self.place(visit={"landing": "visa-offer", "utm_source": "facebook"})
        self.place(phone="0661223344", visit={"landing": "no-such-page"})
        origin = OrderOrigin.objects.order_by("id")
        self.assertEqual(origin[0].landing.slug, "visa-offer")
        self.assertIsNone(origin[1].landing)
        stats = self.layout("visa-offer")["stats"]
        self.assertEqual((stats["views"], stats["orders"], stats["conversion"]), (1, 1, 100.0))
        off = self.client.patch(f"/api/store/design/layouts/{created['id']}/", {"is_live": False}, format="json")
        self.assertCode(off, 200)
        self.assertCode(self.client.get("/api/shop/landing/visa-offer/"), 404)
        copy_of = self.client.post(f"/api/store/design/layouts/{created['id']}/duplicate/")
        self.assertCode(copy_of, 201)
        self.assertEqual(copy_of.data["slug"], "visa-offer-copy")
        self.assertCode(self.client.delete(f"/api/store/design/layouts/{copy_of.data['id']}/"), 204)
        home = next(row for row in self.design()["layouts"] if row["slug"] == "home")
        self.assertCode(self.client.delete(f"/api/store/design/layouts/{home['id']}/"), 409)

    def test_templates_languages_and_settings(self):
        story = self.landing("story", "en")
        self.assertEqual(story["slug"], "visa-card")
        self.assertEqual(story["url"], "https://vizorawear.com/en/l/visa-card/")
        self.assertIn("order_form", [section["type"] for section in story["draft"]["sections"]])
        self.assertEqual(story["draft"]["sections"][-1]["settings"]["title"]["en"], "Order now, pay on delivery")
        blank = self.landing("blank")
        self.assertEqual(blank["slug"], "visa-card-2")
        self.assertEqual(len(blank["draft"]["sections"]), 2)
        bad = self.client.post("/api/store/design/layouts/", {"name": "x", "product": 999, "slug": "Bad Slug"}, format="json")
        self.assertCode(bad, 400)
        self.assertEqual(set(bad.data["errors"]), {"name", "product", "slug"})
        taken = self.client.post("/api/store/design/layouts/", {"name": "عرض", "product": self.card.pk, "slug": "home"}, format="json")
        self.assertCode(taken, 400)
        edited = self.client.patch(f"/api/store/design/layouts/{blank['id']}/", {"name": "عرض الباك", "slug": "pack-offer", "product": self.pack.pk, "lang": "en"}, format="json")
        self.assertCode(edited, 200)
        self.assertEqual((edited.data["slug"], edited.data["product"]["slug"], edited.data["lang"]), ("pack-offer", "tidy-pack", "en"))
        self.assertCode(self.client.patch(f"/api/store/design/layouts/{blank['id']}/", {"slug": "visa-card"}, format="json"), 400)
        self.assertCode(self.client.patch(f"/api/store/design/layouts/{blank['id']}/", {"is_live": True}, format="json"), 400)
        self.pack.is_published = False
        self.pack.save()
        self.assertIn("غير منشور", self.publish(edited.data, 400)["detail"])
        self.publish(story)
        self.assertEqual(self.store.get("/en/l/visa-card/").status_code, 200)
        self.assertEqual(Layout.objects.get(slug="visa-card").versions.count(), 1)

    def test_preview_token_can_be_changed(self):
        old = self.design()["preview"]["token"]
        response = self.client.post("/api/store/design/token/")
        self.assertCode(response, 200)
        self.assertNotEqual(response.data["token"], old)
        self.assertCode(self.client.get("/api/shop/design/preview/", {"token": old}), 403)
