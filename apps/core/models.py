from django.conf import settings
from django.db import models


def default_working_hours():
    return {
        "sunday": [["08:30", "14:00"], ["16:30", "20:30"]],
        "monday": [],
        "tuesday": [],
        "wednesday": [],
        "thursday": [],
        "friday": [],
        "saturday": [],
    }


class SingletonModel(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        instance, _ = cls.objects.get_or_create(pk=1)
        return instance


class Wilaya(models.Model):
    code = models.PositiveSmallIntegerField(primary_key=True)
    name_ar = models.CharField(max_length=60)
    name_fr = models.CharField(max_length=60)
    home_fee = models.PositiveIntegerField(default=600)
    desk_fee = models.PositiveIntegerField(default=400)
    home_available = models.BooleanField(default=True)
    desk_available = models.BooleanField(default=True)
    courier_ref = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name_ar}"


class Commune(models.Model):
    wilaya = models.ForeignKey(Wilaya, on_delete=models.CASCADE, related_name="communes")
    name_ar = models.CharField(max_length=80)
    name_fr = models.CharField(max_length=80, blank=True)
    courier_ref = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["name_ar"]
        constraints = [
            models.UniqueConstraint(fields=["wilaya", "name_ar"], name="unique_commune_per_wilaya")
        ]

    def __str__(self):
        return self.name_ar


class BusinessProfile(SingletonModel):
    name = models.CharField(max_length=120, default="Vizora Global")
    phone = models.CharField(max_length=30, default="+213 667 27 76 76")
    email = models.EmailField(default="Abd.allah.yt123456@gmail.com")
    address = models.CharField(max_length=200, default="الحميز، الدار البيضاء، الجزائر")
    facebook = models.CharField(max_length=120, default="Vizora Globab")
    instagram = models.CharField(max_length=120, default="@vizora_global")
    activity = models.TextField(
        default="تسويق وترويج · تفعيل بطاقات الفيزا · شحن الألعاب والتطبيقات والاشتراكات"
    )
    working_hours = models.JSONField(default=default_working_hours)


class SecuritySettings(SingletonModel):
    two_factor_required = models.BooleanField(default=False)
    auto_logout_enabled = models.BooleanField(default=True)
    auto_logout_minutes = models.PositiveSmallIntegerField(default=30)
    approved_devices_only = models.BooleanField(default=False)


class AuditLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_entries")
    user_name = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=60, db_index=True)
    message = models.CharField(max_length=255)
    target_type = models.CharField(max_length=40, blank=True)
    target_id = models.CharField(max_length=40, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class Backup(models.Model):
    class Kind(models.TextChoices):
        AUTO = "auto", "تلقائية"
        MANUAL = "manual", "يدوية"
        PRE_RESTORE = "pre_restore", "قبل الاسترجاع"

    file_name = models.CharField(max_length=120, unique=True)
    size_bytes = models.PositiveBigIntegerField()
    kind = models.CharField(max_length=20, choices=Kind.choices)
    created_by_name = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
