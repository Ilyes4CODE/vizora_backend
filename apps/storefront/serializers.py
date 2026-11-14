import re
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils.text import slugify
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework import serializers

from .catalog import available_units
from .models import ContactMessage, Review, StockAlert, StoreImage, StoreOffer, StorePage, StoreProduct, StoreSettings

MAX_IMAGE_SIDE = 1400
MAX_IMAGE_BYTES = 8 * 1024 * 1024
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def public_base():
    if settings.STORE_PUBLIC_URL:
        return settings.STORE_PUBLIC_URL
    host = settings.STORE_HOSTS[0] if settings.STORE_HOSTS else "vizorawear.com"
    return f"https://{host}"


FACEBOOK_PIXEL = re.compile(r"^\d{10,20}$")
TIKTOK_PIXEL = re.compile(r"^[A-Z0-9]{10,30}$")
GOOGLE_ID = re.compile(r"^G-[A-Z0-9]{4,20}$")
TEST_CODE = re.compile(r"^[A-Za-z0-9_-]{3,40}$")
MIN_TOKEN = 20


class StoreSettingsSerializer(serializers.ModelSerializer):
    store_url = serializers.SerializerMethodField()
    facebook_token = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=600, trim_whitespace=True)
    tiktok_token = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=600, trim_whitespace=True)
    facebook_token_set = serializers.SerializerMethodField()
    tiktok_token_set = serializers.SerializerMethodField()

    class Meta:
        model = StoreSettings
        fields = [
            "is_open", "whatsapp", "facebook_url", "instagram_url", "tiktok_url", "announcement_ar", "announcement_en",
            "delivery_days_ar", "delivery_days_en", "facebook_pixel", "tiktok_pixel", "google_analytics",
            "facebook_token", "facebook_token_set", "facebook_test_code", "tiktok_token", "tiktok_token_set", "tiktok_test_code",
            "purchase_moment", "orders_per_hour", "updated_at", "store_url",
        ]
        read_only_fields = ["updated_at", "store_url"]

    def get_store_url(self, obj):
        return public_base()

    def get_facebook_token_set(self, obj):
        return bool(obj.encrypted_facebook_token)

    def get_tiktok_token_set(self, obj):
        return bool(obj.encrypted_tiktok_token)

    def validate_orders_per_hour(self, value):
        if value < 1 or value > 100:
            raise serializers.ValidationError("اختر رقماً بين 1 و 100")
        return value

    def validate_facebook_pixel(self, value):
        value = "".join(value.split())
        if value and not FACEBOOK_PIXEL.fullmatch(value):
            raise serializers.ValidationError("معرّف بيكسل فيسبوك أرقام فقط، عادة 15 أو 16 رقماً")
        return value

    def validate_tiktok_pixel(self, value):
        value = "".join(value.split()).upper()
        if value and not TIKTOK_PIXEL.fullmatch(value):
            raise serializers.ValidationError("معرّف بيكسل تيك توك حروف لاتينية وأرقام، مثل CABC123XYZ456")
        return value

    def validate_google_analytics(self, value):
        value = "".join(value.split()).upper()
        if value and not GOOGLE_ID.fullmatch(value):
            raise serializers.ValidationError("معرّف Google Analytics يبدأ بـ G- مثل G-ABC123XYZ")
        return value

    def validate_token(self, value):
        value = "".join(value.split())
        if value and len(value) < MIN_TOKEN:
            raise serializers.ValidationError("الرمز قصير، انسخه كاملاً")
        return value

    def validate_facebook_token(self, value):
        return self.validate_token(value)

    def validate_tiktok_token(self, value):
        return self.validate_token(value)

    def validate_test_code(self, value):
        value = value.strip()
        if value and not TEST_CODE.fullmatch(value):
            raise serializers.ValidationError("رمز الاختبار حروف لاتينية وأرقام فقط، مثل TEST12345")
        return value

    def validate_facebook_test_code(self, value):
        return self.validate_test_code(value)

    def validate_tiktok_test_code(self, value):
        return self.validate_test_code(value)

    def update(self, instance, validated_data):
        for key in ("facebook_token", "tiktok_token"):
            if key in validated_data:
                setattr(instance, key, validated_data.pop(key))
        return super().update(instance, validated_data)


class StoreOfferSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreOffer
        fields = ["id", "store_product", "title_ar", "title_en", "details_ar", "details_en", "price", "compare_price", "is_active", "sort_order"]

    def validate_price(self, value):
        if value < 1:
            raise serializers.ValidationError("السعر يجب أن يكون أكبر من صفر")
        return value

    def get_fields(self):
        fields = super().get_fields()
        if self.instance is not None:
            fields["store_product"].read_only = True
        return fields


class StoreImageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = StoreImage
        fields = ["id", "url", "width", "height", "alt_ar", "alt_en", "sort_order"]
        read_only_fields = ["id", "url", "width", "height"]

    def get_url(self, obj):
        if not obj.image:
            return ""
        request = self.context.get("request")
        return request.build_absolute_uri(obj.image.url) if request is not None else f"{public_base()}{obj.image.url}"


class ImageUploadSerializer(serializers.Serializer):
    image = serializers.ImageField()
    alt_ar = serializers.CharField(required=False, allow_blank=True, max_length=150)
    alt_en = serializers.CharField(required=False, allow_blank=True, max_length=150)

    def validate_image(self, value):
        if value.size > MAX_IMAGE_BYTES:
            raise serializers.ValidationError("حجم الصورة كبير، الحد 8 ميغابايت")
        return value


def optimized_image(upload, stem):
    try:
        picture = Image.open(upload)
        picture = ImageOps.exif_transpose(picture)
    except (UnidentifiedImageError, OSError):
        raise serializers.ValidationError({"image": "الملف ليس صورة صالحة"})
    picture = picture.convert("RGBA") if picture.mode in ("P", "LA", "RGBA") else picture.convert("RGB")
    picture.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.LANCZOS)
    output = BytesIO()
    picture.save(output, "WEBP", quality=82, method=6)
    return ContentFile(output.getvalue(), name=f"{stem}.webp"), picture.size


class StoreProductSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_price = serializers.IntegerField(source="product.price", read_only=True)
    offers = StoreOfferSerializer(many=True, read_only=True)
    images = StoreImageSerializer(many=True, read_only=True)
    url = serializers.SerializerMethodField()
    available = serializers.SerializerMethodField()

    class Meta:
        model = StoreProduct
        fields = [
            "id", "product", "product_name", "product_price", "slug", "is_published", "is_featured", "sort_order",
            "title_ar", "title_en", "summary_ar", "summary_en", "description_ar", "description_en", "badge_ar", "badge_en",
            "compare_price", "max_quantity", "offers", "images", "url", "available", "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def get_fields(self):
        fields = super().get_fields()
        if self.instance is not None and not isinstance(self.instance, (list, tuple)) and hasattr(self.instance, "pk"):
            fields["product"].read_only = True
        fields["slug"].required = False
        return fields

    def get_url(self, obj):
        return f"{public_base()}/p/{obj.slug}/"

    def get_available(self, obj):
        return available_units(obj.product)

    def validate_slug(self, value):
        value = value.strip().lower()
        if not SLUG_PATTERN.fullmatch(value):
            raise serializers.ValidationError("الرابط يقبل الحروف اللاتينية الصغيرة والأرقام والشرطة فقط")
        return value

    def validate_max_quantity(self, value):
        if value < 1 or value > 20:
            raise serializers.ValidationError("اختر رقماً بين 1 و 20")
        return value

    def validate(self, attrs):
        product = attrs.get("product") or (self.instance.product if self.instance else None)
        if self.instance is None and not attrs.get("slug"):
            base = slugify(attrs.get("title_en") or "") or f"product-{product.pk if product else 'new'}"
            slug = base
            index = 2
            while StoreProduct.objects.filter(slug=slug).exists():
                slug = f"{base}-{index}"
                index += 1
            attrs["slug"] = slug
        publishing = attrs.get("is_published", self.instance.is_published if self.instance else False)
        if publishing and product is not None:
            has_offers = self.instance is not None and self.instance.offers.filter(is_active=True).exists()
            if not has_offers and product.price < 1:
                raise serializers.ValidationError({"is_published": "حدّد سعر المنتج أو أضف عرضاً قبل نشره في الموقع"})
        return attrs


class StorePageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = StorePage
        fields = ["id", "slug", "title_ar", "title_en", "body_ar", "body_en", "show_in_footer", "sort_order", "updated_at", "url"]
        read_only_fields = ["slug", "updated_at", "url"]

    def get_url(self, obj):
        return f"{public_base()}/page/{obj.slug}/"


class ReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ["id", "name", "wilaya", "text", "rating", "is_published", "sort_order", "created_at"]
        read_only_fields = ["created_at"]

    def validate_rating(self, value):
        if value < 1 or value > 5:
            raise serializers.ValidationError("التقييم من 1 إلى 5")
        return value


class ContactMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactMessage
        fields = ["id", "name", "phone", "message", "lang", "handled", "handled_at", "created_at"]
        read_only_fields = ["id", "name", "phone", "message", "lang", "handled_at", "created_at"]


class StockAlertSerializer(serializers.ModelSerializer):
    product_title = serializers.CharField(source="store_product.title_ar", read_only=True)

    class Meta:
        model = StockAlert
        fields = ["id", "store_product", "product_title", "phone", "lang", "notified", "created_at"]
        read_only_fields = ["id", "store_product", "product_title", "phone", "lang", "created_at"]
