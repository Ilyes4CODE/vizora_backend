from django.db import transaction

from apps.catalog.models import Product
from apps.core.excel import match_column, normalize_text
from apps.core.models import Wilaya
from apps.finance.models import Campaign

from .models import DeliveryType, ImportBatch, Source
from .services import create_order, find_duplicate, normalize_phone, phone_problem

FIELD_ALIASES = {
    "customer_name": ["nom complet", "nom", "name", "full name", "client", "customer", "الاسم", "اسم الزبون", "الزبون"],
    "phone": ["telephone", "tel", "phone", "mobile", "numero", "الهاتف", "رقم الهاتف"],
    "wilaya": ["wilaya", "state", "الولاية"],
    "commune": ["commune", "city", "البلدية"],
    "address": ["adresse", "address", "العنوان"],
    "product": ["produit", "product", "article", "المنتج"],
    "quantity": ["quantite", "qty", "quantity", "الكمية"],
    "delivery_type": ["livraison", "type de livraison", "delivery", "delivery_type", "نوع التوصيل", "التوصيل"],
    "campaign": ["campagne", "campaign", "الحملة"],
    "note": ["note", "remarque", "ملاحظة"],
}
REQUIRED_FIELDS = ("customer_name", "phone", "wilaya", "product")
PROBLEM_LABELS = {
    "missing_name": "الاسم ناقص",
    "phone_short": "رقم ناقص",
    "phone_foreign": "رقم غير جزائري",
    "unknown_wilaya": "ولاية غير معروفة",
    "unknown_product": "منتج غير معروف",
    "bad_quantity": "كمية غير صالحة",
    "duplicate": "طلبية مكررة",
}
DESK_WORDS = {"desk", "stop desk", "stopdesk", "bureau", "مكتب", "مكتب التوصيل"}


def suggest_mapping(columns):
    return {field: column for field, aliases in FIELD_ALIASES.items() if (column := match_column(columns, aliases))}


def wilaya_index():
    index = {}
    for wilaya in Wilaya.objects.all():
        index[str(wilaya.code)] = wilaya
        index[normalize_text(wilaya.name_ar)] = wilaya
        index[normalize_text(wilaya.name_fr)] = wilaya
    return index


def product_index():
    index = {}
    for product in Product.objects.filter(is_active=True):
        index[normalize_text(product.name)] = product
        if product.sku:
            index[normalize_text(product.sku)] = product
    return index


def resolve_wilaya(value, index):
    text = normalize_text(value)
    if text.endswith(".0"):
        text = text[:-2]
    if text in index:
        return index[text]
    head = text.split("-")[0].strip()
    return index.get(head)


def parse_quantity(value):
    if value in (None, ""):
        return 1
    try:
        quantity = int(float(value))
    except (TypeError, ValueError):
        return None
    return quantity if quantity >= 1 else None


def evaluate(batch, mapping):
    positions = {field: batch.columns.index(column) for field, column in mapping.items() if column in batch.columns}
    wilayas = wilaya_index()
    products = product_index()
    campaigns = {normalize_text(c.name): c for c in Campaign.objects.all()}
    valid = []
    issues = []
    duplicates = 0
    invalid = 0
    seen = set()

    def cell(row, field):
        return row[positions[field]] if field in positions else None

    for offset, row in enumerate(batch.rows):
        if not any(value not in (None, "") for value in row):
            continue
        line = offset + 2
        problems = []
        name = str(cell(row, "customer_name") or "").strip()
        if not name:
            problems.append(("missing_name", ""))
        raw_phone = cell(row, "phone")
        problem = phone_problem(raw_phone)
        if problem:
            problems.append((problem, str(raw_phone or "")))
        wilaya = resolve_wilaya(cell(row, "wilaya"), wilayas)
        if wilaya is None:
            problems.append(("unknown_wilaya", str(cell(row, "wilaya") or "")))
        product = products.get(normalize_text(cell(row, "product")))
        if product is None:
            problems.append(("unknown_product", str(cell(row, "product") or "")))
        quantity = parse_quantity(cell(row, "quantity"))
        if quantity is None:
            problems.append(("bad_quantity", str(cell(row, "quantity") or "")))
        if problems:
            invalid += 1
            for code, value in problems:
                issues.append({"line": line, "code": code, "problem": PROBLEM_LABELS[code], "value": value})
            continue
        phone = normalize_phone(raw_phone)
        key = (phone, product.pk)
        if key in seen or find_duplicate(phone, [product.pk]) is not None:
            duplicates += 1
            issues.append({"line": line, "code": "duplicate", "problem": PROBLEM_LABELS["duplicate"], "value": phone})
            continue
        seen.add(key)
        delivery_word = normalize_text(cell(row, "delivery_type"))
        valid.append(
            {
                "customer_name": name,
                "phone": phone,
                "wilaya": wilaya,
                "commune": str(cell(row, "commune") or "").strip(),
                "address": str(cell(row, "address") or "").strip(),
                "delivery_type": DeliveryType.DESK if delivery_word in DESK_WORDS else DeliveryType.HOME,
                "items": [{"product": product, "quantity": quantity}],
                "campaign": campaigns.get(normalize_text(cell(row, "campaign"))),
                "note": str(cell(row, "note") or "").strip(),
            }
        )
    summary = {"valid": len(valid), "duplicates": duplicates, "invalid": invalid, "issues": issues}
    return valid, summary


@transaction.atomic
def commit_batch(batch, user):
    valid, summary = evaluate(batch, batch.mapping)
    for data in valid:
        create_order(data, user, source=Source.EXCEL, event_message="وصلت الطلبية من استيراد Excel")
    batch.status = ImportBatch.Status.COMMITTED
    batch.created_count = len(valid)
    batch.result = summary
    batch.save(update_fields=["status", "created_count", "result"])
    return batch
