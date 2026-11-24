from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import Role, User
from apps.core.models import AuditLog
from apps.orders.models import Order

from .base import PASSWORD, ApiTestCase


class TraceTests(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.agent_b = User.objects.create_user(username="agent_b", password=PASSWORD, full_name="ياسمين بلعيد", role=Role.CONFIRMATION)

    def confirm_as(self, user, phone, outcome="confirmed"):
        self.as_user(self.admin)
        order = self.new_order(phone=phone, assigned_to=user.pk)
        self.as_user(user)
        self.assertCode(self.client.post(f"/api/confirmation/orders/{order['id']}/outcome/", {"outcome": outcome}), 200)
        self.as_user(self.admin)
        return order["id"]

    def test_a_delivered_order_keeps_who_confirmed_it_and_who_sent_it(self):
        self.as_user(self.agent)
        order = self.new_order()
        self.client.post(f"/api/confirmation/orders/{order['id']}/outcome/", {"outcome": "confirmed"})
        self.as_user(self.admin)
        with self.settings():
            from unittest.mock import patch

            from .base import OfflineMaystro

            with patch("apps.shipping.maystro.LiveMaystroClient", OfflineMaystro):
                sent = self.client.post("/api/shipping/send/", {"order_ids": [order["id"]]}).data["sent"][0]
        self.assertCode(self.webhook(sent["tracking_number"], "delivered"), 200)
        detail = self.client.get(f"/api/orders/{order['id']}/").data
        self.assertEqual(detail["status"], "delivered")
        self.assertEqual((detail["confirmed_by"], detail["confirmed_by_name"], detail["shipped_by_name"], detail["created_by_name"]), (self.agent.pk, "سارة قاسمي", "أمين بوزيد", "سارة قاسمي"))
        row = self.client.get("/api/orders/", {"status": "delivered"}).data["results"][0]
        self.assertEqual((row["confirmed_by_name"], row["shipped_by_name"]), ("سارة قاسمي", "أمين بوزيد"))
        self.assertIsNotNone(row["delivered_at"])

    def test_orders_can_be_filtered_by_who_confirmed_them(self):
        mine = self.confirm_as(self.agent, "0555004112")
        theirs = self.confirm_as(self.agent_b, "0661002875")
        self.assertEqual([item["id"] for item in self.client.get("/api/orders/", {"confirmed_by": self.agent.pk}).data["results"]], [mine])
        self.assertEqual([item["id"] for item in self.client.get("/api/orders/", {"confirmed_by": self.agent_b.pk}).data["results"]], [theirs])
        today = timezone.localdate().isoformat()
        self.assertEqual(self.client.get("/api/orders/", {"confirmed_from": today, "confirmed_to": today}).data["count"], 2)
        self.assertEqual(self.client.get("/api/orders/", {"confirmed_to": (timezone.localdate() - timedelta(days=1)).isoformat()}).data["count"], 0)

    def test_each_confirmation_account_has_its_results(self):
        delivered_id, delivered_tracking = None, None
        self.as_user(self.agent)
        order_ids = []
        for phone in ("0555004101", "0555004102", "0555004103"):
            self.as_user(self.admin)
            order = self.new_order(phone=phone, assigned_to=self.agent.pk)
            self.as_user(self.agent)
            self.client.post(f"/api/confirmation/orders/{order['id']}/outcome/", {"outcome": "no_answer"})
            self.client.post(f"/api/confirmation/orders/{order['id']}/outcome/", {"outcome": "confirmed"})
            order_ids.append(order["id"])
        self.as_user(self.admin)
        from unittest.mock import patch

        from .base import OfflineMaystro

        with patch("apps.shipping.maystro.LiveMaystroClient", OfflineMaystro):
            sent = self.client.post("/api/shipping/send/", {"order_ids": order_ids[:2]}).data["sent"]
        self.webhook(sent[0]["tracking_number"], "delivered")
        self.webhook(sent[1]["tracking_number"], "returned")
        self.confirm_as(self.agent, "0555004104", outcome="cancelled")
        self.confirm_as(self.agent_b, "0661002875")

        response = self.client.get("/api/reports/agents/")
        self.assertCode(response, 200)
        agents = {item["user"]: item for item in response.data["agents"]}
        sara = agents[self.agent.pk]
        self.assertEqual((sara["calls"], sara["orders_called"], sara["confirmed"]), (7, 4, 3))
        self.assertEqual((sara["outcomes"]["no_answer"], sara["outcomes"]["confirmed"], sara["outcomes"]["cancelled"]), (3, 3, 1))
        self.assertEqual((sara["waiting"], sara["shipping"], sara["delivered"], sara["returned"]), (1, 0, 1, 1))
        self.assertEqual((sara["confirmation_rate"], sara["delivery_rate"], sara["delivered_sales"]), (75.0, 50.0, 3900))
        self.assertEqual(agents[self.agent_b.pk]["confirmed"], 1)
        self.assertEqual(agents[self.admin.pk]["sent"], 2)
        self.assertEqual(response.data["totals"]["confirmed"], 4)
        only = self.client.get("/api/reports/agents/", {"user": self.agent_b.pk}).data["agents"]
        self.assertEqual([item["user"] for item in only], [self.agent_b.pk])
        past = (timezone.localdate() - timedelta(days=40)).isoformat()
        old = self.client.get("/api/reports/agents/", {"date_from": past, "date_to": past}).data
        self.assertEqual(old["totals"]["confirmed"], 0)
        self.assertCode(self.client.get("/api/reports/agents/", {"user": "abc"}), 400)
        self.assertCode(self.client.get("/api/reports/agents/", {"date_from": "2026-05-02", "date_to": "2026-05-01"}), 400)

    def test_inactive_agents_with_no_work_are_hidden_but_their_past_work_is_kept(self):
        self.confirm_as(self.agent_b, "0661002875")
        User.objects.filter(pk=self.agent_b.pk).update(is_active=False)
        quiet = User.objects.create_user(username="quiet", password=PASSWORD, full_name="غائب", role=Role.CONFIRMATION, is_active=False)
        users = [item["user"] for item in self.client.get("/api/reports/agents/").data["agents"]]
        self.assertIn(self.agent_b.pk, users)
        self.assertNotIn(quiet.pk, users)
        self.assertIn(self.agent.pk, users)

    def test_the_operations_log_can_be_filtered(self):
        self.confirm_as(self.agent, "0555004112")
        self.client.post("/api/store/reviews/", {"name": "أمينة", "wilaya": "وهران", "text": "خدمة ممتازة", "rating": 5})
        AuditLog.objects.create(action="sheet_synced", message="زامن Google Sheet")
        everything = self.client.get("/api/audit-log/", {"page_size": 100}).data
        actions = [item["action"] for item in everything["results"]]
        self.assertIn("call_outcome", actions)
        self.assertIn("api_change", actions)
        self.assertEqual(actions.count("order_created"), 1)
        change = next(item for item in everything["results"] if item["action"] == "api_change")
        self.assertEqual((change["actor"], change["action_label"], change["group"]), ("أمين بوزيد", "تعديل", "other"))
        self.assertIn("الموقع", change["message"])
        by_agent = self.client.get("/api/audit-log/", {"user": self.agent.pk}).data["results"]
        self.assertEqual({item["action"] for item in by_agent}, {"call_outcome"})
        self.assertEqual(by_agent[0]["action_label"], "نتيجة اتصال")
        self.assertTrue(by_agent[0]["message"].startswith("سجّل «تأكيد» للطلبية VZ-"))
        system = self.client.get("/api/audit-log/", {"user": "system"}).data["results"]
        self.assertEqual([(item["actor"], item["group"]) for item in system], [("النظام", "orders")])
        confirmations = self.client.get("/api/audit-log/", {"group": "confirmation"}).data
        self.assertEqual(confirmations["count"], 1)
        today = timezone.localdate().isoformat()
        self.assertGreater(self.client.get("/api/audit-log/", {"date_from": today, "date_to": today}).data["count"], 2)
        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
        self.assertEqual(self.client.get("/api/audit-log/", {"date_to": yesterday}).data["count"], 0)
        self.assertEqual(self.client.get("/api/audit-log/", {"search": "Google"}).data["count"], 1)
        order = Order.objects.first()
        self.assertGreaterEqual(self.client.get("/api/audit-log/", {"target_type": "Order", "target_id": order.pk}).data["count"], 1)
        groups = self.client.get("/api/audit-log/groups/").data
        self.assertIn({"key": "confirmation", "label": "التأكيد والسويفي"}, groups)
        AuditLog.objects.create(action="call_outcome", message="سجّل no_answer للطلبية VZ-10001", user=self.agent, user_name="سارة قاسمي")
        self.assertEqual(self.client.get("/api/audit-log/", {"search": "VZ-10001"}).data["results"][0]["message"], "سجّل «لا يرد» للطلبية VZ-10001")

    def test_reading_and_logging_in_are_not_logged_as_changes(self):
        self.client.get("/api/orders/")
        self.client.post("/api/auth/login/", {"username": "agent_user", "password": PASSWORD})
        self.assertFalse(AuditLog.objects.filter(action="api_change").exists())

    def test_confirmation_staff_cannot_see_the_team_numbers_or_log(self):
        self.as_user(self.agent)
        self.assertCode(self.client.get("/api/reports/agents/"), 403)
        self.assertCode(self.client.get("/api/audit-log/"), 403)
        self.assertCode(self.client.get("/api/audit-log/groups/"), 403)
