import shutil
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import override_settings

from apps.catalog.models import Product
from apps.core.models import AuditLog, Backup, BusinessProfile, Wilaya
from apps.orders.models import Order

from .base import ApiTestCase


class SystemTests(ApiTestCase):
    def test_health_is_public(self):
        self.anonymous()
        response = self.client.get("/api/health/")
        self.assertCode(response, 200)
        self.assertEqual(response.data["status"], "ok")

    def test_error_format_is_uniform(self):
        response = self.client.get("/api/products/999999/")
        self.assertCode(response, 404, "not_found")
        self.assertIn("detail", response.data)

    def test_business_profile_holds_real_details(self):
        self.as_user(self.agent)
        response = self.client.get("/api/settings/business/")
        self.assertCode(response, 200)
        self.assertEqual(response.data["phone"], "+213 667 27 76 76")
        self.assertEqual(response.data["instagram"], "@vizora_global")
        self.assertEqual(response.data["working_hours"]["sunday"], [["08:30", "14:00"], ["16:30", "20:30"]])

    def test_business_profile_update(self):
        data = dict(self.client.get("/api/settings/business/").data)
        data["facebook"] = "Vizora Global"
        self.assertCode(self.client.put("/api/settings/business/", data), 200)
        self.assertEqual(BusinessProfile.load().facebook, "Vizora Global")
        self.assertTrue(AuditLog.objects.filter(action="business_profile_updated").exists())

    def test_business_profile_rejects_bad_hours(self):
        data = dict(self.client.get("/api/settings/business/").data)
        data["working_hours"] = {"someday": []}
        self.assertCode(self.client.put("/api/settings/business/", data), 400)

    def test_agent_cannot_edit_business_profile(self):
        data = dict(self.client.get("/api/settings/business/").data)
        self.as_user(self.agent)
        self.assertCode(self.client.put("/api/settings/business/", data), 403)

    def test_security_settings(self):
        response = self.client.get("/api/settings/security/")
        self.assertCode(response, 200)
        self.assertTrue(response.data["auto_logout_enabled"])
        update = {**response.data, "auto_logout_minutes": 45, "two_factor_required": False}
        self.assertEqual(self.client.put("/api/settings/security/", update).data["auto_logout_minutes"], 45)
        self.assertCode(self.client.put("/api/settings/security/", {**update, "auto_logout_minutes": 1}), 400)

    def test_audit_log(self):
        self.client.post("/api/orders/", self.order_payload())
        response = self.client.get("/api/audit-log/", {"action": "order_created"})
        self.assertCode(response, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["user_name"], "أمين بوزيد")


class WilayaTests(ApiTestCase):
    def test_all_58_wilayas_available_to_staff(self):
        self.as_user(self.agent)
        response = self.client.get("/api/wilayas/")
        self.assertCode(response, 200)
        self.assertEqual(len(response.data), 58)
        self.assertEqual(response.data[15]["name_ar"], "الجزائر")

    def test_retrieve_and_update_fees(self):
        self.assertEqual(self.client.get("/api/wilayas/31/").data["name_fr"], "Oran")
        self.assertEqual(self.client.patch("/api/wilayas/31/", {"home_fee": 750}).data["home_fee"], 750)
        self.assertEqual(Wilaya.objects.get(pk=31).home_fee, 750)

    def test_agent_needs_shipping_section_to_edit(self):
        self.as_user(self.accountant)
        self.assertCode(self.client.patch("/api/wilayas/31/", {"home_fee": 1}), 403)

    def test_communes(self):
        url = "/api/wilayas/16/communes/"
        self.assertEqual(self.client.get(url).data, [])
        self.assertCode(self.client.post(url, {"name_ar": "الدار البيضاء"}), 201)
        self.assertCode(self.client.post(url, {"name_ar": "الدار البيضاء"}), 409, "commune_exists")
        self.assertEqual(len(self.client.get(url).data), 1)


class BackupTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        override = override_settings(BACKUP_DIR=self.folder, BACKUP_RETENTION=2)
        override.enable()
        self.addCleanup(override.disable)

    def create(self):
        response = self.client.post("/api/backups/")
        self.assertCode(response, 201)
        return response.data

    def test_create_list_retrieve_delete(self):
        backup = self.create()
        self.assertEqual(backup["kind"], "manual")
        self.assertEqual(backup["created_by_name"], "أمين بوزيد")
        self.assertEqual(len(self.client.get("/api/backups/").data), 1)
        self.assertEqual(self.client.get(f"/api/backups/{backup['id']}/").data["file_name"], backup["file_name"])
        self.assertCode(self.client.delete(f"/api/backups/{backup['id']}/"), 204)
        self.assertFalse((Path(self.folder) / backup["file_name"]).exists())

    def test_file_is_encrypted(self):
        self.new_order()
        backup = self.create()
        content = (Path(self.folder) / backup["file_name"]).read_bytes()
        self.assertNotIn(b"pbkdf2", content)
        self.assertNotIn("كريم".encode("utf-8"), content)

    def test_download(self):
        backup = self.create()
        response = self.client.get(f"/api/backups/{backup['id']}/download/")
        self.assertCode(response, 200)
        self.assertEqual(b"".join(response.streaming_content), (Path(self.folder) / backup["file_name"]).read_bytes())

    def test_download_missing_file(self):
        backup = self.create()
        (Path(self.folder) / backup["file_name"]).unlink()
        self.assertCode(self.client.get(f"/api/backups/{backup['id']}/download/"), 404, "backup_missing")

    def test_restore_returns_data_to_backup_state(self):
        order = self.new_order()
        backup = self.create()
        self.new_order(phone="0661002875")
        Product.objects.filter(pk=self.product.pk).update(price=9999)
        self.assertEqual(Order.objects.count(), 2)
        response = self.client.post(f"/api/backups/{backup['id']}/restore/", {"confirm": "RESTORE"})
        self.assertCode(response, 200)
        self.assertEqual(response.data["safety_backup"]["kind"], "pre_restore")
        self.assertEqual(list(Order.objects.values_list("pk", flat=True)), [order["id"]])
        self.assertEqual(Product.objects.get(pk=self.product.pk).price, 3900)
        self.assertTrue(Backup.objects.filter(pk=backup["id"]).exists())
        self.assertCode(self.client.get("/api/auth/me/"), 200)

    def test_restore_requires_confirmation(self):
        backup = self.create()
        self.assertCode(self.client.post(f"/api/backups/{backup['id']}/restore/", {}), 400)

    def test_corrupt_backup_does_not_touch_data(self):
        self.new_order()
        backup = self.create()
        (Path(self.folder) / backup["file_name"]).write_bytes(b"garbage")
        self.assertCode(self.client.post(f"/api/backups/{backup['id']}/restore/", {"confirm": "RESTORE"}), 422, "backup_corrupt")
        self.assertEqual(Order.objects.count(), 1)

    def test_scheduled_command_and_retention(self):
        for _ in range(3):
            call_command("create_backup", verbosity=0, stdout=open(Path(self.folder) / "log.txt", "w", encoding="utf-8"))
        self.assertEqual(Backup.objects.filter(kind="auto").count(), 2)
