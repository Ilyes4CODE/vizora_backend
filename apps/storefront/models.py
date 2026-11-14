from django.db import models
from django.utils import timezone

from apps.core.crypto import decrypt_text, encrypt_text
from apps.core.models import SingletonModel


class PurchaseMoment(models.TextChoices):
    PLACED = "placed", "عند تسجيل الطلبية"
    CONFIRMED = "confirmed", "عند تأكيد الطلبية"
    DELIVERED = "delivered", "عند تسليم الطرد"


class StoreSettings(SingletonModel):
    is_open = models.BooleanField(default=True)
    whatsapp = models.CharField(max_length=30, default="+213667277676", blank=True)
    facebook_url = models.URLField(blank=True)
    instagram_url = models.URLField(default="https://www.instagram.com/vizora_global/", blank=True)
    tiktok_url = models.URLField(blank=True)
    announcement_ar = models.CharField(max_length=160, blank=True)
    announcement_en = models.CharField(max_length=160, blank=True)
    delivery_days_ar = models.CharField(max_length=120, blank=True)
    delivery_days_en = models.CharField(max_length=120, blank=True)
    facebook_pixel = models.CharField(max_length=40, blank=True)
    tiktok_pixel = models.CharField(max_length=40, blank=True)
    google_analytics = models.CharField(max_length=40, blank=True)
    encrypted_facebook_token = models.TextField(blank=True)
    facebook_server_since = models.DateTimeField(null=True, blank=True)
    facebook_test_code = models.CharField(max_length=40, blank=True)
    encrypted_tiktok_token = models.TextField(blank=True)
    tiktok_server_since = models.DateTimeField(null=True, blank=True)
    tiktok_test_code = models.CharField(max_length=40, blank=True)
    purchase_moment = models.CharField(max_length=10, choices=PurchaseMoment.choices, default=PurchaseMoment.PLACED)
    design_token = models.CharField(max_length=64, blank=True)
    orders_per_hour = models.PositiveSmallIntegerField(default=5)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def facebook_token(self):
        return decrypt_text(self.encrypted_facebook_token) if self.encrypted_facebook_token else ""

    @facebook_token.setter
    def facebook_token(self, value):
        if value and not self.encrypted_facebook_token:
            self.facebook_server_since = timezone.now()
        if not value:
            self.facebook_server_since = None
        self.encrypted_facebook_token = encrypt_text(value) if value else ""

    @property
    def tiktok_token(self):
        return decrypt_text(self.encrypted_tiktok_token) if self.encrypted_tiktok_token else ""

    @tiktok_token.setter
    def tiktok_token(self, value):
        if value and not self.encrypted_tiktok_token:
            self.tiktok_server_since = timezone.now()
        if not value:
            self.tiktok_server_since = None
        self.encrypted_tiktok_token = encrypt_text(value) if value else ""


class StoreProduct(models.Model):
    product = models.OneToOneField("catalog.Product", on_delete=models.CASCADE, related_name="store")
    slug = models.SlugField(max_length=80, unique=True)
    is_published = models.BooleanField(default=False)
    is_featured = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)
    title_ar = models.CharField(max_length=150)
    title_en = models.CharField(max_length=150, blank=True)
    summary_ar = models.CharField(max_length=255, blank=True)
    summary_en = models.CharField(max_length=255, blank=True)
    description_ar = models.TextField(blank=True)
    description_en = models.TextField(blank=True)
    badge_ar = models.CharField(max_length=40, blank=True)
    badge_en = models.CharField(max_length=40, blank=True)
    compare_price = models.PositiveIntegerField(null=True, blank=True)
    max_quantity = models.PositiveSmallIntegerField(default=5)
    options = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.title_ar


class StoreOffer(models.Model):
    store_product = models.ForeignKey(StoreProduct, on_delete=models.CASCADE, related_name="offers")
    title_ar = models.CharField(max_length=120)
    title_en = models.CharField(max_length=120, blank=True)
    details_ar = models.CharField(max_length=255, blank=True)
    details_en = models.CharField(max_length=255, blank=True)
    price = models.PositiveIntegerField()
    compare_price = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.title_ar


class StoreImage(models.Model):
    store_product = models.ForeignKey(StoreProduct, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="products/")
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    alt_ar = models.CharField(max_length=150, blank=True)
    alt_en = models.CharField(max_length=150, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]


class StorePage(models.Model):
    slug = models.SlugField(max_length=40, unique=True)
    title_ar = models.CharField(max_length=120)
    title_en = models.CharField(max_length=120)
    body_ar = models.TextField()
    body_en = models.TextField()
    show_in_footer = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]


class Review(models.Model):
    name = models.CharField(max_length=80)
    wilaya = models.CharField(max_length=60, blank=True)
    text = models.TextField(max_length=600)
    rating = models.PositiveSmallIntegerField(default=5)
    is_published = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "-id"]


class ContactMessage(models.Model):
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=15)
    message = models.TextField(max_length=2000)
    lang = models.CharField(max_length=2, default="ar")
    ip = models.GenericIPAddressField(null=True, blank=True)
    handled = models.BooleanField(default=False, db_index=True)
    handled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["handled", "-created_at", "-id"]


class StockAlert(models.Model):
    store_product = models.ForeignKey(StoreProduct, on_delete=models.CASCADE, related_name="alerts")
    phone = models.CharField(max_length=15)
    lang = models.CharField(max_length=2, default="ar")
    notified = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["notified", "-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["store_product", "phone"], name="unique_stock_alert_phone")]


class OrderOrigin(models.Model):
    order = models.OneToOneField("orders.Order", on_delete=models.CASCADE, related_name="web")
    ip = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    user_agent = models.CharField(max_length=255, blank=True)
    lang = models.CharField(max_length=2, default="ar")
    utm_source = models.CharField(max_length=100, blank=True)
    utm_medium = models.CharField(max_length=100, blank=True)
    utm_campaign = models.CharField(max_length=100, blank=True)
    referrer = models.CharField(max_length=255, blank=True)
    page_url = models.CharField(max_length=500, blank=True)
    fbp = models.CharField(max_length=120, blank=True)
    fbc = models.CharField(max_length=500, blank=True)
    ttp = models.CharField(max_length=120, blank=True)
    ttclid = models.CharField(max_length=500, blank=True)
    landing = models.ForeignKey("storefront.Layout", null=True, blank=True, on_delete=models.SET_NULL, related_name="origins")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)


class PixelEvent(models.Model):
    class Platform(models.TextChoices):
        FACEBOOK = "facebook", "Facebook"
        TIKTOK = "tiktok", "TikTok"

    class Status(models.TextChoices):
        SENT = "sent", "وصل"
        FAILED = "failed", "فشل"

    order = models.ForeignKey("orders.Order", null=True, blank=True, on_delete=models.CASCADE, related_name="pixel_events")
    platform = models.CharField(max_length=10, choices=Platform.choices)
    event_name = models.CharField(max_length=40)
    event_id = models.CharField(max_length=60)
    value = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=Status.choices)
    is_test = models.BooleanField(default=False)
    detail = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class Layout(models.Model):
    class Kind(models.TextChoices):
        SITE = "site", "الشكل العام"
        HOME = "home", "الصفحة الرئيسية"
        LANDING = "landing", "صفحة هبوط"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    slug = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=120)
    store_product = models.ForeignKey(StoreProduct, null=True, blank=True, on_delete=models.SET_NULL, related_name="landings")
    lang = models.CharField(max_length=2, default="ar")
    draft = models.JSONField(default=dict)
    published = models.JSONField(null=True, blank=True)
    is_live = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["kind", "-created_at", "id"]

    def __str__(self):
        return self.name

    @property
    def has_changes(self):
        return self.published is None or self.draft != self.published


class LayoutVersion(models.Model):
    layout = models.ForeignKey(Layout, on_delete=models.CASCADE, related_name="versions")
    data = models.JSONField()
    created_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]


class DesignImage(models.Model):
    image = models.ImageField(upload_to="design/")
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]


class LandingVisit(models.Model):
    layout = models.ForeignKey(Layout, on_delete=models.CASCADE, related_name="visits")
    day = models.DateField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["layout", "day"], name="unique_landing_visit_day")]
