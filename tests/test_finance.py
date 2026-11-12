from io import BytesIO

from django.utils import timezone
from openpyxl import load_workbook

from apps.finance.models import Campaign, Expense, Settlement
from apps.shipping.models import Shipment

from .base import ApiTestCase, excel_file


class ExpenseTests(ApiTestCase):
    def payload(self, **extra):
        return {"date": str(timezone.localdate()), "title": "رواتب", "category": "salaries", "amount": 1000, **extra}

    def test_crud(self):
        created = self.client.post("/api/expenses/", self.payload())
        self.assertCode(created, 201)
        expense_id = created.data["id"]
        self.assertEqual(created.data["created_by_name"], "أمين بوزيد")
        self.assertEqual(self.client.get("/api/expenses/").data["count"], 1)
        self.assertEqual(self.client.get(f"/api/expenses/{expense_id}/").data["category_label"], "الرواتب")
        self.assertEqual(self.client.patch(f"/api/expenses/{expense_id}/", {"amount": 1500}).data["amount"], 1500)
        self.assertEqual(self.client.put(f"/api/expenses/{expense_id}/", self.payload(amount=1700)).data["amount"], 1700)
        self.assertCode(self.client.delete(f"/api/expenses/{expense_id}/"), 204)

    def test_filters_and_validation(self):
        self.client.post("/api/expenses/", self.payload())
        self.assertEqual(self.client.get("/api/expenses/", {"category": "ads"}).data["count"], 0)
        self.assertEqual(self.client.get("/api/expenses/", {"month": "2020-01"}).data["count"], 0)
        self.assertEqual(self.client.get("/api/expenses/", {"search": "رواتب"}).data["count"], 1)
        self.assertCode(self.client.post("/api/expenses/", self.payload(amount=0)), 400)
        self.assertCode(self.client.post("/api/expenses/", self.payload(category="x")), 400)
        self.assertCode(self.client.get("/api/expenses/", {"month": "bad"}), 400)


class AccountingTests(ApiTestCase):
    def test_profit_calculation(self):
        self.delivered_order()
        self.delivered_order(phone="0661002875", items=[{"product": self.product_b.pk, "quantity": 2}])
        self.returned_order(phone="0770001690")
        self.confirmed_order(phone="0550000001")
        today = str(timezone.localdate())
        for category, amount in (("ads", 1000), ("salaries", 2000), ("packaging", 300), ("other", 400), ("goods", 999999)):
            Expense.objects.create(date=today, title="x", category=category, amount=amount)
        response = self.client.get("/api/accounting/summary/")
        self.assertCode(response, 200)
        self.assertEqual(response.data["sales"], 14900)
        self.assertEqual(response.data["delivered_parcels"], 2)
        lines = {line["key"]: line["amount"] for line in response.data["breakdown"]}
        self.assertEqual(lines, {"goods_cost": 5600, "ads": 1000, "salaries": 2000, "packaging": 300, "return_fees": 350, "other": 400})
        self.assertEqual(response.data["expenses_total"], 9650)
        self.assertEqual(response.data["net_profit"], 5250)
        self.assertEqual(response.data["margin"], 35.2)

    def test_empty_month_has_no_division_error(self):
        response = self.client.get("/api/accounting/summary/", {"month": "2020-01"})
        self.assertCode(response, 200)
        self.assertEqual((response.data["sales"], response.data["margin"]), (0, 0))

    def test_invalid_month(self):
        self.assertCode(self.client.get("/api/accounting/summary/", {"month": "13-2026"}), 400, "validation_error")

    def test_export(self):
        self.delivered_order()
        response = self.client.get("/api/accounting/export/")
        self.assertCode(response, 200)
        rows = {row[0]: row[1] for row in load_workbook(BytesIO(response.content)).active.iter_rows(values_only=True)}
        self.assertEqual(rows["المبيعات المسلّمة"], 3900)
        self.assertEqual(rows["صافي الربح"], 2500)


class CampaignTests(ApiTestCase):
    def create(self, name="Facebook A", platform="facebook"):
        response = self.client.post("/api/campaigns/", {"name": name, "platform": platform, "product": self.product.pk})
        self.assertCode(response, 201)
        return response.data

    def test_crud(self):
        campaign = self.create()
        self.assertEqual(len(self.client.get("/api/campaigns/").data), 1)
        self.assertEqual(self.client.get(f"/api/campaigns/{campaign['id']}/").data["platform_label"], "Facebook")
        self.assertEqual(self.client.patch(f"/api/campaigns/{campaign['id']}/", {"is_active": False}).data["is_active"], False)
        self.assertCode(self.client.put(f"/api/campaigns/{campaign['id']}/", {"name": "B", "platform": "tiktok"}), 200)
        self.assertCode(self.client.delete(f"/api/campaigns/{campaign['id']}/"), 204)
        self.assertEqual(Campaign.objects.count(), 0)

    def test_campaign_with_history_is_deactivated(self):
        campaign = self.create()
        self.new_order(campaign=campaign["id"])
        self.assertCode(self.client.delete(f"/api/campaigns/{campaign['id']}/"), 204)
        self.assertFalse(Campaign.objects.get(pk=campaign["id"]).is_active)

    def test_spend_creates_ads_expense(self):
        campaign = self.create()
        url = f"/api/campaigns/{campaign['id']}/spend/"
        response = self.client.post(url, {"date": str(timezone.localdate()), "amount": 5000})
        self.assertCode(response, 201)
        self.assertEqual(response.data["category"], "ads")
        self.assertCode(self.client.post(url, {"date": str(timezone.localdate()), "amount": 0}), 400)

    def test_performance_figures(self):
        first = self.create()
        second = self.create("Instagram B", "instagram")
        today = str(timezone.localdate())
        self.client.post(f"/api/campaigns/{first['id']}/spend/", {"date": today, "amount": 1000})
        self.client.post(f"/api/campaigns/{second['id']}/spend/", {"date": today, "amount": 3000})
        self.delivered_order(campaign=first["id"])
        self.new_order(campaign=first["id"], phone="0661002875")
        self.delivered_order(campaign=second["id"], phone="0770001690")
        response = self.client.get("/api/campaigns/performance/")
        self.assertCode(response, 200)
        self.assertEqual(response.data["totals"]["spend"], 4000)
        self.assertEqual(response.data["totals"]["cost_per_order"], 1333)
        self.assertEqual(response.data["totals"]["cost_per_delivered"], 2000)
        row = response.data["campaigns"][0]
        self.assertEqual((row["orders"], row["confirmed"], row["delivered"], row["cost_per_order"], row["cost_per_delivered"]), (2, 1, 1, 500, 1000))
        self.assertEqual([item["id"] for item in response.data["ranking"]], [first["id"], second["id"]])
        filtered = self.client.get("/api/campaigns/performance/", {"platform": "instagram"})
        self.assertEqual(filtered.data["totals"]["campaigns"], 1)

    def test_order_staff_can_read_campaign_list_only(self):
        self.as_user(self.agent)
        self.assertCode(self.client.get("/api/campaigns/"), 200)
        self.assertCode(self.client.get("/api/campaigns/performance/"), 403)
        self.assertCode(self.client.post("/api/campaigns/", {"name": "x", "platform": "facebook"}), 403)


class SettlementTests(ApiTestCase):
    def create(self):
        today = str(timezone.localdate())
        response = self.client.post("/api/settlements/", {"period_start": today, "period_end": today})
        self.assertCode(response, 201)
        return response.data

    def test_create_computes_totals(self):
        self.delivered_order()
        self.delivered_order(phone="0661002875", items=[{"product": self.product_b.pk, "quantity": 1}])
        self.returned_order(phone="0770001690")
        settlement = self.create()
        self.assertEqual(settlement["delivered_parcels"], 2)
        self.assertEqual(settlement["returned_parcels"], 1)
        self.assertEqual((settlement["gross"], settlement["return_fees"], settlement["net"]), (9400, 350, 9050))
        self.assertIsNone(settlement["difference"])
        self.assertEqual(settlement["status"], "open")

    def test_list_retrieve(self):
        self.delivered_order()
        settlement = self.create()
        self.assertEqual(len(self.client.get("/api/settlements/").data), 1)
        self.assertEqual(self.client.get("/api/settlements/", {"status": "paid"}).data, [])
        self.assertEqual(self.client.get(f"/api/settlements/{settlement['id']}/").data["net"], 3900)

    def test_validation(self):
        self.assertCode(self.client.post("/api/settlements/", {"period_start": "2026-10-10", "period_end": "2026-10-01"}), 400)
        self.assertCode(self.client.post("/api/settlements/", {"period_start": "2026-10-01", "period_end": "2026-10-10"}), 400, "no_parcels")

    def test_overlap_rejected(self):
        self.delivered_order()
        self.create()
        today = str(timezone.localdate())
        response = self.client.post("/api/settlements/", {"period_start": today, "period_end": today})
        self.assertCode(response, 409, "settlement_overlap")

    def test_mark_paid_once(self):
        self.delivered_order()
        settlement = self.create()
        url = f"/api/settlements/{settlement['id']}/mark-paid/"
        response = self.client.post(url, {"paid_on": "2026-11-05"})
        self.assertCode(response, 200)
        self.assertEqual((response.data["status"], response.data["paid_on"]), ("paid", "2026-11-05"))
        self.assertCode(self.client.post(url, {}), 409, "settlement_paid")
        self.assertCode(self.client.delete(f"/api/settlements/{settlement['id']}/"), 409, "settlement_paid")

    def test_delete_open_releases_parcels(self):
        _, tracking = self.delivered_order()
        settlement = self.create()
        self.assertCode(self.client.delete(f"/api/settlements/{settlement['id']}/"), 204)
        self.assertIsNone(Shipment.objects.get(tracking_number=tracking).settlement_id)
        self.assertEqual(Settlement.objects.count(), 0)

    def upload(self, settlement_id, rows):
        return self.client.post(f"/api/settlements/{settlement_id}/statement/", {"file": excel_file(rows)}, format="multipart")

    def test_statement_reconciliation(self):
        _, kept = self.delivered_order()
        _, missing = self.delivered_order(phone="0661002875")
        _, wrong = self.delivered_order(phone="0770001690")
        settlement = self.create()
        response = self.upload(settlement["id"], [["رقم التتبع", "المبلغ"], [kept, 3900], [wrong, 3000], ["GHOST", 100]])
        self.assertCode(response, 200)
        self.assertEqual(response.data["net"], 11700)
        self.assertEqual(response.data["statement_total"], 7000)
        self.assertEqual(response.data["difference"], -4700)
        self.assertEqual(response.data["missing_in_statement"], [missing])
        self.assertEqual(response.data["unknown_in_system"], ["GHOST"])
        self.assertEqual(response.data["amount_mismatches"], [{"tracking_number": wrong, "expected": 3900, "statement": 3000}])

    def test_statement_reimport_replaces_lines(self):
        _, tracking = self.delivered_order()
        settlement = self.create()
        self.upload(settlement["id"], [["tracking", "montant"], [tracking, 100]])
        response = self.upload(settlement["id"], [["tracking", "montant"], [tracking, 3900]])
        self.assertEqual(response.data["difference"], 0)

    def test_bad_statement(self):
        _, tracking = self.delivered_order()
        settlement = self.create()
        self.assertCode(self.upload(settlement["id"], [["a", "b"], [1, 2]]), 400, "statement_columns_missing")
        self.assertCode(self.upload(settlement["id"], [["tracking", "amount"], [tracking, "abc"]]), 400, "statement_bad_amount")

    def test_summary(self):
        self.delivered_order()
        settlement = self.create()
        self.delivered_order(phone="0661002875")
        response = self.client.get("/api/settlements/summary/")
        self.assertCode(response, 200)
        self.assertEqual((response.data["net"], response.data["paid"], response.data["remaining"]), (3900, 0, 3900))
        self.assertEqual(response.data["not_yet_in_settlement"], 3900)
        self.client.post(f"/api/settlements/{settlement['id']}/mark-paid/", {})
        response = self.client.get("/api/settlements/summary/")
        self.assertEqual((response.data["paid"], response.data["remaining"], response.data["paid_share"]), (3900, 0, 100.0))
