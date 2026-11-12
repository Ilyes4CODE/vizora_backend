from apps.accounts.permissions import sections_for

from .base import ApiTestCase
from .routes import endpoints, route_path

PUBLIC_PATHS = ("/api/health/", "/api/auth/setup/", "/api/auth/login/", "/api/auth/refresh/", "/api/schema/", "/api/docs/", "/api/shipping/webhook/")


class AccessSweepTests(ApiTestCase):
    def call(self, method, route):
        return getattr(self.client, method.lower())(route_path(route))

    def test_every_private_endpoint_rejects_anonymous_requests(self):
        checked = 0
        self.anonymous()
        for method, route, _ in endpoints():
            path = route_path(route)
            if path.startswith(PUBLIC_PATHS):
                continue
            with self.subTest(method=method, path=path):
                self.assertCode(self.call(method, route), 401)
                checked += 1
        self.assertGreater(checked, 60)

    def test_every_section_endpoint_rejects_roles_without_access(self):
        staff = [self.agent, self.stock_user, self.accountant]
        checked = 0
        for method, route, callback in endpoints():
            view = callback.cls()
            actions = getattr(callback, "actions", None)
            view.action = actions[method.lower()] if actions else None
            sections = sections_for(view, method)
            if not sections:
                continue
            denied = next((user for user in staff if not any(user.has_section(s) for s in sections)), None)
            if denied is None:
                continue
            self.as_user(denied)
            with self.subTest(method=method, route=route, user=denied.username):
                self.assertCode(self.call(method, route), 403)
                checked += 1
        self.assertGreater(checked, 40)

    def test_invalid_bearer_token_is_rejected(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer not-a-token")
        self.assertCode(self.client.get("/api/auth/me/"), 401, "token_not_valid")
