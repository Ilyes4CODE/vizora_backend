import base64
import binascii
import json
import os
import re
import threading
import time

import requests

from apps.core.excel import normalize_text

from .models import MaystroConfig

API_URL = (os.environ.get("MAYSTRO_API_URL") or "https://orders-management.maystro-delivery.com").rstrip("/")
STORE_URL = (os.environ.get("MAYSTRO_STORE_URL") or "https://backend.maystro-delivery.com").rstrip("/")
STORE_ORDERS_URL = f"{STORE_URL}/api/stores/orders/"
STORE_BALANCE_URL = f"{STORE_URL}/api/stores/payments/"
MAX_PAYMENT_PAGES = 200
REQUEST_TIMEOUT_SECONDS = 30
MIN_REQUEST_INTERVAL = 0.21
MAX_RETRY_WAIT = 60
NETWORK_ATTEMPTS = 4
NETWORK_RETRY_WAIT = 3
TRANSIENT_STATUSES = (502, 503, 504)
ALGERIA = 1
HOME_DELIVERY = 1
STOP_DESK_DELIVERY = 2
MAX_WEBHOOK_LAYERS = 6
MAX_PRODUCT_PAGES = 50
ACTIVE_STATUSES = (4, 5, 6, 8, 9, 11, 12, 15, 22, 31, 32, 33, 42)
STATUS_LABELS = {
    "4": "أُنشئ الطرد",
    "5": "طُلب الاستلام من المتجر",
    "6": "قيد المعالجة",
    "8": "بانتظار النقل",
    "9": "في الطريق للتسليم",
    "10": "في الطريق للإرجاع",
    "11": "بانتظار تخصيص المخزون",
    "12": "نفد المخزون عند Maystro",
    "15": "جاهز للشحن",
    "22": "أُسند إلى موزع",
    "31": "خرج مع الموزع",
    "32": "تنبيه من الموزع",
    "33": "جاهز للاستلام في المكتب",
    "41": "مسلّم",
    "42": "مؤجل",
    "50": "أُلغي",
    "51": "جاهز للإرجاع",
    "52": "استلمه المتجر",
    "53": "لم يُستلم",
}
ORDER_ERRORS = {
    10: "السعر غير صالح أو أقل من سعر التوصيل ورصيدك لا يغطي الفرق",
    20: "عنوان التوصيل ناقص",
    30: "اسم الزبون أو هاتفه ناقص أو طويل جداً",
    40: "الولاية أو البلدية غير موجودة أو غير مغطاة",
    45: "نوع التوصيل غير متاح لهذه البلدية",
    50: "المنتجات ناقصة أو غير متطابقة",
    55: "طلبية مكررة لنفس الزبون خلال 24 ساعة عند Maystro",
    60: "خطأ غير متوقع عند Maystro",
    70: "لا يوجد سعر توصيل لهذه البلدية",
    80: "قائمة الطلبيات فارغة",
    90: "أكثر من 1000 طلبية في طلب واحد",
    100: "المرجع طويل جداً",
    110: "الكمية أكبر من المسموح لمتجرك",
    120: "متجرك غير مفعّل لإنشاء الطلبيات",
}
WAIT_PATTERN = re.compile(r"(\d+)\s*second")
_last_request = [0.0]
_request_lock = threading.Lock()


class MaystroError(Exception):
    def __init__(self, message, code="maystro_error", status_code=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


def order_error_message(body):
    errors = body.get("errors") if isinstance(body, dict) else None
    if not errors:
        return ""
    messages = []
    for item in errors:
        code = item.get("code") if isinstance(item, dict) else None
        try:
            label = ORDER_ERRORS.get(int(code))
        except (TypeError, ValueError):
            label = None
        detail = item.get("detail", "") if isinstance(item, dict) else str(item)
        messages.append(f"{label} ({code})" if label else f"{detail} ({code})")
    return " · ".join(messages)


class LiveMaystroClient:
    def __init__(self, config, attempts=NETWORK_ATTEMPTS):
        self.config = config
        self.attempts = attempts

    def throttle(self):
        with _request_lock:
            wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request[0])
            if wait > 0:
                time.sleep(wait)
            _last_request[0] = time.monotonic()

    def send(self, method, url, **kwargs):
        token = self.config.get_token()
        if not token:
            raise MaystroError("أدخل مفتاح Maystro أولاً", "maystro_not_configured")
        headers = {"Authorization": f"Token {token}", "Accept": "application/json"}
        attempts = self.attempts if method == "GET" else 1
        for attempt in range(1, attempts + 1):
            self.throttle()
            try:
                response = requests.request(method, url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
            except requests.RequestException as error:
                if attempt == attempts:
                    raise MaystroError("تعذّر الاتصال بخادم Maystro", "maystro_unreachable") from error
            else:
                if response.status_code not in TRANSIENT_STATUSES or attempt == attempts:
                    return response
            time.sleep(NETWORK_RETRY_WAIT * attempt)

    def request(self, method, url, **kwargs):
        response = self.send(method, url, **kwargs)
        if response.status_code == 429:
            match = WAIT_PATTERN.search(self.body_text(response))
            time.sleep(min(int(match.group(1)) + 1 if match else 5, MAX_RETRY_WAIT))
            response = self.send(method, url, **kwargs)
        if response.status_code >= 400:
            raise MaystroError(self.rejection_message(response), "maystro_rejected", response.status_code)
        try:
            return response.json()
        except ValueError as error:
            raise MaystroError("رد غير متوقع من Maystro", "maystro_bad_response") from error

    def body_text(self, response):
        try:
            body = response.json()
        except (ValueError, AttributeError):
            return ""
        return json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body

    def rejection_message(self, response):
        try:
            body = response.json()
        except (ValueError, AttributeError):
            body = {}
        if response.status_code in (401, 403):
            return "مفتاح Maystro غير صالح أو لا يملك الصلاحية"
        detail = order_error_message(body)
        if not detail and isinstance(body, dict):
            detail = str(body.get("detail") or body.get("error") or "")
        return f"رفض Maystro الطلب (HTTP {response.status_code}) {detail}".strip()

    def api(self, path):
        return f"{API_URL}{path}"

    def ping(self):
        self.request("GET", self.api("/api/base/wilayas/"))
        return True

    def wilayas(self):
        return self.request("GET", self.api("/api/base/wilayas/"), params={"country": ALGERIA})

    def communes(self, wilaya_display_id):
        return self.request("GET", self.api("/api/base/communes/"), params={"wilaya": wilaya_display_id})

    def delivery_options(self, commune_id):
        return self.request("GET", self.api("/api/base/delivery-options/"), params={"commune": commune_id})

    def products(self):
        collected = []
        url = self.api("/api/stock/products/")
        params = {"page_size": 200}
        for _ in range(MAX_PRODUCT_PAGES):
            data = self.request("GET", url, params=params)
            collected.extend(data.get("results", []))
            url = data.get("next")
            params = None
            if not url:
                break
        return collected

    def create_product(self, name, reference=None, description=None, low_stock_level=None):
        if not self.config.store_id:
            raise MaystroError("أدخل معرّف متجرك في Maystro أولاً في «الحماية والنسخ» ← «ربط Maystro»", "maystro_store_missing")
        body = {"store": self.config.store_id, "logistical_description": name}
        if reference:
            body["product_id"] = reference
        if description:
            body["comercial_description"] = description
        if low_stock_level is not None:
            body["low_stock_level"] = low_stock_level
        data = self.request("POST", self.api("/api/stock/products/"), json=body)
        if not isinstance(data, dict) or not data.get("id"):
            raise MaystroError("رد غير متوقع من Maystro", "maystro_bad_response")
        return data

    def stock_details(self, product_id):
        data = self.request("GET", self.api("/api/stock/product-details/stock-details/"), params={"product_id": product_id})
        return data if isinstance(data, list) else []

    def balance(self):
        data = self.request("GET", STORE_BALANCE_URL)
        if not isinstance(data, dict):
            raise MaystroError("رد غير متوقع من Maystro", "maystro_bad_response")
        return {"current": data.get("solde en cours"), "payable": data.get("solde a payer")}

    def payments(self, page):
        data = self.request("GET", self.api("/api/payments/"), params={"page": page})
        if not isinstance(data, dict):
            raise MaystroError("رد غير متوقع من Maystro", "maystro_bad_response")
        return {"results": [row for row in data.get("results", []) if isinstance(row, dict)], "next": data.get("next"), "count": data.get("count", 0)}

    def store_orders(self, page, statuses=None):
        params = {"page": page}
        if statuses:
            params["status"] = ",".join(str(status) for status in statuses)
        data = self.request("GET", STORE_ORDERS_URL, params=params)
        body = data.get("list", data) if isinstance(data, dict) else {}
        return {"results": body.get("results", []), "next": body.get("next"), "count": body.get("count", 0)}

    def commune_id(self, order):
        target = normalize_text(order.commune)
        if not target:
            raise MaystroError("اختر البلدية في الطلبية قبل إرسالها (زر «تعديل»)", "commune_missing")
        for commune in order.wilaya.communes.exclude(courier_ref=""):
            if target and target in {normalize_text(commune.name_ar), normalize_text(commune.name_fr)}:
                return int(commune.courier_ref)
        raise MaystroError(f"البلدية «{order.commune}» غير مربوطة بـ Maystro", "commune_not_linked")

    def details(self, order):
        lines = []
        for item in order.items.select_related("product"):
            if not item.product.courier_ref:
                raise MaystroError(f"المنتج «{item.product.name}» غير مربوط بـ Maystro: أنشئه في Maystro بنفس الاسم وسيُربط تلقائياً، أو ضع معرّفه في «المنتجات والمخزون»", "product_not_linked")
            lines.append({"product": item.product.courier_ref, "quantity": item.quantity})
        return lines

    def driver_note(self, order):
        parts = [order.note.strip()] if order.note and order.note.strip() else []
        for item in order.items.select_related("product"):
            if item.variant and item.variant not in (order.note or ""):
                parts.append(f"{item.product.name}: {item.variant} × {item.quantity}")
        return " | ".join(parts)[:500] or None

    def build_payload(self, order):
        return {
            "external_id": order.reference,
            "customer_name": order.customer_name,
            "customer_phone": order.phone,
            "destination_text": order.address or order.commune or order.wilaya.name_ar,
            "commune": self.commune_id(order),
            "delivery_type": STOP_DESK_DELIVERY if order.delivery_type == "desk" else HOME_DELIVERY,
            "total_price": order.total,
            "express": False,
            "note_to_driver": self.driver_note(order),
            "details": self.details(order),
        }

    def create_order(self, order):
        data = self.request("POST", self.api("/api/orders/"), json=self.build_payload(order))
        result = data[0] if isinstance(data, list) and data else data
        if not isinstance(result, dict):
            raise MaystroError("رد غير متوقع من Maystro", "maystro_bad_response")
        if result.get("success") is False:
            raise MaystroError(f"رفض Maystro الطلبية: {order_error_message(result)}", "maystro_rejected")
        if not result.get("tracking") or not result.get("id"):
            raise MaystroError("لم يُرجع Maystro رقم تتبع", "maystro_bad_response")
        return {"tracking_number": str(result["tracking"]), "external_id": str(result["id"])}

    def history(self, maystro_id):
        data = self.request("GET", self.api(f"/api/orders/history-order/{maystro_id}/"))
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def fetch_status(self, shipment):
        history = self.history(shipment.external_id)
        if not history:
            return None
        latest = history[0]
        if latest.get("status") in (None, ""):
            return None
        return {"status": str(latest["status"]), "occurred_at": latest.get("created_at")}


def is_connected(config=None):
    config = config or MaystroConfig.load()
    return bool(config.encrypted_token)


def get_client(config=None, attempts=NETWORK_ATTEMPTS):
    return LiveMaystroClient(config or MaystroConfig.load(), attempts)


def decode_text(value):
    try:
        return json.loads(value)
    except ValueError:
        pass
    try:
        return base64.b64decode(value, validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        return None


def decode_webhook(body):
    value = body
    for _ in range(MAX_WEBHOOK_LAYERS):
        if isinstance(value, dict):
            envelope = value.get("message")
            if not isinstance(envelope, dict) or "data" not in envelope:
                return value
            value = envelope["data"]
        elif isinstance(value, str):
            value = decode_text(value)
        else:
            return None
    return None
