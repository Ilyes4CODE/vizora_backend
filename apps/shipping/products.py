from apps.catalog.models import Product
from apps.core.excel import normalize_text
from apps.core.exceptions import ApiError

from .maystro import MaystroError, get_client, is_connected
from .models import MaystroConfig, MaystroProduct


def number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def mirror(data):
    MaystroProduct.objects.update_or_create(
        maystro_id=str(data["id"]),
        defaults={
            "display_id": number(data.get("display_id")),
            "name": str(data.get("logistical_description") or "")[:255],
            "reference": str(data.get("product_id") or "")[:120],
            "deleted": bool(data.get("deleted")),
            "low_stock_level": number(data.get("low_stock_level")),
        },
    )


def same_product(item, name, reference):
    if item.get("deleted"):
        return False
    if normalize_text(item.get("logistical_description")) == normalize_text(name):
        return True
    return bool(reference) and normalize_text(item.get("product_id")) == normalize_text(reference)


def register_in_maystro(name, reference=None, description=None, low_stock_level=None):
    config = MaystroConfig.load()
    if not is_connected(config):
        raise ApiError("Maystro غير مربوط بعد. أضف مفتاحه في «الحماية والنسخ» ← «ربط Maystro» أو ألغِ «إنشاؤه في Maystro»", "maystro_not_configured")
    client = get_client(config)
    try:
        data = client.create_product(name, reference=reference, description=description, low_stock_level=low_stock_level)
        created = True
    except MaystroError as error:
        if error.status_code != 400:
            raise ApiError(f"لم يقبل Maystro إنشاء المنتج: {error.message}", error.code, 502) from error
        try:
            data = next((item for item in client.products() if same_product(item, name, reference)), None)
        except MaystroError as lookup:
            raise ApiError(f"لم يقبل Maystro إنشاء المنتج: {lookup.message}", lookup.code, 502) from lookup
        if data is None:
            raise ApiError(f"لم يقبل Maystro إنشاء المنتج: {error.message}", error.code, 502) from error
        created = False
    maystro_id = str(data["id"])
    owner = Product.objects.filter(courier_ref=maystro_id).first()
    if owner is not None:
        raise ApiError(f"يوجد في Maystro منتج بهذا الاسم مربوط بالمنتج «{owner.name}» في التطبيق", "maystro_product_taken")
    mirror(data)
    return maystro_id, created
