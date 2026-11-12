from django.conf import settings
from django.db import models
from django.utils import timezone


class OrderStatus(models.TextChoices):
    PENDING = "pending", "بانتظار التأكيد"
    CONFIRMED = "confirmed", "مؤكدة"
    SHIPPING = "shipping", "قيد الشحن"
    DELIVERED = "delivered", "مسلّمة"
    RETURNED = "returned", "مرتجعة"
    CANCELLED = "cancelled", "ملغاة"


class DeliveryType(models.TextChoices):
    HOME = "home", "إلى المنزل"
    DESK = "desk", "مكتب التوصيل"


class Source(models.TextChoices):
    MANUAL = "manual", "يدوية"
    EXCEL = "excel", "استيراد Excel"


class ConfirmationOutcome(models.TextChoices):
    CONFIRMED = "confirmed", "مؤكدة"
    NO_ANSWER = "no_answer", "لا يرد"
    BUSY = "busy", "مشغول"
    POSTPONED = "postponed", "تأجيل"
    CANCELLED = "cancelled", "ملغاة"
    WRONG_NUMBER = "wrong_number", "رقم خاطئ"
    DUPLICATE = "duplicate", "مكررة"


class SuiviOutcome(models.TextChoices):
    WILL_RECEIVE = "will_receive", "سيستلم اليوم"
    NO_ANSWER = "no_answer", "لا يرد"
    POSTPONED = "postponed", "طلب تأجيل"
    REFUSED = "refused", "يرفض الاستلام"
    WRONG_ADDRESS = "wrong_address", "عنوان خاطئ"


class Order(models.Model):
    reference = models.CharField(max_length=30, unique=True, null=True, blank=True)
    customer_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=15, db_index=True)
    wilaya = models.ForeignKey("core.Wilaya", on_delete=models.PROTECT, related_name="orders")
    commune = models.CharField(max_length=80, blank=True)
    address = models.CharField(max_length=255, blank=True)
    delivery_type = models.CharField(max_length=10, choices=DeliveryType.choices, default=DeliveryType.HOME)
    subtotal = models.PositiveIntegerField(default=0)
    delivery_fee = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12, choices=OrderStatus.choices, default=OrderStatus.PENDING, db_index=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    campaign = models.ForeignKey("finance.Campaign", null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_orders")
    note = models.TextField(blank=True)
    call_attempts = models.PositiveSmallIntegerField(default=0)
    next_call_at = models.DateTimeField(null=True, blank=True, db_index=True)
    cancel_reason = models.CharField(max_length=30, blank=True)
    suivi_attempts = models.PositiveSmallIntegerField(default=0)
    suivi_next_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True, db_index=True)
    returned_at = models.DateTimeField(null=True, blank=True, db_index=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.reference or str(self.pk)


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="order_items")
    quantity = models.PositiveIntegerField()
    unit_price = models.PositiveIntegerField()
    unit_cost = models.PositiveIntegerField()


class OrderEvent(models.Model):
    class Stage(models.TextChoices):
        SYSTEM = "system", "النظام"
        CONFIRM = "confirm", "التأكيد"
        SUIVI = "suivi", "السويفي"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="events")
    stage = models.CharField(max_length=10, choices=Stage.choices, default=Stage.SYSTEM)
    outcome = models.CharField(max_length=20, blank=True)
    message = models.CharField(max_length=255)
    note = models.TextField(blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class ImportBatch(models.Model):
    class Status(models.TextChoices):
        UPLOADED = "uploaded", "مرفوع"
        COMMITTED = "committed", "مستورد"

    file_name = models.CharField(max_length=200)
    columns = models.JSONField(default=list)
    rows = models.JSONField(default=list)
    mapping = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UPLOADED)
    created_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
