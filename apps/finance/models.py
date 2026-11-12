from django.conf import settings
from django.db import models


class Campaign(models.Model):
    class Platform(models.TextChoices):
        FACEBOOK = "facebook", "Facebook"
        INSTAGRAM = "instagram", "Instagram"
        TIKTOK = "tiktok", "TikTok"
        OTHER = "other", "أخرى"

    name = models.CharField(max_length=150, unique=True)
    platform = models.CharField(max_length=20, choices=Platform.choices)
    product = models.ForeignKey("catalog.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="campaigns")
    is_active = models.BooleanField(default=True)
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.name


class Expense(models.Model):
    class Category(models.TextChoices):
        ADS = "ads", "الإعلانات"
        SALARIES = "salaries", "الرواتب"
        PACKAGING = "packaging", "التغليف"
        OTHER = "other", "مصاريف أخرى"
        GOODS = "goods", "شراء بضاعة"

    date = models.DateField(db_index=True)
    title = models.CharField(max_length=200)
    category = models.CharField(max_length=20, choices=Category.choices)
    amount = models.PositiveIntegerField()
    campaign = models.ForeignKey(Campaign, null=True, blank=True, on_delete=models.SET_NULL, related_name="expenses")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]


class Settlement(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "غير مسددة"
        PAID = "paid", "مسددة"

    courier = models.CharField(max_length=30, default="maystro")
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    paid_on = models.DateField(null=True, blank=True)
    statement_uploaded_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_start", "-id"]


class StatementLine(models.Model):
    settlement = models.ForeignKey(Settlement, on_delete=models.CASCADE, related_name="statement_lines")
    tracking_number = models.CharField(max_length=60)
    amount = models.IntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["settlement", "tracking_number"], name="unique_statement_tracking")]
