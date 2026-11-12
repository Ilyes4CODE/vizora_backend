from io import BytesIO, StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone
from openpyxl import load_workbook
from reportlab.pdfbase.ttfonts import TTFontFile

from apps.orders.models import Order
from apps.reports.exports import FONT_DIRECTORY, shape

from .base import ApiTestCase


class DashboardTests(ApiTestCase):
    def build_activity(self):
        self.delivered_order()
        self.returned_order(phone="0661002875")
        self.confirmed_order(phone="0770001690")
        self.new_order(phone="0550000001")
        self.new_order(phone="0550000002")

    def test_admin_dashboard(self):
        self.build_activity()
        response = self.client.get("/api/dashboard/")
        self.assertCode(response, 200)
        data = response.data
        self.assertEqual(data["today"]["pending_confirmation"], 2)
        self.assertEqual(data["today"]["confirmed_today"], 3)
        kpis = data["kpis"]
        self.assertEqual((kpis["orders"], kpis["confirmed"], kpis["delivered"], kpis["returned"]), (5, 3, 1, 1))
        self.assertEqual(kpis["confirmation_rate"], 60.0)
        self.assertEqual(kpis["delivery_rate"], 33.3)
        self.assertIn("net_profit", kpis)
        self.assertEqual(data["funnel"][0]["value"], 5)
        self.assertEqual(data["agents_today"][0]["confirmed"], 3)
        self.assertIn("unpaid_dues", data)
        self.assertEqual(sum(day["orders"] for day in data["daily_orders"]), 5)

    def test_agent_does_not_see_money(self):
        self.build_activity()
        self.as_user(self.agent)
        response = self.client.get("/api/dashboard/")
        self.assertCode(response, 200)
        self.assertNotIn("net_profit", response.data["kpis"])
        self.assertNotIn("unpaid_dues", response.data)

    def test_low_stock_alert(self):
        self.client.post(f"/api/products/{self.product.pk}/adjust/", {"quantity": -95, "note": "جرد"})
        alerts = self.client.get("/api/dashboard/").data["today"]["low_stock"]
        self.assertEqual([(item["id"], item["available"], item["state"]) for item in alerts], [(self.product.pk, 5, "low")])

    def test_unpaid_dues_on_dashboard(self):
        self.delivered_order()
        today = str(timezone.localdate())
        self.client.post("/api/settlements/", {"period_start": today, "period_end": today})
        dues = self.client.get("/api/dashboard/").data["unpaid_dues"]
        self.assertEqual((dues["amount"], dues["settlements"], dues["latest"]["delivered_parcels"]), (3900, 1, 1))

    def test_bad_month(self):
        self.assertCode(self.client.get("/api/dashboard/", {"month": "x"}), 400)


class ReportTests(ApiTestCase):
    def test_monthly(self):
        self.delivered_order()
        self.returned_order(phone="0661002875", wilaya=31)
        self.delivered_order(phone="0770001690", items=[{"product": self.product_b.pk, "quantity": 2}])
        response = self.client.get("/api/reports/monthly/")
        self.assertCode(response, 200)
        self.assertEqual(len(response.data["net_profit_by_month"]), 6)
        self.assertEqual(response.data["net_profit_by_month"][-1]["month"], response.data["month"])
        self.assertEqual(response.data["product_split"]["total"], 3)
        self.assertEqual(response.data["product_split"]["products"][0]["quantity"], 2)
        wilayas = {row["wilaya"]: row for row in response.data["wilayas"]}
        self.assertEqual(wilayas["الجزائر"]["success_rate"], 100.0)
        self.assertEqual(wilayas["وهران"]["success_rate"], 0.0)

    def test_top_wilayas_grouped(self):
        for index, code in enumerate((1, 2, 3, 4, 5, 6, 7)):
            self.delivered_order(phone=f"055500000{index}", wilaya=code)
        wilayas = self.client.get("/api/reports/monthly/").data["wilayas"]
        self.assertEqual(len(wilayas), 6)
        self.assertEqual((wilayas[-1]["wilaya"], wilayas[-1]["delivered"]), ("ولايات أخرى", 2))

    def test_daily_month_and_range(self):
        self.delivered_order()
        today = timezone.localdate()
        response = self.client.get("/api/reports/daily/")
        self.assertCode(response, 200)
        row = next(item for item in response.data["rows"] if item["date"] == today)
        self.assertEqual((row["orders"], row["confirmed"], row["confirmation_rate"], row["delivered"], row["sales"]), (1, 1, 100.0, 1, 3900))
        ranged = self.client.get("/api/reports/daily/", {"date_from": str(today), "date_to": str(today)})
        self.assertEqual(len(ranged.data["rows"]), 1)

    def test_daily_range_validation(self):
        self.assertCode(self.client.get("/api/reports/daily/", {"date_from": "2026-10-10", "date_to": "2026-10-01"}), 400)
        self.assertCode(self.client.get("/api/reports/daily/", {"date_from": "2026-10-10"}), 400)
        self.assertCode(self.client.get("/api/reports/daily/", {"date_from": "2020-01-01", "date_to": "2026-10-01"}), 400)

    def test_excel_exports(self):
        self.delivered_order()
        for kind, first in (("monthly", "الطلبيات الواردة"), ("daily", None)):
            response = self.client.get("/api/reports/export/", {"kind": kind, "file_type": "xlsx"})
            self.assertCode(response, 200)
            rows = list(load_workbook(BytesIO(response.content)).active.iter_rows(values_only=True))
            self.assertGreater(len(rows), 1)
            if first:
                self.assertEqual(rows[1][0], first)

    def test_pdf_exports(self):
        self.delivered_order()
        for kind in ("monthly", "daily"):
            response = self.client.get("/api/reports/export/", {"kind": kind, "file_type": "pdf"})
            self.assertCode(response, 200)
            self.assertEqual(response["Content-Type"], "application/pdf")
            self.assertTrue(response.content.startswith(b"%PDF"))
            self.assertGreater(len(response.content), 5000)

    def test_arabic_is_shaped_for_pdf(self):
        self.assertNotEqual(shape("المبيعات"), "المبيعات")

    def test_pdf_fonts_contain_every_arabic_letter_form(self):
        letters = "ابتثجحخدذرزسشصضطظعغفقكلمنهويءآأؤإئىة"
        samples = [form for letter in letters for form in (letter, f"ب{letter}ب", f"{letter}ب", f"ب{letter}")]
        samples += ["لا", "لأ", "لإ", "لآ", "1,234.5%"]
        needed = {char for sample in samples for char in shape(sample)}
        for file_name in ("IBMPlexSansArabic-Regular.ttf", "IBMPlexSansArabic-Bold.ttf"):
            font = TTFontFile(str(FONT_DIRECTORY / file_name))
            self.assertEqual({char for char in needed if ord(char) not in font.charToGlyph}, set(), file_name)

    def test_export_validation(self):
        self.assertCode(self.client.get("/api/reports/export/", {"kind": "x"}), 400)
        self.assertCode(self.client.get("/api/reports/export/", {"file_type": "doc"}), 400)


class SchemaTests(ApiTestCase):
    def test_openapi_schema_and_docs(self):
        schema = self.client.get("/api/schema/")
        self.assertCode(schema, 200)
        self.assertIn(b"/api/orders/", schema.content)
        self.assertCode(self.client.get("/api/docs/"), 200)


class DemoDataTests(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = cls.make_user("reviewer", "مراجع", "admin")

    def setUp(self):
        self.as_user(self.admin)

    def test_refuses_outside_debug(self):
        with override_settings(DEBUG=False), self.assertRaises(CommandError):
            call_command("seed_demo", password="Vizora-Strong-2026!")

    @override_settings(DEBUG=True)
    def test_demo_matches_design_numbers(self):
        call_command("seed_demo", password="Vizora-Strong-2026!", stdout=StringIO())
        dashboard = self.client.get("/api/dashboard/", {"month": "2026-10"}).data
        kpis = dashboard["kpis"]
        self.assertEqual(
            (kpis["orders"], kpis["confirmed"], kpis["confirmation_rate"], kpis["delivered"], kpis["returned"], kpis["delivery_rate"]),
            (1842, 1326, 72.0, 1061, 198, 80.0),
        )
        self.assertEqual((kpis["net_profit"], kpis["margin"]), (1893740, 40.7))
        self.assertEqual([day["orders"] for day in dashboard["daily_orders"]][:3], [52, 48, 61])
        today = dashboard["today"]
        self.assertEqual(
            (today["pending_confirmation"], today["suivi_due"], today["stuck_parcels"], today["in_shipping"], today["confirmed_today"]),
            (31, 12, 9, 143, 52),
        )
        self.assertEqual([agent["confirmed"] for agent in dashboard["agents_today"]], [21, 18, 13])
        accounting = self.client.get("/api/accounting/summary/", {"month": "2026-10"}).data
        self.assertEqual((accounting["sales"], accounting["expenses_total"]), (4648300, 2754560))
        lines = {line["key"]: line["amount"] for line in accounting["breakdown"]}
        self.assertEqual(lines["goods_cost"], 1708700)
        self.assertEqual(lines["return_fees"], 69300)
        performance = self.client.get("/api/campaigns/performance/", {"month": "2026-10"}).data
        self.assertEqual((performance["totals"]["spend"], performance["totals"]["cost_per_order"], performance["totals"]["cost_per_delivered"]), (612000, 332, 577))
        dues = self.client.get("/api/settlements/summary/", {"month": "2026-10"}).data
        self.assertEqual(dues["net"], 4579000)
        products = {item["name"]: item for item in self.client.get("/api/products/").data}
        self.assertEqual((products["المنتج الأول"]["available"], products["المنتج الأول"]["reserved"], products["المنتج الأول"]["at_courier"]), (412, 56, 98))
        self.assertEqual((products["المنتج الثاني"]["available"], products["المنتج الثاني"]["state"], products["المنتج الثاني"]["runway_days"]), (46, "low", 3))
        monthly = self.client.get("/api/reports/monthly/", {"month": "2026-10"}).data
        self.assertEqual({row["wilaya"]: row["delivered"] for row in monthly["wilayas"]}["الجزائر"], 214)
        latest = self.client.get("/api/settlements/", {"status": "open"}).data[0]
        self.assertEqual(latest["difference"], -3900)
        self.assertEqual(Order.objects.filter(status="pending").count(), 31)
