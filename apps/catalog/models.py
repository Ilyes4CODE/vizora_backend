from django.conf import settings
from django.db import models


class StockSource(models.TextChoices):
    MAYSTRO = "maystro", "مخازن Maystro"
    LOCAL = "local", "عندي"


class Product(models.Model):
    name = models.CharField(max_length=150, unique=True)
    sku = models.CharField(max_length=60, unique=True, null=True, blank=True)
    price = models.PositiveIntegerField()
    cost = models.PositiveIntegerField(default=0)
    on_hand = models.PositiveIntegerField(default=0, editable=False)
    low_stock_threshold = models.PositiveIntegerField(default=50)
    courier_ref = models.CharField(max_length=60, blank=True)
    stock_source = models.CharField(max_length=10, choices=StockSource.choices, default=StockSource.MAYSTRO)
    image = models.ImageField(upload_to="products/", null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.name


class StockMovement(models.Model):
    class Kind(models.TextChoices):
        IN = "in", "إدخال مخزون"
        SHIP = "ship", "شحن عبر Maystro"
        RETURN = "return", "مرتجع سليم"
        ADJUST = "adjust", "تعديل جرد"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="movements")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    quantity = models.IntegerField()
    balance_after = models.PositiveIntegerField()
    note = models.CharField(max_length=255, blank=True)
    order_reference = models.CharField(max_length=30, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
