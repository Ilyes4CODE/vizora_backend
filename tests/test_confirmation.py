from collections import Counter
from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import Role, User
from apps.catalog.models import Product
from apps.orders.models import Order

from .base import ApiTestCase


class ConfirmationTests(ApiTestCase):
    def outcome(self, order_id, outcome, **extra):
        return self.client.post(f"/api/confirmation/orders/{order_id}/outcome/", {"outcome": outcome, **extra})

    def queue(self, stage="confirm"):
        return self.client.get("/api/confirmation/queue/", {"stage": stage})

    def test_queue_and_counts(self):
        order = self.new_order()
        response = self.queue()
        self.assertCode(response, 200)
        self.assertEqual(response.data["results"][0]["id"], order["id"])
        self.assertIn("events", response.data["results"][0])
        self.assertEqual(self.client.get("/api/confirmation/counts/").data, {"confirm": 1, "suivi": 0})

    def test_bad_stage_rejected(self):
        self.assertCode(self.queue("x"), 400)

    def test_confirm(self):
        order = self.new_order()
        self.as_user(self.agent)
        response = self.outcome(order["id"], "confirmed", note="تم")
        self.assertCode(response, 200)
        self.assertEqual(response.data["status"], "confirmed")
        self.assertEqual(response.data["assigned_to"], self.agent.pk)
        self.assertEqual(response.data["call_attempts"], 1)
        self.assertEqual(response.data["events"][0]["outcome"], "confirmed")
        self.assertEqual(self.queue().data["count"], 0)

    def test_confirm_blocked_without_stock(self):
        order = self.new_order(items=[{"product": self.product.pk, "quantity": 101}])
        self.assertCode(self.outcome(order["id"], "confirmed"), 409, "insufficient_stock")
        self.assertEqual(Order.objects.get(pk=order["id"]).status, "pending")

    def test_reserved_stock_counts_against_next_confirmation(self):
        self.confirmed_order(items=[{"product": self.product.pk, "quantity": 60}])
        second = self.new_order(phone="0661002875", items=[{"product": self.product.pk, "quantity": 60}])
        self.assertCode(self.outcome(second["id"], "confirmed"), 409, "insufficient_stock")

    def test_no_answer_and_busy_retry_later(self):
        order = self.new_order()
        for outcome, attempts in (("no_answer", 1), ("busy", 2)):
            Order.objects.filter(pk=order["id"]).update(next_call_at=None)
            response = self.outcome(order["id"], outcome)
            self.assertCode(response, 200)
            self.assertEqual(response.data["status"], "pending")
            self.assertEqual(response.data["call_attempts"], attempts)
            self.assertEqual(self.queue().data["count"], 0)
        Order.objects.filter(pk=order["id"]).update(next_call_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.queue().data["count"], 1)

    def test_postponed_requires_date(self):
        order = self.new_order()
        self.assertCode(self.outcome(order["id"], "postponed"), 400)
        later = (timezone.now() + timedelta(days=1)).isoformat()
        self.assertCode(self.outcome(order["id"], "postponed", next_call_at=later), 200)
        self.assertEqual(self.queue().data["count"], 0)

    def test_closing_outcomes_cancel_the_order(self):
        for index, outcome in enumerate(("cancelled", "wrong_number", "duplicate")):
            order = self.new_order(phone=f"055500000{index}")
            response = self.outcome(order["id"], outcome)
            self.assertCode(response, 200)
            self.assertEqual((response.data["status"], response.data["cancel_reason"]), ("cancelled", outcome))

    def test_invalid_outcome_and_repeat(self):
        order = self.new_order()
        self.assertCode(self.outcome(order["id"], "maybe"), 400)
        self.assertCode(self.outcome(order["id"], "confirmed"), 200)
        self.assertCode(self.outcome(order["id"], "confirmed"), 409, "invalid_transition")

    def test_agent_sees_own_and_unassigned_only(self):
        mine = self.new_order(assigned_to=self.agent.pk)
        unassigned = self.new_order(phone="0661002875")
        other = self.new_order(phone="0770001690", assigned_to=self.admin.pk)
        self.as_user(self.agent)
        ids = {item["id"] for item in self.queue().data["results"]}
        self.assertEqual(ids, {mine["id"], unassigned["id"]})
        response = self.outcome(other["id"], "confirmed")
        self.assertCode(response, 409, "order_taken")
        self.assertEqual(response.data["detail"], "هذه الطلبية عند موظف آخر")

    def agents(self, count):
        return [self.make_user(f"agent_{index}", f"موظف {index}", Role.CONFIRMATION) for index in range(count)]

    def test_new_orders_are_shared_evenly_between_agents(self):
        for agent in self.agents(2):
            User.objects.filter(pk=agent.pk).update(last_activity_at=timezone.now())
        User.objects.filter(pk=self.agent.pk).update(last_activity_at=timezone.now())
        owners = [self.new_order(phone=f"055500000{index}")["assigned_to"] for index in range(9)]
        self.assertEqual(sorted(Counter(owners).values()), [3, 3, 3])

    def test_agents_never_see_the_same_new_customer(self):
        extra = self.agents(1)[0]
        for index in range(4):
            self.new_order(phone=f"055500000{index}")
        self.as_user(self.agent)
        first = {item["id"] for item in self.queue().data["results"]}
        self.as_user(extra)
        second = {item["id"] for item in self.queue().data["results"]}
        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 2)
        self.assertFalse(first & second)

    def test_inactive_agents_get_no_new_orders_when_others_are_online(self):
        away = self.agents(1)[0]
        User.objects.filter(pk=away.pk).update(last_activity_at=timezone.now() - timedelta(days=3))
        User.objects.filter(pk=self.agent.pk).update(last_activity_at=timezone.now())
        self.assertEqual(self.new_order()["assigned_to"], self.agent.pk)

    def test_explicit_assignment_is_respected(self):
        extra = self.agents(1)[0]
        self.assertEqual(self.new_order(assigned_to=extra.pk)["assigned_to"], extra.pk)

    def test_disabled_agent_orders_move_to_someone_else(self):
        extra = self.agents(1)[0]
        order = self.new_order(assigned_to=extra.pk)
        self.assertCode(self.client.delete(f"/api/users/{extra.pk}/"), 204)
        self.assertEqual(Order.objects.get(pk=order["id"]).assigned_to_id, self.agent.pk)

    def test_missing_order_message_is_arabic(self):
        response = self.outcome(999999, "confirmed")
        self.assertCode(response, 404, "not_found")
        self.assertEqual(response.data["detail"], "العنصر المطلوب غير موجود")

    def test_suivi_flow(self):
        order_id, tracking = self.shipped_order()
        self.assertEqual(self.queue("suivi").data["count"], 0)
        self.assertCode(self.webhook(tracking, "out_for_delivery"), 200)
        self.assertEqual(self.client.get("/api/confirmation/counts/").data["suivi"], 1)
        item = self.queue("suivi").data["results"][0]
        self.assertEqual(item["shipment"]["stage"], "out")
        response = self.outcome(order_id, "will_receive", stage="suivi")
        self.assertCode(response, 200)
        self.assertEqual(response.data["suivi_attempts"], 1)
        self.assertEqual(self.queue("suivi").data["count"], 0)

    def test_suivi_outcomes(self):
        order_id, tracking = self.shipped_order()
        self.webhook(tracking, "failed")
        self.assertCode(self.outcome(order_id, "postponed", stage="suivi"), 400)
        self.assertCode(self.outcome(order_id, "confirmed", stage="suivi"), 400)
        for outcome in ("no_answer", "refused", "wrong_address"):
            self.assertCode(self.outcome(order_id, outcome, stage="suivi"), 200)
        later = (timezone.now() + timedelta(days=1)).isoformat()
        self.assertCode(self.outcome(order_id, "postponed", stage="suivi", next_call_at=later), 200)

    def test_suivi_requires_shipping_status(self):
        order = self.new_order()
        self.assertCode(self.outcome(order["id"], "will_receive", stage="suivi"), 409, "invalid_transition")

    def test_stock_is_untouched_until_shipping(self):
        self.confirmed_order()
        self.assertEqual(Product.objects.get(pk=self.product.pk).on_hand, 100)
