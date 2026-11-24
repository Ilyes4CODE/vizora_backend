from .models import AuditLog

GROUPS = {
    "orders": ("الطلبيات", ("order_", "orders_", "sheet_")),
    "confirmation": ("التأكيد والسويفي", ("call_outcome", "parcel_call_outcome")),
    "shipping": ("الشحن و Maystro", ("parcel_", "return_received", "maystro_", "wilaya_")),
    "products": ("المنتجات والمخزون", ("product_", "stock_")),
    "store": ("الموقع", ("store_", "landing_", "design_", "pixel_")),
    "money": ("المال والحملات", ("expense_", "settlement_", "statement_", "campaign_", "report_")),
    "team": ("الموظفون والدخول", ("user_", "login", "password_", "two_factor_", "device_", "first_admin")),
    "security": ("الحماية والنسخ", ("backup_",)),
    "other": ("تعديلات أخرى", ("api_change",)),
}
LABELS = {
    "order_created": "أضاف طلبية",
    "order_updated": "عدّل طلبية",
    "order_cancelled": "ألغى طلبية",
    "orders_assigned": "وزّع طلبيات",
    "orders_imported": "استورد طلبيات",
    "orders_sent": "أرسل إلى Maystro",
    "call_outcome": "نتيجة اتصال",
    "parcel_call_outcome": "اتصال سويفي",
    "login": "دخول",
    "login_failed": "محاولة دخول فاشلة",
    "api_change": "تعديل",
}
VERBS = {"POST": "نفّذ", "PUT": "عدّل", "PATCH": "عدّل", "DELETE": "حذف"}
OUTCOME_WORDS = {
    "confirmed": "تأكيد",
    "no_answer": "لا يرد",
    "busy": "مشغول",
    "postponed": "تأجيل",
    "cancelled": "إلغاء",
    "wrong_number": "رقم خاطئ",
    "duplicate": "مكررة",
    "will_receive": "سيستلم اليوم",
    "refused": "يرفض الاستلام",
    "wrong_address": "عنوان خاطئ",
}
AREAS = [
    ("/api/store/", "الموقع"),
    ("/api/products/", "المنتجات"),
    ("/api/orders/", "الطلبيات"),
    ("/api/shipping/", "الشحن"),
    ("/api/confirmation/", "التأكيد"),
    ("/api/campaigns/", "الحملات"),
    ("/api/accounting/", "المحاسبة"),
    ("/api/finance/", "المحاسبة"),
    ("/api/settings/", "الإعدادات"),
    ("/api/users/", "الموظفين"),
    ("/api/wilayas/", "أسعار التوصيل"),
]
SKIPPED = ("/api/auth/", "/api/shop/", "/api/shipping/webhook")


def group_of(action):
    for key, (_, prefixes) in GROUPS.items():
        if any(action.startswith(prefix) for prefix in prefixes):
            return key
    return "other"


def label_of(action):
    if action in LABELS:
        return LABELS[action]
    return GROUPS[group_of(action)][0]


def readable(message):
    head, _, rest = message.partition(" ")
    if head == "سجّل" and rest:
        word, _, tail = rest.partition(" ")
        if word in OUTCOME_WORDS:
            return f"سجّل «{OUTCOME_WORDS[word]}» {tail}".strip()
    return message


def mark(request):
    if request is not None:
        setattr(getattr(request, "_request", request), "_audited", True)


def record(action, message, request=None, user=None, target=None):
    actor = user
    if actor is None and request is not None and request.user.is_authenticated:
        actor = request.user
    mark(request)
    AuditLog.objects.create(
        user=actor,
        user_name=actor.full_name if actor else "",
        action=action,
        message=message[:255],
        target_type=target.__class__.__name__ if target is not None else "",
        target_id=str(target.pk) if target is not None else "",
        ip_address=request.META.get("REMOTE_ADDR") if request is not None else None,
    )


def record_unlogged_change(request, response):
    if request.method not in VERBS or response.status_code >= 400 or getattr(request, "_audited", False):
        return
    path = request.path
    if not path.startswith("/api/") or path.startswith(SKIPPED) or path.endswith("/preview/"):
        return
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return
    area = next((name for prefix, name in AREAS if path.startswith(prefix)), "النظام")
    AuditLog.objects.create(
        user=user,
        user_name=user.full_name,
        action="api_change",
        message=f"{VERBS[request.method]} في {area} · {path}"[:255],
        ip_address=request.META.get("REMOTE_ADDR"),
    )
