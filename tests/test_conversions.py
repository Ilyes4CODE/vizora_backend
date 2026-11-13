import hashlib
from unittest.mock import patch

import requests
from django.test import override_settings

from apps.orders.models import Order
from apps.storefront import conversions
from apps.storefront.models import PixelEvent, StoreSettings

from .test_storefront import StoreTestCase, embedded_config

FB_PIXEL = "1234567890123456"
FB_TOKEN = "EAAB" + "x" * 60
TT_PIXEL = "CABC123XYZ4567"
TT_TOKEN = "tt" + "y" * 40
VISIT = {
    "utm_source": "facebook",
    "referrer": "https://m.facebook.com/",
    "page_url": "https://vizorawear.com/p/visa-card/?fbclid=abc",
    "fbp": "fb.1.1763000000000.123456789",
    "fbc": "fb.1.1763000000000.abc",
    "ttp": "ttp-cookie-value",
    "ttclid": "E.C.P.tiktok-click",
}


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body

    def json(self):
        return self.body


def accepted(url, **kwargs):
    if "tiktok" in url:
        return FakeResponse(200, {"code": 0, "message": "OK"})
    return FakeResponse(200, {"events_received": 1, "fbtrace_id": "trace"})


def calls_to(post, platform):
    return [call for call in post.call_args_list if (platform == "tiktok") == ("tiktok" in call.args[0])]


@override_settings(CONVERSIONS_SYNC=True, CONVERSIONS_META_URL="https://graph.example/v23.0", CONVERSIONS_TIKTOK_URL="https://tiktok.example/event/track/")
class ConversionTestCase(StoreTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch("apps.storefront.conversions.requests.post", side_effect=accepted)
        self.post = patcher.start()
        self.addCleanup(patcher.stop)

    def connect(self, moment="placed", facebook=True, tiktok=True, **extra):
        body = {"purchase_moment": moment}
        if facebook:
            body.update(facebook_pixel=FB_PIXEL, facebook_token=FB_TOKEN, facebook_test_code="TEST55021")
        if tiktok:
            body.update(tiktok_pixel=TT_PIXEL, tiktok_token=TT_TOKEN, tiktok_test_code="TEST7781")
        body.update(extra)
        self.assertCode(self.client.patch("/api/store/settings/", body, format="json"), 200)

    def place_tracked(self, **overrides):
        response = self.place(visit=VISIT, **overrides)
        self.assertIn(response.status_code, (200, 201), response.data)
        return response.data["reference"]

    def confirm(self, reference):
        order = Order.objects.get(reference=reference)
        self.assertCode(self.client.post(f"/api/confirmation/orders/{order.pk}/outcome/", {"outcome": "confirmed"}), 200)
        return order


class PixelSettingsTests(ConversionTestCase):
    def test_keys_are_encrypted_and_never_sent_back(self):
        response = self.client.patch(
            "/api/store/settings/",
            {"facebook_pixel": " 1234 5678 9012 3456 ", "facebook_token": FB_TOKEN, "tiktok_pixel": "cabc123xyz4567", "google_analytics": "g-abc123xyz"},
            format="json",
        )
        self.assertCode(response, 200)
        self.assertNotIn("facebook_token", response.data)
        self.assertNotIn("encrypted_facebook_token", response.data)
        self.assertTrue(response.data["facebook_token_set"])
        self.assertFalse(response.data["tiktok_token_set"])
        self.assertEqual((response.data["facebook_pixel"], response.data["tiktok_pixel"], response.data["google_analytics"]), (FB_PIXEL, TT_PIXEL, "G-ABC123XYZ"))
        store = StoreSettings.load()
        self.assertNotIn(FB_TOKEN, store.encrypted_facebook_token)
        self.assertEqual(store.facebook_token, FB_TOKEN)
        self.assertNotIn(FB_TOKEN, self.client.get("/api/store/settings/").content.decode())
        self.assertNotIn(FB_TOKEN, self.store.get("/").content.decode())
        self.assertCode(self.client.patch("/api/store/settings/", {"announcement_ar": "x"}, format="json"), 200)
        self.assertEqual(StoreSettings.load().facebook_token, FB_TOKEN)
        cleared = self.client.patch("/api/store/settings/", {"facebook_token": ""}, format="json")
        self.assertFalse(cleared.data["facebook_token_set"])

    def test_wrong_ids_are_refused_with_a_clear_message(self):
        for field, value in (
            ("facebook_pixel", "my-pixel"),
            ("tiktok_pixel", "bad id!"),
            ("google_analytics", "UA-1234-1"),
            ("facebook_token", "short"),
            ("tiktok_test_code", "has space!"),
            ("purchase_moment", "never"),
        ):
            response = self.client.patch("/api/store/settings/", {field: value}, format="json")
            self.assertCode(response, 400)
            self.assertIn(field, response.data["errors"])

    def test_browser_event_names_follow_the_purchase_moment(self):
        events = self.client.get("/api/shop/config/").data["store"]["events"]
        self.assertEqual(events, {"facebook": "Purchase", "tiktok": "CompletePayment", "google": "purchase"})
        self.connect("confirmed", tiktok=False)
        events = embedded_config(self.store.get("/").content.decode())["store"]["events"]
        self.assertEqual(events, {"facebook": "Lead", "tiktok": "CompletePayment", "google": "generate_lead"})


class ServerEventTests(ConversionTestCase):
    def test_order_sends_purchase_with_the_matching_data(self):
        self.connect()
        reference = self.place_tracked()
        self.assertEqual(self.post.call_count, 2)
        facebook = calls_to(self.post, "facebook")[0]
        self.assertEqual(facebook.args[0], f"https://graph.example/v23.0/{FB_PIXEL}/events")
        self.assertEqual(facebook.kwargs["params"], {"access_token": FB_TOKEN})
        self.assertNotIn("test_event_code", facebook.kwargs["json"])
        event = facebook.kwargs["json"]["data"][0]
        self.assertEqual((event["event_name"], event["event_id"], event["action_source"]), ("Purchase", reference, "website"))
        self.assertEqual(event["event_source_url"], VISIT["page_url"])
        user = event["user_data"]
        self.assertEqual(user["ph"], [sha("213555004112")])
        self.assertEqual(user["external_id"], [sha("213555004112")])
        self.assertEqual(user["country"], [sha("dz")])
        self.assertEqual(user["ct"], [sha("darelbeida")])
        self.assertEqual((user["fbp"], user["fbc"], user["client_user_agent"], user["client_ip_address"]), (VISIT["fbp"], VISIT["fbc"], "Phone", "127.0.0.1"))
        self.assertNotIn("0555004112", str(event))
        custom = event["custom_data"]
        self.assertEqual((custom["value"], custom["currency"], custom["content_ids"], custom["num_items"]), (11900, "DZD", ["visa-card"], 1))
        tiktok = calls_to(self.post, "tiktok")[0]
        self.assertEqual(tiktok.kwargs["headers"], {"Access-Token": TT_TOKEN})
        body = tiktok.kwargs["json"]
        self.assertEqual((body["event_source"], body["event_source_id"]), ("web", TT_PIXEL))
        row = body["data"][0]
        self.assertEqual((row["event"], row["event_id"]), ("CompletePayment", reference))
        self.assertEqual(row["user"]["phone"], sha("+213555004112"))
        self.assertEqual((row["user"]["ttclid"], row["user"]["ttp"]), (VISIT["ttclid"], VISIT["ttp"]))
        self.assertEqual(row["page"]["url"], VISIT["page_url"])
        self.assertEqual(row["properties"]["contents"][0]["content_id"], "visa-card")
        self.assertEqual(PixelEvent.objects.filter(status="sent", event_id=reference).count(), 2)
        self.assertEqual(self.place_tracked(), reference)
        self.assertEqual(self.post.call_count, 2)

    def test_confirmed_moment_sends_lead_first_then_purchase_on_confirmation(self):
        self.connect("confirmed", tiktok=False)
        reference = self.place_tracked()
        self.assertEqual([call.kwargs["json"]["data"][0]["event_name"] for call in self.post.call_args_list], ["Lead"])
        self.confirm(reference)
        names = [call.kwargs["json"]["data"][0]["event_name"] for call in self.post.call_args_list]
        self.assertEqual(names, ["Lead", "Purchase"])
        self.assertEqual(self.post.call_args_list[1].kwargs["json"]["data"][0]["event_id"], reference)
        manual = self.new_order(phone="0661998877")
        self.client.post(f"/api/confirmation/orders/{manual['id']}/outcome/", {"outcome": "confirmed"})
        self.assertEqual(self.post.call_count, 2)

    def test_delivered_moment_waits_for_the_parcel(self):
        self.connect("delivered", facebook=False)
        reference = self.place_tracked()
        order = self.confirm(reference)
        self.assertEqual([call.kwargs["json"]["data"][0]["event"] for call in self.post.call_args_list], ["PlaceAnOrder"])
        self.use_offline_maystro()
        sent = self.client.post("/api/shipping/send/", {"order_ids": [order.pk]})
        self.assertCode(sent, 200)
        self.assertCode(self.webhook(sent.data["sent"][0]["tracking_number"], "delivered"), 200)
        events = [call.kwargs["json"]["data"][0]["event"] for call in self.post.call_args_list]
        self.assertEqual(events, ["PlaceAnOrder", "CompletePayment"])

    def test_nothing_leaves_without_server_keys(self):
        self.client.patch("/api/store/settings/", {"facebook_pixel": FB_PIXEL, "purchase_moment": "confirmed"}, format="json")
        self.confirm(self.place_tracked())
        self.post.assert_not_called()
        self.assertEqual(self.client.get("/api/shop/config/").data["store"]["events"]["facebook"], "Purchase")

    @override_settings(CONVERSIONS_SYNC=False)
    def test_events_go_out_after_the_order_is_saved(self):
        self.connect(tiktok=False)

        class Inline:
            def __init__(self, target, daemon):
                self.target = target

            def start(self):
                self.target()

        with patch("apps.storefront.conversions.threading.Thread", Inline), patch("apps.storefront.conversions.connection"):
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                reference = self.place_tracked()
            self.post.assert_not_called()
            for callback in callbacks:
                callback()
        self.assertEqual(PixelEvent.objects.get().event_id, reference)


class FailureAndReportTests(ConversionTestCase):
    def test_failures_are_logged_and_can_be_sent_again(self):
        self.connect(tiktok=False)
        self.post.side_effect = requests.ConnectionError("down")
        reference = self.place_tracked()
        failed = PixelEvent.objects.get()
        self.assertEqual((failed.status, failed.event_id), ("failed", reference))
        self.assertIn("ConnectionError", failed.detail)
        self.post.side_effect = lambda url, **kwargs: FakeResponse(400, {"error": {"message": "Invalid OAuth access token"}})
        self.assertEqual(self.client.post("/api/store/conversions/retry/").data, {"sent": 0, "failed": 1})
        self.assertEqual(PixelEvent.objects.first().detail, "Invalid OAuth access token")
        self.assertEqual(self.client.get("/api/store/conversions/").data["retryable"], 1)
        self.post.side_effect = accepted
        retried = self.client.post("/api/store/conversions/retry/")
        self.assertCode(retried, 200)
        self.assertEqual(retried.data, {"sent": 1, "failed": 0})
        self.assertEqual(self.client.get("/api/store/conversions/").data["retryable"], 0)
        self.assertEqual(self.client.post("/api/store/conversions/retry/").data, {"sent": 0, "failed": 0})

    def test_test_button_needs_keys_and_a_test_code(self):
        self.assertCode(self.client.post("/api/store/conversions/test/", {"platform": "google"}), 400)
        self.assertCode(self.client.post("/api/store/conversions/test/", {"platform": "facebook"}), 400)
        self.connect(facebook_test_code="")
        self.assertCode(self.client.post("/api/store/conversions/test/", {"platform": "facebook"}), 400)
        self.post.assert_not_called()
        response = self.client.post("/api/store/conversions/test/", {"platform": "tiktok"}, HTTP_USER_AGENT="VizoraDesktop")
        self.assertCode(response, 200)
        self.assertEqual(response.data, {"ok": True, "detail": "استلم TikTok الحدث"})
        body = self.post.call_args.kwargs["json"]
        self.assertEqual(body["test_event_code"], "TEST7781")
        self.assertEqual(body["data"][0]["user"]["user_agent"], "VizoraDesktop")
        self.assertTrue(PixelEvent.objects.get().is_test)
        self.post.side_effect = lambda url, **kwargs: FakeResponse(200, {"code": 40001, "message": "Invalid access token"})
        refused = self.client.post("/api/store/conversions/test/", {"platform": "tiktok"})
        self.assertEqual(refused.data, {"ok": False, "detail": "Invalid access token"})

    def test_report_compares_orders_with_events(self):
        self.connect("confirmed")
        self.client.post("/api/store/conversions/test/", {"platform": "facebook"})
        first = self.place_tracked()
        self.place_tracked(phone="0661223344")
        self.confirm(first)
        report = self.client.get("/api/store/conversions/")
        self.assertCode(report, 200)
        data = report.data
        self.assertEqual(data["purchase_moment"], "confirmed")
        self.assertEqual((data["orders"]["placed"]["count"], data["orders"]["confirmed"]["count"]), (2, 1))
        self.assertEqual(data["orders"]["confirmed"]["value"], 11900)
        facebook = next(row for row in data["platforms"] if row["key"] == "facebook")
        self.assertEqual((facebook["browser"], facebook["server"], facebook["order_event"], facebook["purchase_moment"]), (True, True, "Lead", "confirmed"))
        self.assertEqual({row["name"]: row["sent"] for row in facebook["events"]}, {"Lead": 2, "Purchase": 1})
        self.assertEqual(facebook["expected"], {"count": 1, "value": 11900})
        self.assertIsNotNone(facebook["server_since"])
        google = next(row for row in data["platforms"] if row["key"] == "google")
        self.assertEqual((google["browser"], google["server"], google["expected"]), (False, False, None))
        self.assertEqual(len(data["recent"]), 7)
        self.assertTrue(data["recent"][-1]["is_test"])
        self.as_user(self.agent)
        self.assertCode(self.client.get("/api/store/conversions/"), 403)


    def test_orders_from_before_the_server_key_are_not_expected(self):
        earlier = self.place_tracked()
        self.confirm(earlier)
        self.connect("confirmed", tiktok=False)
        later = self.place_tracked(phone="0661223344")
        self.confirm(later)
        facebook = next(row for row in self.client.get("/api/store/conversions/").data["platforms"] if row["key"] == "facebook")
        self.assertEqual(facebook["expected"]["count"], 1)
        self.assertEqual({row["name"]: row["sent"] for row in facebook["events"]}, {"Lead": 1, "Purchase": 1})
        since = StoreSettings.load().facebook_server_since
        self.connect("confirmed", tiktok=False, facebook_token=FB_TOKEN + "z")
        self.assertEqual(StoreSettings.load().facebook_server_since, since)
        self.client.patch("/api/store/settings/", {"facebook_token": ""}, format="json")
        self.assertIsNone(StoreSettings.load().facebook_server_since)


class HelperTests(ConversionTestCase):
    def test_phone_and_text_normalising(self):
        self.assertEqual(conversions.international("0555 00 41 12"), "213555004112")
        self.assertEqual(conversions.latin("Bordj Bou Arréridj"), "bordjbouarreridj")
        self.assertEqual(conversions.name_part("Karim-Meziane"), "karimmeziane")
