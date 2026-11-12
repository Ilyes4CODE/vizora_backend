import secrets

from django.conf import settings
from django.db import models

from apps.core.crypto import decrypt_text, encrypt_text
from apps.core.models import SingletonModel


class Stage(models.TextChoices):
    TRANSIT = "transit", "في الطريق"
    OUT = "out", "خرج مع الموزع"
    DESK = "desk", "في مكتب التوصيل"
    FAILED = "failed", "تعذّر التسليم"
    DELIVERED = "delivered", "مسلّم"
    RETURNED = "returned", "مرتجع"
    ABORTED = "aborted", "ملغى عند Maystro"


def default_status_map():
    return {
        "4": Stage.TRANSIT.value,
        "5": Stage.TRANSIT.value,
        "6": Stage.TRANSIT.value,
        "8": Stage.TRANSIT.value,
        "9": Stage.TRANSIT.value,
        "10": Stage.RETURNED.value,
        "11": Stage.TRANSIT.value,
        "12": Stage.FAILED.value,
        "15": Stage.TRANSIT.value,
        "22": Stage.OUT.value,
        "31": Stage.OUT.value,
        "32": Stage.FAILED.value,
        "33": Stage.DESK.value,
        "41": Stage.DELIVERED.value,
        "42": Stage.FAILED.value,
        "50": Stage.ABORTED.value,
        "51": Stage.RETURNED.value,
        "52": Stage.RETURNED.value,
        "53": Stage.RETURNED.value,
    }


class MaystroConfig(SingletonModel):
    class Mode(models.TextChoices):
        SIMULATED = "simulated", "محاكاة"
        LIVE = "live", "مباشر"

    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.SIMULATED)
    store_id = models.CharField(max_length=64, blank=True)
    encrypted_token = models.TextField(blank=True)
    status_map = models.JSONField(default=default_status_map)
    webhook_token = models.CharField(max_length=64, blank=True)
    return_fee = models.PositiveIntegerField(default=350)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    live_parcels_at = models.DateTimeField(null=True, blank=True)
    live_stock_at = models.DateTimeField(null=True, blank=True)
    live_error = models.CharField(max_length=255, blank=True)
    last_sync_ok = models.BooleanField(default=True)
    last_sync_error = models.CharField(max_length=255, blank=True)
    current_points = models.FloatField(null=True, blank=True)
    mirror_synced_at = models.DateTimeField(null=True, blank=True)
    mirror_full_sync_at = models.DateTimeField(null=True, blank=True)
    mirror_sync_ok = models.BooleanField(default=True)
    mirror_sync_error = models.CharField(max_length=255, blank=True)
    mirror_running_since = models.DateTimeField(null=True, blank=True)
    mirror_progress_done = models.PositiveIntegerField(default=0)
    mirror_progress_total = models.PositiveIntegerField(default=0)
    balance_start_date = models.DateField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.webhook_token:
            self.webhook_token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def set_token(self, token):
        self.encrypted_token = encrypt_text(token)

    def get_token(self):
        return decrypt_text(self.encrypted_token)


class Shipment(models.Model):
    order = models.OneToOneField("orders.Order", on_delete=models.CASCADE, related_name="shipment")
    tracking_number = models.CharField(max_length=60, unique=True)
    external_id = models.CharField(max_length=60, blank=True)
    stage = models.CharField(max_length=12, choices=Stage.choices, default=Stage.TRANSIT, db_index=True)
    raw_status = models.CharField(max_length=60, blank=True)
    delivery_attempts = models.PositiveSmallIntegerField(default=0)
    return_fee = models.PositiveIntegerField(default=0)
    return_condition = models.CharField(max_length=10, blank=True)
    return_received_at = models.DateTimeField(null=True, blank=True)
    settlement = models.ForeignKey("finance.Settlement", null=True, blank=True, on_delete=models.SET_NULL, related_name="shipments")
    last_update_at = models.DateTimeField()
    last_event_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        ordering = ["-last_update_at", "-id"]

    @property
    def is_terminal(self):
        return self.stage in (Stage.DELIVERED, Stage.RETURNED, Stage.ABORTED)


class MaystroPayment(models.Model):
    class Source(models.TextChoices):
        MANUAL = "manual", "مسجّل يدوياً"
        WEBHOOK = "webhook", "إشعار من Maystro"

    fingerprint = models.CharField(max_length=64, unique=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.WEBHOOK)
    paid_on = models.DateTimeField()
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    order_ids = models.JSONField(default=list)
    orders_count = models.PositiveIntegerField(default=0)
    amount = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_on", "-id"]


class MaystroOrder(models.Model):
    maystro_id = models.CharField(max_length=64, unique=True)
    display_id = models.CharField(max_length=40, db_index=True)
    status = models.PositiveSmallIntegerField(db_index=True)
    product_price = models.BigIntegerField(default=0)
    delivery_price = models.BigIntegerField(default=0)
    total_price = models.BigIntegerField(default=0)
    return_fees = models.BigIntegerField(null=True, blank=True)
    paid_to_store = models.BooleanField(default=False)
    is_refund = models.BooleanField(default=False)
    is_exchange = models.BooleanField(default=False)
    delivery_type = models.PositiveSmallIntegerField(null=True, blank=True)
    wilaya = models.CharField(max_length=80, blank=True)
    wilaya_id = models.PositiveSmallIntegerField(null=True, blank=True)
    commune_name = models.CharField(max_length=120, blank=True)
    product_names = models.CharField(max_length=500, blank=True)
    products = models.JSONField(default=list)
    abort_reason = models.CharField(max_length=200, blank=True)
    alert_reason = models.CharField(max_length=200, blank=True)
    ordered_at = models.DateTimeField(null=True, blank=True, db_index=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    remote_updated_at = models.DateTimeField(null=True, blank=True)
    payment = models.ForeignKey(MaystroPayment, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    shipment = models.ForeignKey(Shipment, null=True, blank=True, on_delete=models.SET_NULL, related_name="mirror_orders")
    customer_name = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=30, blank=True)
    customer_phone2 = models.CharField(max_length=30, blank=True)
    address = models.CharField(max_length=255, blank=True)
    alerted_at = models.DateTimeField(null=True, blank=True)
    postponed_to = models.DateTimeField(null=True, blank=True)
    suivi_attempts = models.PositiveSmallIntegerField(default=0)
    suivi_next_at = models.DateTimeField(null=True, blank=True)
    suivi_status = models.PositiveSmallIntegerField(null=True, blank=True)
    live_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-ordered_at", "-id"]


class ParcelCall(models.Model):
    parcel = models.ForeignKey(MaystroOrder, on_delete=models.CASCADE, related_name="calls")
    outcome = models.CharField(max_length=20)
    message = models.CharField(max_length=255)
    note = models.TextField(blank=True)
    next_call_at = models.DateTimeField(null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class MaystroProduct(models.Model):
    maystro_id = models.CharField(max_length=64, unique=True)
    display_id = models.PositiveIntegerField(null=True, blank=True)
    name = models.CharField(max_length=255)
    reference = models.CharField(max_length=120, blank=True)
    delivery_rate = models.FloatField(null=True, blank=True)
    cancelation_rate = models.FloatField(null=True, blank=True)
    deleted = models.BooleanField(default=False)
    last_used = models.DateTimeField(null=True, blank=True)
    low_stock_level = models.PositiveIntegerField(null=True, blank=True)
    stock = models.JSONField(default=list)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]


class ShipmentEvent(models.Model):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="events")
    raw_status = models.CharField(max_length=60)
    stage = models.CharField(max_length=12, blank=True)
    message = models.CharField(max_length=255, blank=True)
    occurred_at = models.DateTimeField()

    class Meta:
        ordering = ["-occurred_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["shipment", "raw_status", "occurred_at"], name="unique_shipment_event")
        ]
