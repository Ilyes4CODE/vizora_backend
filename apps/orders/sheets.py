import csv
import hashlib
import io
import re
import threading
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.catalog.models import Product
from apps.core.excel import normalize_text
from apps.core.exceptions import ApiError
from apps.core.models import Commune, Wilaya

from .models import DeliveryType, SheetRow, SheetSource, Source
from .services import clean_variant, create_order, delivery_fee_for, find_duplicate, normalize_phone, phone_problem

FETCH_TIMEOUT = 25
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 30000
PREVIEW_ROWS = 200
REQUIRED = ("customer_name", "phone", "wilaya")
ALIASES = {
    "customer_name": [
        "full name", "fullname", "name", "nom", "nom complet", "nom et prenom", "nom prenom", "client", "customer", "customer name",
        "client name", "الاسم", "الاسم الكامل", "اسم الزبون", "الزبون", "الاسم واللقب",
    ],
    "phone": ["phone", "phone number", "telephone", "tel", "mobile", "numero", "numero de telephone", "الهاتف", "رقم الهاتف", "الهاتف المحمول"],
    "wilaya": ["wilaya", "province", "state", "region", "الولاية", "ولاية"],
    "wilaya_code": ["wilaya code", "code wilaya", "province code", "province id", "province number", "wilaya id", "wilaya number", "رقم الولاية", "رمز الولاية"],
    "commune": ["commune", "city", "ville", "municipality", "البلدية", "بلدية", "المدينة"],
    "address": ["address", "adresse", "full address", "العنوان"],
    "product": ["product", "product title", "product name", "produit", "article", "item", "المنتج", "اسم المنتج"],
    "sku": ["sku", "product sku", "ref", "reference", "référence"],
    "variant": ["variant", "variante", "variant title", "option", "options", "size", "taille", "المقاس", "اللون", "الخيار"],
    "quantity": ["quantity", "qty", "quantite", "quantité", "الكمية"],
    "total": ["total", "total price", "prix total", "prix", "price", "amount", "montant", "المجموع", "السعر", "المبلغ", "السعر الإجمالي"],
    "delivery_fee": ["delivery price", "delivery fee", "shipping price", "shipping fee", "frais de livraison", "prix livraison", "سعر التوصيل", "ثمن التوصيل"],
    "delivery_type": ["delivery type", "type de livraison", "livraison", "delivery", "shipping type", "نوع التوصيل", "التوصيل"],
    "order_number": ["order number", "order n", "order no", "order id", "order", "n commande", "numero de commande", "commande", "رقم الطلب", "رقم الطلبية"],
    "note": ["note", "notes", "remarque", "commentaire", "ملاحظة", "ملاحظات"],
    "date": ["date", "date time", "datetime", "created at", "order date", "التاريخ"],
}
FIELD_LABELS = {
    "customer_name": "الاسم",
    "phone": "الهاتف",
    "wilaya": "الولاية",
    "wilaya_code": "رقم الولاية",
    "commune": "البلدية",
    "address": "العنوان",
    "product": "المنتج",
    "sku": "SKU",
    "variant": "المقاس / اللون",
    "quantity": "الكمية",
    "total": "المجموع",
    "delivery_fee": "سعر التوصيل",
    "delivery_type": "نوع التوصيل",
    "order_number": "رقم الطلب",
    "note": "ملاحظة",
    "date": "التاريخ",
}
PROBLEMS = {
    "missing_name": "الاسم ناقص",
    "missing_phone": "رقم الهاتف ناقص",
    "phone_short": "رقم الهاتف ناقص",
    "phone_foreign": "رقم غير جزائري",
    "unknown_wilaya": "الولاية غير معروفة",
    "unknown_product": "المنتج غير معروف: اختر «المنتج الافتراضي» في إعدادات الشيت أو اكتب اسم منتج موجود",
    "bad_quantity": "الكمية غير صالحة",
    "bad_total": "المبلغ غير صالح",
    "delivery_unavailable": "التوصيل غير متاح لهذه الولاية",
}
DESK_WORDS = {"desk", "stop desk", "stopdesk", "bureau", "point relais", "مكتب", "مكتب التوصيل"}
_running = threading.Lock()


ARABIC_LETTERS = str.maketrans({"ة": "ه", "ى": "ي", "ـ": ""})
WILAYA_ALIASES = {
    "عين صالح": 53, "in salah": 53, "ain salah": 53, "عين قزام": 54, "in guezzam": 54, "ain guezzam": 54, "تموشنت": 46,
    "تيموشنت": 46, "عين تمشونت": 46, "عين تيموشنت": 46, "ain temouchent": 46, "واد سوف": 39, "وادي سوف": 39, "سوف": 39, "الوادي": 39,
    "oued souf": 39, "el oued": 39, "برج بوعريرج": 34, "bba": 34, "سيدي بلعباس": 22, "سيدي بل عباس": 22, "sba": 22, "بوعريريج": 34,
    "تمنغست": 11, "تمنراست": 11, "مسيله": 28, "msila": 28, "ام البواقي": 4, "oeb": 4, "عين دفلى": 44, "عين الدفله": 44,
    "بشار": 8, "باجي مختار": 50, "bordj badji mokhtar": 50, "اولاد جلال": 51, "بني عباس": 52, "المنيعه": 58, "منيعه": 58, "el menia": 58,
    "المغير": 57, "مغير": 57, "el mghair": 57, "جانت": 56, "توقرت": 55, "تقرت": 55, "touggourt": 55, "تيميمون": 49, "تميمون": 49,
}


def compact(value):
    text = normalize_text(value).translate(ARABIC_LETTERS)
    words = re.split(r"[^0-9a-z؀-ۿ]+", text)
    return "".join(word[2:] if word.startswith("ال") and len(word) > 3 else word for word in words)


ALIAS_INDEX = {compact(alias): field for field, aliases in ALIASES.items() for alias in aliases}


def export_url(url):
    raw = str(url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme != "https" or parsed.hostname != "docs.google.com" or not parsed.path.startswith("/spreadsheets/"):
        raise ApiError("الصق رابط Google Sheets (يبدأ بـ https://docs.google.com/spreadsheets/)", "sheet_url_invalid")
    query = parse_qs(parsed.query)
    gid = (query.get("gid") or [None])[0] or (parse_qs(parsed.fragment).get("gid") or [None])[0]
    base = settings.GOOGLE_SHEETS_URL
    published = re.match(r"^/spreadsheets/d/e/([\w-]+)", parsed.path)
    if published:
        params = {"output": "tsv"}
        if gid:
            params.update(gid=gid, single="true")
        return f"{base}/spreadsheets/d/e/{published[1]}/pub?{urlencode(params)}"
    shared = re.match(r"^/spreadsheets/d/([\w-]+)", parsed.path)
    if shared:
        params = {"format": "tsv"}
        if gid:
            params["gid"] = gid
        return f"{base}/spreadsheets/d/{shared[1]}/export?{urlencode(params)}"
    raise ApiError("رابط Google Sheets غير مفهوم", "sheet_url_invalid")


def fetch_text(url):
    target = export_url(url)
    try:
        response = requests.get(target, timeout=FETCH_TIMEOUT, stream=True, headers={"User-Agent": "VizoraGlobal/1.0"})
    except requests.RequestException as error:
        raise ApiError("تعذّر الاتصال بـ Google Sheets، حاول بعد قليل", "sheet_unreachable", 502) from error
    with response:
        if response.status_code in (401, 403, 404):
            raise ApiError("لا يمكن قراءة الشيت: انشره على الويب (ملف ← مشاركة ← نشر على الويب) أو اجعل المشاركة «أي شخص لديه الرابط»", "sheet_not_shared")
        if response.status_code >= 400:
            raise ApiError(f"ردّ Google Sheets بخطأ ({response.status_code})", "sheet_unreachable", 502)
        content = b""
        for chunk in response.iter_content(65536):
            content += chunk
            if len(content) > MAX_BYTES:
                raise ApiError("الشيت كبير جداً", "sheet_too_large")
        kind = response.headers.get("Content-Type", "")
    text = content.decode("utf-8", errors="replace").lstrip("﻿")
    head = text[:200].lower()
    if "text/html" in kind or head.startswith("<!doctype") or head.startswith("<html"):
        raise ApiError("لا يمكن قراءة الشيت: انشره على الويب (ملف ← مشاركة ← نشر على الويب) أو اجعل المشاركة «أي شخص لديه الرابط»", "sheet_not_shared")
    return text


def parse_table(text):
    first = text.split("\n", 1)[0]
    if "\t" in first or "," not in first:
        rows = [line.rstrip("\r").split("\t") for line in text.split("\n")]
    else:
        rows = list(csv.reader(io.StringIO(text)))
    while rows and not any(cell.strip() for cell in rows[-1]):
        rows.pop()
    if len(rows) > MAX_ROWS:
        raise ApiError(f"الحد الأقصى {MAX_ROWS} سطر في الشيت", "sheet_too_large")
    return [[cell.strip() for cell in row] for row in rows]


def map_columns(header):
    mapping = {}
    for position, title in enumerate(header):
        field = ALIAS_INDEX.get(compact(title))
        if field and field not in mapping:
            mapping[field] = position
    return mapping


def find_header(rows):
    best = (0, 0)
    for index, row in enumerate(rows[:15]):
        score = len(map_columns(row))
        if score > best[1]:
            best = (index, score)
    return best[0]


def read_sheet(url):
    rows = parse_table(fetch_text(url))
    if not rows:
        raise ApiError("الشيت فارغ", "sheet_empty")
    header_index = find_header(rows)
    mapping = map_columns(rows[header_index])
    missing = [field for field in REQUIRED if field not in mapping and not (field == "wilaya" and "wilaya_code" in mapping)]
    if missing:
        names = "، ".join(FIELD_LABELS[field] for field in missing)
        raise ApiError(f"لم أجد أعمدة: {names}. سمِّ الأعمدة في السطر الأول كما في الشرح", "sheet_columns_missing")
    return {"rows": rows, "header_index": header_index, "mapping": mapping}


def parse_amount(value):
    text = str(value or "").strip()
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.\-]", "", text)
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif cleaned.count(",") == 1 and len(cleaned.split(",")[1]) <= 2:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return -1
    return int(amount.quantize(Decimal("1"))) if amount >= 0 else -1


def parse_quantity(value):
    text = str(value or "").strip()
    if not text:
        return 1
    try:
        quantity = int(float(text.replace(",", ".")))
    except ValueError:
        return None
    return quantity if 1 <= quantity <= 1000 else None


class Lookups:
    def __init__(self, source):
        self.source = source
        self.wilayas = {}
        for wilaya in Wilaya.objects.all():
            self.wilayas[str(wilaya.code)] = wilaya
            self.wilayas[str(wilaya.code).zfill(2)] = wilaya
            for name in (wilaya.name_ar, wilaya.name_fr):
                self.wilayas[compact(name)] = wilaya
        by_code = {wilaya.code: wilaya for wilaya in self.wilayas.values()}
        for name, code in WILAYA_ALIASES.items():
            if code in by_code:
                self.wilayas.setdefault(compact(name), by_code[code])
        self.products = {}
        for product in Product.objects.filter(is_active=True).select_related():
            self.products[compact(product.name)] = product
            if product.sku:
                self.products[compact(product.sku)] = product
        from apps.storefront.models import StoreProduct

        for item in StoreProduct.objects.select_related("product").filter(product__is_active=True):
            for name in (item.slug, item.title_ar, item.title_en):
                if name:
                    self.products.setdefault(compact(name), item.product)
        self.communes = {}
        for commune in Commune.objects.all():
            for name in (commune.name_ar, commune.name_fr):
                self.communes[(commune.wilaya_id, compact(name))] = commune.name_ar

    def wilaya(self, code, name):
        for value in (code, name):
            text = str(value or "").strip()
            if not text:
                continue
            if text.endswith(".0"):
                text = text[:-2]
            digits = re.match(r"^\s*(\d{1,2})\b", text)
            if digits and digits[1].lstrip("0") in self.wilayas:
                return self.wilayas[digits[1].lstrip("0")]
            for part in (text, re.sub(r"^\s*\d{1,2}\s*[-–.]\s*", "", text)):
                found = self.wilayas.get(compact(part))
                if found:
                    return found
        return None

    def product(self, name, sku):
        for value in (sku, name):
            found = self.products.get(compact(value)) if value else None
            if found:
                return found
        return self.source.default_product if self.source.default_product and self.source.default_product.is_active else None

    def commune(self, wilaya, name):
        return self.communes.get((wilaya.pk, compact(name)), name)


def row_key(values, mapping):
    def cell(field):
        return values[mapping[field]] if field in mapping and mapping[field] < len(values) else ""

    number = compact(cell("order_number"))
    if number:
        return f"n:{number}"[:80]
    parts = [cell("date"), cell("customer_name"), normalize_phone(cell("phone")), cell("wilaya"), cell("wilaya_code"), cell("product"), cell("sku"), cell("variant"), cell("quantity"), cell("total")]
    digest = hashlib.sha1("|".join(normalize_text(part) for part in parts).encode("utf-8")).hexdigest()
    return f"h:{digest}"


def price_lines(product, quantity, variant, amount):
    if amount is None:
        return [{"product": product, "quantity": quantity, "variant": variant}]
    base, extra = divmod(amount, quantity)
    lines = []
    if extra:
        lines.append({"product": product, "quantity": extra, "unit_price": base + 1, "variant": variant})
    if quantity - extra:
        lines.append({"product": product, "quantity": quantity - extra, "unit_price": base, "variant": variant})
    return lines


def read_row(values, mapping, lookups, row_number):
    def cell(field):
        return values[mapping[field]].strip() if field in mapping and mapping[field] < len(values) else ""

    problems = []
    name = cell("customer_name")
    if not name:
        problems.append("missing_name")
    raw_phone = cell("phone")
    if not raw_phone:
        problems.append("missing_phone")
    elif phone_problem(raw_phone):
        problems.append(phone_problem(raw_phone))
    wilaya = lookups.wilaya(cell("wilaya_code"), cell("wilaya"))
    if wilaya is None:
        problems.append("unknown_wilaya")
    product = lookups.product(cell("product"), cell("sku"))
    if product is None:
        problems.append("unknown_product")
    quantity = parse_quantity(cell("quantity"))
    if quantity is None:
        problems.append("bad_quantity")
    amount = parse_amount(cell("total"))
    fee_cell = parse_amount(cell("delivery_fee"))
    if amount == -1 or fee_cell == -1:
        problems.append("bad_total")
    summary = {
        "row": row_number,
        "name": name,
        "phone": raw_phone,
        "wilaya": wilaya.name_ar if wilaya else (cell("wilaya") or cell("wilaya_code")),
        "product": product.name if product else cell("product"),
        "quantity": cell("quantity") or "1",
        "total": cell("total"),
        "order_number": cell("order_number"),
    }
    if problems:
        return None, summary, problems
    delivery_type = DeliveryType.DESK if normalize_text(cell("delivery_type")) in DESK_WORDS else DeliveryType.HOME
    if delivery_type == DeliveryType.HOME and not wilaya.home_available and wilaya.desk_available:
        delivery_type = DeliveryType.DESK
    if delivery_type == DeliveryType.DESK and not wilaya.desk_available and wilaya.home_available:
        delivery_type = DeliveryType.HOME
    try:
        standard_fee = delivery_fee_for(wilaya, delivery_type)
    except (ValidationError, DjangoValidationError):
        return None, summary, ["delivery_unavailable"]
    fee = fee_cell if fee_cell is not None else standard_fee
    if amount is not None and lookups.source.total_includes_delivery:
        amount -= fee
        if amount < 0:
            return None, summary, ["bad_total"]
    variant = clean_variant(cell("variant"))
    notes = []
    if cell("order_number"):
        notes.append(f"رقم الطلب في الشيت: {cell('order_number')}")
    if cell("note"):
        notes.append(f"ملاحظة الشيت: {cell('note')}")
    data = {
        "customer_name": name[:150],
        "phone": normalize_phone(raw_phone),
        "wilaya": wilaya,
        "commune": lookups.commune(wilaya, cell("commune"))[:80],
        "address": cell("address")[:255],
        "delivery_type": delivery_type,
        "items": price_lines(product, quantity, variant, amount),
        "note": "\n".join(notes),
    }
    if fee_cell is not None:
        data["delivery_fee"] = fee_cell
    return data, summary, []


def preview(url):
    sheet = read_sheet(url)
    rows, header_index, mapping = sheet["rows"], sheet["header_index"], sheet["mapping"]
    source = SheetSource(name="preview")
    lookups = Lookups(source)
    data_rows = [(index + 1, values) for index, values in enumerate(rows) if index > header_index and any(values)]
    chosen = data_rows if len(data_rows) <= PREVIEW_ROWS else data_rows[: PREVIEW_ROWS // 2] + data_rows[-PREVIEW_ROWS // 2 :]
    lines = []
    for row_number, values in chosen:
        _, summary, problems = read_row(values, mapping, lookups, row_number)
        summary["problems"] = [PROBLEMS[code] for code in problems if code != "unknown_product"]
        summary["product_known"] = "unknown_product" not in problems
        lines.append(summary)
    header = rows[header_index]
    return {
        "header_row": header_index + 1,
        "columns": [{"title": header[position], "field": field, "label": FIELD_LABELS[field]} for field, position in sorted(mapping.items(), key=lambda pair: pair[1])],
        "unused": [title for position, title in enumerate(header) if title and position not in mapping.values()],
        "total_rows": len(data_rows),
        "first_row": data_rows[0][0] if data_rows else header_index + 2,
        "last_row": data_rows[-1][0] if data_rows else header_index + 1,
        "rows": lines,
    }


def sync_source(source, sheet=None):
    try:
        sheet = sheet or read_sheet(source.url)
    except ApiError as error:
        SheetSource.objects.filter(pk=source.pk).update(last_synced_at=timezone.now(), last_sync_ok=False, last_error=str(error.detail)[:255])
        raise
    rows, header_index, mapping = sheet["rows"], sheet["header_index"], sheet["mapping"]
    with transaction.atomic():
        source = SheetSource.objects.select_for_update().get(pk=source.pk)
        start_row = max(source.start_row, header_index + 2)
        baseline = source.baseline_row != source.start_row
        seen = set(source.rows.values_list("key", flat=True))
        lookups = Lookups(source)
        created = []
        duplicates = []
        waiting = []
        skipped = 0
        records = []
        for index in range(header_index + 1, len(rows)):
            values = rows[index]
            if not any(values):
                continue
            row_number = index + 1
            key = row_key(values, mapping)
            if key in seen:
                continue
            if baseline and row_number < start_row:
                records.append(SheetRow(source=source, key=key, row_number=row_number, status=SheetRow.Status.SKIPPED))
                seen.add(key)
                skipped += 1
                continue
            data, summary, problems = read_row(values, mapping, lookups, row_number)
            if problems:
                waiting.append({"row": row_number, "name": summary["name"], "problems": [PROBLEMS[code] for code in problems]})
                continue
            product_ids = [line["product"].pk for line in data["items"]]
            existing = find_duplicate(data["phone"], product_ids)
            if existing is not None:
                records.append(SheetRow(source=source, key=key, row_number=row_number, status=SheetRow.Status.DUPLICATE, order=existing))
                duplicates.append({"row": row_number, "name": summary["name"], "reference": existing.reference})
                seen.add(key)
                continue
            try:
                with transaction.atomic():
                    order = create_order(data, None, source=Source.SHEET, event_message=f"وصلت الطلبية من Google Sheet «{source.name}» · السطر {row_number}")
            except (ValidationError, DjangoValidationError, ApiError):
                waiting.append({"row": row_number, "name": summary["name"], "problems": [PROBLEMS["delivery_unavailable"]]})
                continue
            records.append(SheetRow(source=source, key=key, row_number=row_number, status=SheetRow.Status.IMPORTED, order=order))
            created.append({"row": row_number, "name": summary["name"], "reference": order.reference})
            seen.add(key)
        SheetRow.objects.bulk_create(records, ignore_conflicts=True)
        result = {
            "created": len(created),
            "duplicates": len(duplicates),
            "skipped": skipped,
            "waiting": waiting[:50],
            "waiting_count": len(waiting),
            "orders": created[-20:],
            "duplicate_rows": duplicates[-20:],
            "rows_in_sheet": max(len(rows) - header_index - 1, 0),
        }
        source.baseline_row = source.start_row
        source.last_synced_at = timezone.now()
        source.last_sync_ok = True
        source.last_error = ""
        source.last_result = result
        source.imported_count += len(created)
        source.save(update_fields=["baseline_row", "last_synced_at", "last_sync_ok", "last_error", "last_result", "imported_count"])
    return result


def due_sources(now=None):
    now = now or timezone.now()
    limit = now - timedelta(seconds=settings.SHEETS_SYNC_SECONDS)
    return SheetSource.objects.filter(is_active=True).filter(Q(last_synced_at__isnull=True) | Q(last_synced_at__lte=limit))


def sync_due():
    done = []
    for source in due_sources():
        try:
            result = sync_source(source)
        except ApiError as error:
            done.append(f"{source.name}: {error.detail}")
        except Exception:
            SheetSource.objects.filter(pk=source.pk).update(last_synced_at=timezone.now(), last_sync_ok=False, last_error="خطأ غير متوقع أثناء قراءة الشيت")
            done.append(f"{source.name}: error")
        else:
            done.append(f"{source.name}: +{result['created']}")
    return done


def sync_in_background():
    try:
        sync_due()
    finally:
        _running.release()
        connection.close()


def sync_sheets_soon():
    if not due_sources().exists():
        return False
    if settings.SHEETS_SYNC_INLINE:
        sync_due()
        return True
    if not _running.acquire(blocking=False):
        return False
    threading.Thread(target=sync_in_background, daemon=True).start()
    return True
