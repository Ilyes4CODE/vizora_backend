import importlib
import sys
import tempfile
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch

import dj_database_url
from cryptography.fernet import Fernet
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connections
from django.test import override_settings
from django.utils import timezone
from PIL import Image

from apps.accounts.models import User
from apps.core.exceptions import ApiError
from apps.core.models import Backup
from apps.shipping.models import MaystroConfig

from .base import ApiTestCase

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND / "tools"))
make_env = importlib.import_module("make_env")


def png_file(name="photo.png"):
    buffer = BytesIO()
    Image.new("RGB", (4, 4), (31, 98, 224)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class PassengerEntryTests(ApiTestCase):
    def test_passenger_entry_exposes_the_django_application(self):
        module = importlib.import_module("passenger_wsgi")
        self.assertTrue(callable(module.application))


class MediaTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.media = override_settings(MEDIA_ROOT=folder.name, DEBUG=False)
        self.media.enable()
        self.addCleanup(self.media.disable)

    def test_product_images_are_served_when_debug_is_off(self):
        response = self.client.patch(f"/api/products/{self.product.pk}/", {"image": png_file()}, format="multipart")
        self.assertCode(response, 200)
        path = response.data["image"].split("://", 1)[1].split("/", 1)[1]
        image = self.client.get(f"/{path}")
        self.assertEqual(image.status_code, 200)
        self.assertTrue(image["Content-Type"].startswith("image/"))
        b"".join(image.streaming_content)

    def test_only_product_images_are_reachable(self):
        self.assertEqual(self.client.get("/media/../.env").status_code, 404)
        self.assertEqual(self.client.get("/media/backups/x.enc").status_code, 404)


class MaintenanceCommandTests(ApiTestCase):
    def test_cron_command_makes_the_daily_backup_once(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        with override_settings(BACKUP_DIR=folder.name):
            first = StringIO()
            call_command("run_maintenance", stdout=first)
            second = StringIO()
            call_command("run_maintenance", stdout=second)
        self.assertIn("backup", first.getvalue())
        self.assertEqual(second.getvalue().strip(), "nothing due")
        self.assertEqual(Backup.objects.filter(kind=Backup.Kind.AUTO).count(), 1)


class CronFullSyncTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        backups = override_settings(BACKUP_DIR=folder.name)
        backups.enable()
        self.addCleanup(backups.disable)
        config = MaystroConfig.load()
        config.mode = MaystroConfig.Mode.LIVE
        config.set_token("synthetic-token")
        config.save()

    def run_command(self):
        output = StringIO()
        with patch("apps.core.maintenance.sync_parcels"), patch("apps.core.management.commands.run_maintenance.refresh_stock", return_value=False):
            call_command("run_maintenance", stdout=output)
        return output.getvalue()

    def test_cron_finishes_a_full_sync_that_never_completed(self):
        with patch("apps.core.management.commands.run_maintenance.sync_account", return_value={"fetched": 3692}) as full:
            text = self.run_command()
        full.assert_called_once_with(full=True)
        self.assertIn("maystro full sync (3692 orders)", text)

    def test_cron_leaves_a_finished_full_sync_alone(self):
        MaystroConfig.objects.filter(pk=MaystroConfig.load().pk).update(mirror_full_sync_at=timezone.now())
        with patch("apps.core.management.commands.run_maintenance.sync_account") as full:
            self.run_command()
        full.assert_not_called()

    def test_cron_reports_a_sync_already_running(self):
        with patch("apps.core.management.commands.run_maintenance.sync_account", side_effect=ApiError("مزامنة Maystro جارية بالفعل", "sync_running", 409)):
            text = self.run_command()
        self.assertIn("maystro full sync not finished", text)


@override_settings(DEBUG=False, CORS_ALLOWED_ORIGINS=["app://vizora"], ALLOWED_HOSTS=["backend.vizorawear.com"])
class CheckServerTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        folders = override_settings(MEDIA_ROOT=f"{folder.name}/media", BACKUP_DIR=f"{folder.name}/backups")
        folders.enable()
        self.addCleanup(folders.disable)

    def run_check(self, **options):
        output = StringIO()
        try:
            call_command("check_server", stdout=output, skip_checks=False, **options)
        except SystemExit:
            return output.getvalue(), False
        return output.getvalue(), True

    def test_ready_server_passes_every_check(self):
        text, ok = self.run_check(domain="backend.vizorawear.com")
        self.assertTrue(ok, text)
        self.assertIn("server is ready", text)
        self.assertIn("simulation mode", text)
        self.assertNotIn("FAIL", text)

    def test_missing_admin_and_wrong_domain_are_reported(self):
        User.objects.all().delete()
        text, ok = self.run_check(domain="other.example")
        self.assertFalse(ok)
        self.assertIn("FAIL other.example is an allowed host", text)
        self.assertIn("FAIL an admin account exists", text)
        self.assertIn("2 check(s) failed", text)

    def test_old_postgres_is_refused(self):
        with patch.object(connections["default"], "vendor", "postgresql"), patch.object(connections["default"], "pg_version", 120005, create=True):
            text, ok = self.run_check()
        self.assertFalse(ok)
        self.assertIn("PostgreSQL 12 is too old", text)


class MakeEnvTests(ApiTestCase):
    def write(self, password, *extra):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        target = Path(folder.name) / ".env"
        arguments = ["--domain", "backend.vizorawear.com", "--engine", "postgres", "--db-name", "wzaheqfp_vizora", "--db-user", "wzaheqfp_app", "--output", str(target), *extra]
        with patch("make_env.getpass.getpass", return_value=password), patch("builtins.print"):
            make_env.main(arguments)
        return target, dict(line.split("=", 1) for line in target.read_text(encoding="utf-8").splitlines())

    def test_env_file_holds_fresh_keys_and_a_safe_database_address(self):
        target, values = self.write("p@ss:w/rd%#?")
        parsed = dj_database_url.parse(values["DATABASE_URL"])
        self.assertEqual((parsed["NAME"], parsed["USER"], parsed["PASSWORD"], parsed["HOST"], parsed["PORT"]), ("wzaheqfp_vizora", "wzaheqfp_app", "p@ss:w/rd%#?", "localhost", 5432))
        self.assertEqual((values["DJANGO_DEBUG"], values["DJANGO_ALLOWED_HOSTS"], values["CORS_ALLOWED_ORIGINS"]), ("false", "backend.vizorawear.com", "app://vizora"))
        Fernet(values["FIELD_ENCRYPTION_KEY"].encode())
        self.assertGreater(len(values["DJANGO_SECRET_KEY"]), 40)

    def test_existing_env_is_never_replaced_by_accident(self):
        target, first = self.write("first-password")
        with patch("make_env.getpass.getpass", return_value="second"), self.assertRaises(SystemExit):
            make_env.main(["--domain", "x", "--engine", "mysql", "--db-name", "a", "--db-user", "b", "--output", str(target)])
        self.assertIn(first["FIELD_ENCRYPTION_KEY"], target.read_text(encoding="utf-8"))

    def test_mysql_address_uses_its_port(self):
        url = make_env.database_url("mysql", "wzaheqfp_vizora", "wzaheqfp_app", "secret", "localhost")
        parsed = dj_database_url.parse(url)
        self.assertEqual((parsed["ENGINE"], parsed["PORT"]), ("django.db.backends.mysql", 3306))
