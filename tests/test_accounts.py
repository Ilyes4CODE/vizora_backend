from datetime import timedelta

import pyotp
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Device, RolePermission, User
from apps.core.models import AuditLog, SecuritySettings

from .base import PASSWORD, ApiTestCase


class AuthenticationTests(ApiTestCase):
    def login(self, username="agent_user", password=PASSWORD, **extra):
        self.anonymous()
        return self.client.post("/api/auth/login/", {"username": username, "password": password, **extra})

    def test_login_returns_tokens_and_permissions(self):
        response = self.login()
        self.assertCode(response, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["permissions"], ["orders", "confirmation", "shipping"])
        self.assertTrue(AuditLog.objects.filter(action="login").exists())

    def test_wrong_password_is_rejected_and_logged(self):
        self.assertCode(self.login(password="wrong"), 401, "invalid_credentials")
        self.assertTrue(AuditLog.objects.filter(action="login_failed").exists())

    def test_inactive_user_cannot_login(self):
        User.objects.filter(pk=self.agent.pk).update(is_active=False)
        self.assertCode(self.login(), 401, "invalid_credentials")

    def test_login_requires_username_and_password(self):
        self.anonymous()
        self.assertCode(self.client.post("/api/auth/login/", {}), 400, "validation_error")

    def test_login_is_rate_limited(self):
        for _ in range(10):
            self.assertCode(self.login(password="wrong"), 401)
        self.assertCode(self.login(password="wrong"), 429, "throttled")

    def test_refresh_rotates_and_old_token_is_revoked(self):
        refresh = self.login().data["refresh"]
        rotated = self.client.post("/api/auth/refresh/", {"refresh": refresh})
        self.assertCode(rotated, 200)
        self.assertIn("access", rotated.data)
        self.assertNotEqual(rotated.data["refresh"], refresh)
        self.assertCode(self.client.post("/api/auth/refresh/", {"refresh": refresh}), 401)

    def test_refresh_fails_for_disabled_account(self):
        refresh = self.login().data["refresh"]
        User.objects.filter(pk=self.agent.pk).update(is_active=False)
        self.assertCode(self.client.post("/api/auth/refresh/", {"refresh": refresh}), 401, "account_inactive")

    def test_logout_revokes_refresh_token(self):
        refresh = self.login().data["refresh"]
        self.as_user(self.agent)
        self.assertCode(self.client.post("/api/auth/logout/", {"refresh": refresh}), 204)
        self.assertCode(self.client.post("/api/auth/refresh/", {"refresh": refresh}), 401)

    def test_logout_with_invalid_token(self):
        self.assertCode(self.client.post("/api/auth/logout/", {"refresh": "bad"}), 400, "token_not_valid")

    def test_me_returns_current_user(self):
        self.as_user(self.accountant)
        response = self.client.get("/api/auth/me/")
        self.assertCode(response, 200)
        self.assertEqual(response.data["role"], "accountant")
        self.assertEqual(response.data["permissions"], ["accounting", "dues", "campaigns", "reports"])

    def test_change_password(self):
        old_refresh = str(RefreshToken.for_user(self.agent))
        self.as_user(self.agent)
        url = "/api/auth/change-password/"
        self.assertCode(self.client.post(url, {"current_password": "bad", "new_password": "Another-Strong-77!"}), 400, "wrong_password")
        self.assertCode(self.client.post(url, {"current_password": PASSWORD, "new_password": "123"}), 400, "validation_error")
        self.assertCode(self.client.post(url, {"current_password": PASSWORD, "new_password": "Another-Strong-77!"}), 204)
        self.assertCode(self.login(password="Another-Strong-77!"), 200)
        self.assertCode(self.client.post("/api/auth/refresh/", {"refresh": old_refresh}), 401)

    def test_two_factor_full_cycle(self):
        self.as_user(self.agent)
        secret = self.client.post("/api/auth/2fa/setup/").data["secret"]
        self.assertCode(self.client.post("/api/auth/2fa/enable/", {"otp": "000000"}), 400, "otp_invalid")
        self.assertCode(self.client.post("/api/auth/2fa/enable/", {"otp": pyotp.TOTP(secret).now()}), 200)
        self.assertCode(self.client.post("/api/auth/2fa/setup/"), 409, "two_factor_already_enabled")
        self.assertCode(self.login(), 403, "otp_required")
        self.assertCode(self.login(otp="000000"), 401, "otp_invalid")
        self.assertCode(self.login(otp=pyotp.TOTP(secret).now()), 200)
        self.as_user(self.agent)
        self.assertCode(self.client.post("/api/auth/2fa/disable/", {"password": "wrong"}), 400, "wrong_password")
        self.assertCode(self.client.post("/api/auth/2fa/disable/", {"password": PASSWORD}), 200)
        self.assertCode(self.login(), 200)

    def test_totp_secret_is_encrypted_at_rest(self):
        self.as_user(self.agent)
        secret = self.client.post("/api/auth/2fa/setup/").data["secret"]
        stored = User.objects.get(pk=self.agent.pk).totp_secret
        self.assertNotEqual(stored, secret)
        self.assertNotIn(secret, stored)

    def test_required_two_factor_blocks_business_endpoints_until_enrolled(self):
        SecuritySettings.objects.update_or_create(pk=1, defaults={"two_factor_required": True})
        self.as_user(self.agent)
        self.assertCode(self.client.get("/api/orders/"), 403, "two_factor_setup_required")
        self.assertCode(self.client.get("/api/auth/me/"), 200)
        secret = self.client.post("/api/auth/2fa/setup/").data["secret"]
        self.assertCode(self.client.post("/api/auth/2fa/enable/", {"otp": pyotp.TOTP(secret).now()}), 200)
        self.assertCode(self.client.get("/api/orders/"), 200)

    def test_inactive_session_expires(self):
        refresh = str(RefreshToken.for_user(self.agent))
        User.objects.filter(pk=self.agent.pk).update(last_activity_at=timezone.now() - timedelta(minutes=31))
        self.as_user(self.agent)
        self.assertCode(self.client.get("/api/auth/me/"), 401, "session_expired")
        self.assertCode(self.client.post("/api/auth/refresh/", {"refresh": refresh}), 401, "session_expired")
        SecuritySettings.objects.update_or_create(pk=1, defaults={"auto_logout_enabled": False})
        self.assertCode(self.client.get("/api/auth/me/"), 200)

    def test_activity_is_recorded(self):
        self.as_user(self.agent)
        self.client.get("/api/auth/me/")
        self.assertIsNotNone(User.objects.get(pk=self.agent.pk).last_activity_at)

    def test_approved_device_flow(self):
        SecuritySettings.objects.update_or_create(pk=1, defaults={"approved_devices_only": True})
        self.assertCode(self.login(), 400, "device_required")
        self.assertCode(self.login(device_id="pc-1", device_name="مكتب"), 403, "device_pending")
        device = Device.objects.get(user=self.agent)
        self.assertFalse(device.approved)
        self.as_user(self.admin)
        listing = self.client.get("/api/devices/")
        self.assertCode(listing, 200)
        self.assertEqual(listing.data[0]["device_id"], "pc-1")
        self.assertCode(self.client.post(f"/api/devices/{device.pk}/approve/"), 200)
        self.assertCode(self.login(device_id="pc-1"), 200)
        self.as_user(self.admin)
        self.assertCode(self.client.delete(f"/api/devices/{device.pk}/"), 204)
        self.assertCode(self.login(device_id="pc-1"), 403, "device_pending")

    def test_admin_is_exempt_from_device_approval(self):
        SecuritySettings.objects.update_or_create(pk=1, defaults={"approved_devices_only": True})
        self.assertCode(self.login(username="admin_user"), 200)


class StaffManagementTests(ApiTestCase):
    def payload(self, username="new_user", **extra):
        return {"username": username, "full_name": "موظف جديد", "role": "confirmation", "password": PASSWORD, **extra}

    def test_list_create_retrieve_update(self):
        created = self.client.post("/api/users/", self.payload())
        self.assertCode(created, 201)
        self.assertNotIn("password", created.data)
        user_id = created.data["id"]
        self.assertTrue(User.objects.get(pk=user_id).check_password(PASSWORD))
        self.assertEqual(len(self.client.get("/api/users/").data), 5)
        self.assertEqual(self.client.get(f"/api/users/{user_id}/").data["username"], "new_user")
        self.assertEqual(self.client.patch(f"/api/users/{user_id}/", {"phone": "0555000000"}).data["phone"], "0555000000")
        replaced = self.client.put(f"/api/users/{user_id}/", {"username": "renamed", "full_name": "اسم", "role": "stock"})
        self.assertCode(replaced, 200)
        self.assertEqual(replaced.data["role"], "stock")

    def test_password_is_required_and_validated(self):
        self.assertCode(self.client.post("/api/users/", {"username": "a", "full_name": "b", "role": "stock"}), 400)
        self.assertCode(self.client.post("/api/users/", self.payload(password="123")), 400)

    def test_six_account_limit(self):
        self.assertCode(self.client.post("/api/users/", self.payload("u5")), 201)
        self.assertCode(self.client.post("/api/users/", self.payload("u6")), 201)
        self.assertCode(self.client.post("/api/users/", self.payload("u7")), 409, "seat_limit_reached")
        seats = self.client.get("/api/users/seats/")
        self.assertCode(seats, 200)
        self.assertEqual(seats.data, {"used": 6, "total": 6, "free": 0})

    def test_reactivation_respects_limit(self):
        self.assertCode(self.client.delete(f"/api/users/{self.agent.pk}/"), 204)
        self.client.post("/api/users/", self.payload("u5"))
        self.client.post("/api/users/", self.payload("u6"))
        self.client.post("/api/users/", self.payload("u7"))
        self.assertCode(self.client.patch(f"/api/users/{self.agent.pk}/", {"is_active": True}), 409, "seat_limit_reached")

    def test_disable_keeps_record_and_blocks_access(self):
        self.assertCode(self.client.delete(f"/api/users/{self.agent.pk}/"), 204)
        self.assertFalse(User.objects.get(pk=self.agent.pk).is_active)
        self.as_user(self.agent)
        self.assertCode(self.client.get("/api/auth/me/"), 401)

    def test_cannot_disable_self_or_remove_last_admin(self):
        self.assertCode(self.client.delete(f"/api/users/{self.admin.pk}/"), 409, "cannot_disable_self")
        self.assertCode(self.client.patch(f"/api/users/{self.admin.pk}/", {"role": "stock"}), 409, "last_admin")
        self.assertCode(self.client.patch(f"/api/users/{self.admin.pk}/", {"is_active": False}), 409, "last_admin")

    def test_second_admin_can_be_disabled(self):
        second = self.client.post("/api/users/", self.payload("admin2", role="admin")).data["id"]
        self.assertCode(self.client.delete(f"/api/users/{second}/"), 204)

    def test_reset_password_revokes_sessions(self):
        refresh = str(RefreshToken.for_user(self.agent))
        url = f"/api/users/{self.agent.pk}/reset-password/"
        self.assertCode(self.client.post(url, {"new_password": "123"}), 400)
        self.assertCode(self.client.post(url, {"new_password": "Fresh-Password-55!"}), 204)
        self.assertTrue(User.objects.get(pk=self.agent.pk).check_password("Fresh-Password-55!"))
        self.assertCode(self.client.post("/api/auth/refresh/", {"refresh": refresh}), 401)

    def test_password_reset_ends_a_session_already_in_use(self):
        self.anonymous()
        access = self.client.post("/api/auth/login/", {"username": "agent_user", "password": PASSWORD}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertCode(self.client.get("/api/auth/me/"), 200)
        self.as_user(self.admin)
        self.assertCode(self.client.post(f"/api/users/{self.agent.pk}/reset-password/", {"new_password": "Fresh-Password-55!"}), 204)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertCode(self.client.get("/api/auth/me/"), 401, "session_revoked")
        self.anonymous()
        fresh = self.client.post("/api/auth/login/", {"username": "agent_user", "password": "Fresh-Password-55!"})
        self.assertCode(fresh, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {fresh.data['access']}")
        self.assertCode(self.client.get("/api/auth/me/"), 200)

    def test_manager_must_enrol_before_requiring_two_factor(self):
        current = dict(self.client.get("/api/settings/security/").data)
        response = self.client.put("/api/settings/security/", {**current, "two_factor_required": True})
        self.assertCode(response, 409, "enroll_two_factor_first")
        self.assertFalse(SecuritySettings.load().two_factor_required)
        User.objects.filter(pk=self.admin.pk).update(totp_enabled=True)
        self.assertCode(self.client.put("/api/settings/security/", {**current, "two_factor_required": True}), 200)
        self.assertTrue(SecuritySettings.load().two_factor_required)

    def test_reset_two_factor(self):
        User.objects.filter(pk=self.agent.pk).update(totp_enabled=True, totp_secret="x")
        self.assertCode(self.client.post(f"/api/users/{self.agent.pk}/reset-two-factor/"), 204)
        self.assertFalse(User.objects.get(pk=self.agent.pk).totp_enabled)

    def test_directory_is_available_to_any_staff(self):
        self.as_user(self.agent)
        response = self.client.get("/api/staff-directory/")
        self.assertCode(response, 200)
        self.assertEqual(len(response.data), 4)
        self.assertNotIn("username", response.data[0])

    def test_permission_matrix_matches_design_defaults(self):
        response = self.client.get("/api/permissions/")
        self.assertCode(response, 200)
        sections = {row["value"]: row["roles"] for row in response.data["sections"]}
        self.assertEqual(len(sections), 10)
        self.assertTrue(sections["team"]["admin"])
        self.assertFalse(sections["team"]["confirmation"])
        self.assertTrue(sections["orders"]["stock"])
        self.assertFalse(sections["accounting"]["confirmation"])

    def test_permission_change_takes_effect(self):
        self.as_user(self.agent)
        self.assertCode(self.client.get("/api/reports/monthly/"), 403)
        self.as_user(self.admin)
        self.assertCode(self.client.put("/api/permissions/", {"role": "confirmation", "section": "reports", "allowed": True}), 200)
        self.as_user(self.agent)
        self.assertCode(self.client.get("/api/reports/monthly/"), 200)
        self.assertTrue(RolePermission.objects.get(role="confirmation", section="reports").allowed)

    def test_admin_role_is_locked(self):
        self.assertCode(self.client.put("/api/permissions/", {"role": "admin", "section": "reports", "allowed": False}), 400)
