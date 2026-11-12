import json
import logging
import os
import shutil
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.core.maintenance import run_maintenance
from apps.core.models import AuditLog, Backup
from apps.shipping.models import MaystroConfig
import vizora_server

from .base import PASSWORD, ApiTestCase


class FirstAdminTests(ApiTestCase):
    def empty_install(self):
        User.objects.all().delete()
        self.anonymous()

    def test_setup_is_not_needed_once_accounts_exist(self):
        self.anonymous()
        response = self.client.get("/api/auth/setup/")
        self.assertCode(response, 200)
        self.assertEqual(response.data, {"needed": False})
        refused = self.client.post("/api/auth/setup/", {"username": "owner", "full_name": "صاحب المتجر", "password": PASSWORD})
        self.assertCode(refused, 409, "setup_done")
        self.assertFalse(User.objects.filter(username="owner").exists())

    def test_first_admin_is_created_once_and_can_sign_in(self):
        self.empty_install()
        self.assertEqual(self.client.get("/api/auth/setup/").data, {"needed": True})
        created = self.client.post("/api/auth/setup/", {"username": "owner", "full_name": "  صاحب المتجر ", "password": PASSWORD})
        self.assertCode(created, 201)
        self.assertEqual(created.data, {"username": "owner", "full_name": "صاحب المتجر"})
        owner = User.objects.get(username="owner")
        self.assertEqual(owner.role, Role.ADMIN)
        self.assertTrue(owner.has_section("dues"))
        self.assertTrue(AuditLog.objects.filter(action="first_admin_created").exists())
        login = self.client.post("/api/auth/login/", {"username": "owner", "password": PASSWORD, "device_id": "d-1", "device_name": "pc"})
        self.assertCode(login, 200)
        again = self.client.post("/api/auth/setup/", {"username": "second", "full_name": "آخر", "password": PASSWORD})
        self.assertCode(again, 409, "setup_done")
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(self.client.get("/api/auth/setup/").data, {"needed": False})

    def test_opening_the_sign_in_screen_does_not_use_up_sign_in_attempts(self):
        self.anonymous()
        for _ in range(15):
            self.assertCode(self.client.get("/api/auth/setup/"), 200)
        login = self.client.post("/api/auth/login/", {"username": "admin_user", "password": PASSWORD, "device_id": "d-1", "device_name": "pc"})
        self.assertCode(login, 200)

    def test_weak_password_and_bad_username_are_refused(self):
        self.empty_install()
        weak = self.client.post("/api/auth/setup/", {"username": "owner", "full_name": "صاحب المتجر", "password": "12345678"})
        self.assertCode(weak, 400)
        odd = self.client.post("/api/auth/setup/", {"username": "صاحب", "full_name": "صاحب المتجر", "password": PASSWORD})
        self.assertCode(odd, 400)
        self.assertEqual(User.objects.count(), 0)


class MaintenanceTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        override = override_settings(BACKUP_DIR=self.folder)
        override.enable()
        self.addCleanup(override.disable)

    def test_daily_backup_runs_once_per_day(self):
        self.assertEqual(run_maintenance({}), ["backup"])
        self.assertEqual(Backup.objects.filter(kind=Backup.Kind.AUTO).count(), 1)
        self.assertEqual(run_maintenance({}), [])
        Backup.objects.update(created_at=timezone.now() - timedelta(hours=25))
        self.assertEqual(run_maintenance({}), ["backup"])
        self.assertEqual(Backup.objects.filter(kind=Backup.Kind.AUTO).count(), 2)

    def test_maystro_updates_only_with_a_token_every_half_hour(self):
        run_maintenance({})
        state = {}
        with patch("apps.core.maintenance.sync_parcels") as parcels:
            self.assertEqual(run_maintenance(state), [])
            parcels.assert_not_called()
            config = MaystroConfig.load()
            config.set_token("secret-token")
            config.save()
            now = timezone.now()
            self.assertEqual(run_maintenance(state, now), ["maystro"])
            self.assertEqual(run_maintenance(state, now + timedelta(minutes=10)), [])
            self.assertEqual(run_maintenance(state, now + timedelta(minutes=31)), ["maystro"])
            self.assertEqual(parcels.call_count, 2)

    def test_maystro_failure_does_not_stop_maintenance(self):
        from apps.core.exceptions import ApiError

        config = MaystroConfig.load()
        config.set_token("secret-token")
        config.save()
        with patch("apps.core.maintenance.sync_parcels", side_effect=ApiError("تعذّر الاتصال بخادم Maystro", "maystro_unreachable", 502)):
            self.assertEqual(run_maintenance({}), ["backup"])


class ServerLauncherTests(ApiTestCase):
    def test_keys_are_created_once_and_reused(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        first = vizora_server.load_keys(folder)
        second = vizora_server.load_keys(folder)
        self.assertEqual(first, second)
        self.assertEqual(json.loads((folder / "keys.json").read_text(encoding="utf-8")), first)
        self.assertEqual(len(first["field_key"]), 44)
        self.assertGreater(len(first["secret_key"]), 40)
        self.assertFalse((folder / "keys.tmp").exists())

    def test_a_missing_process_is_reported_as_gone(self):
        self.assertTrue(vizora_server.parent_alive(os.getpid()))
        self.assertFalse(vizora_server.parent_alive(2 ** 22 + 7))

    def test_strangers_on_the_network_leave_one_short_warning_line(self):
        try:
            raise ValueError("bad host")
        except ValueError:
            record = logging.LogRecord("django.security.DisallowedHost", logging.ERROR, __file__, 1, "Invalid HTTP_HOST header: 'x.example'", None, sys.exc_info())
        self.assertTrue(vizora_server.QuietUnknownHosts().filter(record))
        line = logging.Formatter("%(levelname)s %(message)s").format(record)
        self.assertEqual(line, "WARNING Invalid HTTP_HOST header: 'x.example'")
        other = logging.LogRecord("django.request", logging.ERROR, __file__, 1, "boom", None, None)
        vizora_server.QuietUnknownHosts().filter(other)
        self.assertEqual(other.levelname, "ERROR")

    def test_only_this_computer_is_allowed_unless_phone_access_is_on(self):
        self.assertEqual(vizora_server.allowed_hosts(), ["127.0.0.1", "localhost"])
        self.assertEqual(vizora_server.allowed_hosts(" 192.168.1.20 ,,10.0.0.4,localhost"), ["127.0.0.1", "localhost", "192.168.1.20", "10.0.0.4"])

    def test_phones_reach_the_server_through_its_network_address(self):
        self.anonymous()
        with override_settings(ALLOWED_HOSTS=vizora_server.allowed_hosts("192.168.1.20")):
            allowed = self.client.get("/api/health/", HTTP_HOST="192.168.1.20:8765")
            refused = self.client.get("/api/health/", HTTP_HOST="evil.example")
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.data["service"], "vizora-global")
        self.assertEqual(refused.status_code, 400)
