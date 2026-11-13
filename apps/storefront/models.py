from django.db import models
from django.utils import timezone

from apps.core.models import SingletonModel


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
    orders_per_hour = models.PositiveSmallIntegerField(default=5)
    updated_at = models.DateTimeField(auto_now=True)


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
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
