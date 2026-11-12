from django.contrib.auth.models import AbstractUser
from django.db import models

from apps.core.crypto import decrypt_text, encrypt_text


class Role(models.TextChoices):
    ADMIN = "admin", "مدير النظام"
    CONFIRMATION = "confirmation", "موظف تأكيد"
    STOCK = "stock", "مسؤول مخزون"
    ACCOUNTANT = "accountant", "محاسب"


class Section(models.TextChoices):
    ORDERS = "orders", "الطلبيات"
    CONFIRMATION = "confirmation", "التأكيد والسويفي"
    PRODUCTS = "products", "المنتجات والمخزون"
    SHIPPING = "shipping", "متابعة الشحن"
    ACCOUNTING = "accounting", "المحاسبة"
    DUES = "dues", "مستحقات التوصيل"
    CAMPAIGNS = "campaigns", "الحملات الإعلانية"
    REPORTS = "reports", "التقارير"
    TEAM = "team", "الموظفون والصلاحيات"
    SECURITY = "security", "الحماية والنسخ"


class User(AbstractUser):
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CONFIRMATION)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    totp_secret = models.CharField(max_length=255, blank=True)
    totp_enabled = models.BooleanField(default=False)
    session_version = models.PositiveIntegerField(default=0)

    REQUIRED_FIELDS = ["full_name"]

    def save(self, *args, **kwargs):
        if self.is_superuser:
            self.role = Role.ADMIN
        super().save(*args, **kwargs)

    @property
    def is_admin_role(self):
        return self.role == Role.ADMIN

    def has_section(self, section):
        if self.is_admin_role:
            return True
        if not hasattr(self, "_allowed_sections"):
            self._allowed_sections = set(
                RolePermission.objects.filter(role=self.role, allowed=True).values_list("section", flat=True)
            )
        return section in self._allowed_sections

    def allowed_sections(self):
        return [value for value in Section.values if self.has_section(value)]

    def set_totp_secret(self, secret):
        self.totp_secret = encrypt_text(secret)

    def get_totp_secret(self):
        return decrypt_text(self.totp_secret)


class RolePermission(models.Model):
    role = models.CharField(max_length=20, choices=Role.choices)
    section = models.CharField(max_length=20, choices=Section.choices)
    allowed = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["role", "section"], name="unique_role_section")]


class Device(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="devices")
    device_id = models.CharField(max_length=120)
    name = models.CharField(max_length=120, blank=True)
    approved = models.BooleanField(default=False)
    last_seen_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-last_seen_at"]
        constraints = [models.UniqueConstraint(fields=["user", "device_id"], name="unique_user_device")]
