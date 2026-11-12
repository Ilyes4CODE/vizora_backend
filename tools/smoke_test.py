import argparse
import getpass
import sys
import time

import requests

TIMEOUT = 30


def count(data):
    return len(data) if isinstance(data, list) else data.get("count", 0)


class Smoke:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        self.results = []

    def url(self, path):
        return f"{self.base}/api{path}"

    def step(self, name, action):
        started = time.monotonic()
        try:
            note = action()
        except Exception as error:
            self.results.append(False)
            print(f"FAIL {name}: {error}")
            return None
        self.results.append(True)
        print(f"PASS {name} {note or ''} ({time.monotonic() - started:.1f}s)")
        return note

    def get(self, path, **params):
        response = self.session.get(self.url(path), params=params, timeout=TIMEOUT)
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code} {response.text[:200]}")
        return response.json()

    def health(self):
        data = self.get("/health/")
        if data.get("service") != "vizora-global":
            raise RuntimeError(f"unexpected answer {data}")
        return data["time"]

    def https_redirect(self):
        plain = self.base.replace("https://", "http://", 1)
        response = requests.get(f"{plain}/api/health/", timeout=TIMEOUT, allow_redirects=False)
        if response.status_code in (301, 302, 307, 308) and response.headers.get("Location", "").startswith("https://"):
            return "http goes to https"
        raise RuntimeError(f"http answered {response.status_code}: turn on Force HTTPS Redirect for the domain in cPanel")

    def desktop_origin(self):
        response = requests.options(
            self.url("/auth/login/"),
            headers={"Origin": "app://vizora", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"},
            timeout=TIMEOUT,
        )
        allowed = response.headers.get("Access-Control-Allow-Origin")
        if allowed != "app://vizora":
            raise RuntimeError(f"the desktop app would be blocked (Access-Control-Allow-Origin={allowed}), set CORS_ALLOWED_ORIGINS=app://vizora")
        return "desktop app allowed"

    def wrong_password(self):
        response = self.session.post(self.url("/auth/login/"), json={"username": "smoke-test-nobody", "password": "wrong-password"}, timeout=TIMEOUT)
        if response.status_code != 401:
            raise RuntimeError(f"HTTP {response.status_code}")
        return response.json().get("detail", "")

    def sign_in(self, username, password):
        body = {"username": username, "password": password, "device_id": "smoke-test", "device_name": "smoke test"}
        response = self.session.post(self.url("/auth/login/"), json=body, timeout=TIMEOUT)
        if response.status_code == 403 and response.json().get("code") == "otp_required":
            body["otp"] = input("Two-factor code: ").strip()
            response = self.session.post(self.url("/auth/login/"), json=body, timeout=TIMEOUT)
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code} {response.text[:200]}")
        data = response.json()
        self.refresh = data["refresh"]
        self.session.headers["Authorization"] = f"Bearer {data['access']}"
        return f"{data['user']['full_name']} ({data['user']['role']})"

    def media(self, products):
        image = next((item["image"] for item in products if item.get("image")), None)
        if not image:
            return "no product image to check"
        response = requests.get(image, timeout=TIMEOUT)
        if response.status_code != 200 or not response.headers.get("Content-Type", "").startswith("image/"):
            raise RuntimeError(f"{image} answered {response.status_code}")
        return image

    def sign_out(self):
        response = self.session.post(self.url("/auth/logout/"), json={"refresh": self.refresh}, timeout=TIMEOUT)
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}")
        return "session closed"


def main():
    parser = argparse.ArgumentParser(description="Check a deployed Vizora server from outside")
    parser.add_argument("base", help="for example https://backend.vizorawear.com")
    parser.add_argument("--username", default="")
    options = parser.parse_args()

    smoke = Smoke(options.base)
    smoke.step("server answers over HTTPS", smoke.health)
    smoke.step("plain http is redirected to https", smoke.https_redirect)
    smoke.step("desktop app is allowed to call the server", smoke.desktop_origin)
    smoke.step("wrong password is refused in Arabic", smoke.wrong_password)
    username = options.username or input("Admin username: ").strip()
    password = getpass.getpass("Admin password: ")
    if smoke.step("admin signs in", lambda: smoke.sign_in(username, password)) is None:
        sys.exit(1)
    smoke.step("account details", lambda: smoke.get("/auth/me/")["username"])
    smoke.step("dashboard", lambda: f"{smoke.get('/dashboard/')['today']['pending_confirmation']} waiting for confirmation")
    products = smoke.step("products and stock", lambda: smoke.get("/products/"))
    if products is not None:
        print(f"     {len(products)} products, stock from {sorted({item.get('stock_source') for item in products}) or ['none']}")
        smoke.step("product images are served", lambda: smoke.media(products))
    smoke.step("orders", lambda: f"{smoke.get('/orders/', page_size=1)['count']} orders")
    smoke.step("confirmation and suivi queues", lambda: smoke.get("/confirmation/counts/"))
    smoke.step("parcels", lambda: smoke.get("/shipping/parcels/counts/"))
    smoke.step("Maystro settings", lambda: f"mode {smoke.get('/shipping/config/')['mode']}")
    smoke.step("backups list", lambda: f"{count(smoke.get('/backups/'))} backups")
    smoke.step("sign out", smoke.sign_out)
    passed = sum(smoke.results)
    print(f"\n{passed}/{len(smoke.results)} passed")
    sys.exit(0 if passed == len(smoke.results) else 1)


if __name__ == "__main__":
    main()
