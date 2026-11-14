import re
import secrets

from django.utils.dateparse import parse_datetime

from .templatetags.store import illustration
from .text import LANGUAGES, text_for

SHORT = 200
LONG = 3000
MAX_SECTIONS = 30
MAX_LINK = 300
IMAGE_PREFIXES = ("/media/design/", "/media/products/", "/static/storefront/")
IMAGE_PATTERN = re.compile(r"^[\w\-./]+(\?v=[0-9a-f]{1,16})?$")
LINK_PATTERN = re.compile(r"^(#[\w-]{1,40}|/[\w\-./#?=&%]*|https://[\w\-.]+(/[\w\-./#?=&%:@+~]*)?)$")
YOUTUBE = re.compile(r"^https://(www\.|m\.)?(youtube\.com/(watch\?v=|shorts/|embed/)|youtu\.be/)([\w-]{6,20})")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
ANCHOR = re.compile(r"^[a-z0-9-]{0,30}$")
SECTION_ID = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
RESERVED_SLUGS = {"site", "home", "new", "preview"}

PALETTES = {
    "vizora": {"label": "أزرق Vizora", "primary": "#1F6BFF", "dark": "#0A1A3A"},
    "emerald": {"label": "أخضر", "primary": "#0E9F6E", "dark": "#06281F"},
    "royal": {"label": "بنفسجي", "primary": "#7C3AED", "dark": "#1C1240"},
    "sunset": {"label": "برتقالي", "primary": "#E8590C", "dark": "#2B1208"},
    "rose": {"label": "وردي", "primary": "#E11D48", "dark": "#2A0A14"},
    "graphite": {"label": "رمادي داكن", "primary": "#2563EB", "dark": "#111827"},
}
FONTS = {
    "plex": {"label": "IBM Plex و Cairo", "body": '"IBM Plex Sans Arabic"', "head": '"Cairo"', "google": ""},
    "tajawal": {"label": "Tajawal", "body": '"Tajawal"', "head": '"Tajawal"', "google": "family=Tajawal:wght@400;500;700;800"},
    "almarai": {"label": "Almarai", "body": '"Almarai"', "head": '"Almarai"', "google": "family=Almarai:wght@400;700;800"},
    "noto": {
        "label": "Noto Kufi و Noto Sans",
        "body": '"Noto Sans Arabic"',
        "head": '"Noto Kufi Arabic"',
        "google": "family=Noto+Kufi+Arabic:wght@600;700;800&family=Noto+Sans+Arabic:wght@400;500;600",
    },
}
RADII = {"round": "18px", "soft": "10px", "square": "4px"}


class DesignError(Exception):
    def __init__(self, message, path=""):
        super().__init__(message)
        self.message = message
        self.path = path


def field(key, kind, label, **extra):
    return {"key": key, "kind": kind, "label": label, **extra}


def text(key, label, **extra):
    return field(key, "text", label, **extra)


def long(key, label, **extra):
    return field(key, "long", label, **extra)


def image(key="image", label="الصورة", **extra):
    return field(key, "image", label, **extra)


def link(key, label, **extra):
    return field(key, "link", label, **extra)


def items(key, label, fields, minimum=0, maximum=8, item_label="عنصر"):
    return field(key, "list", label, fields=fields, min=minimum, max=maximum, item_label=item_label)


HEAD = [text("title", "العنوان"), long("text", "النص تحت العنوان")]
BUTTON = [text("button_label", "نص الزر"), link("button_link", "رابط الزر")]

SECTIONS = {
    "hero": {
        "label": "شرائح الواجهة",
        "description": "الصور الكبيرة أعلى الصفحة الرئيسية، تتبدل تلقائياً",
        "kinds": ("home",),
        "fields": [
            field("show_ticks", "bool", "إظهار مزايا الدفع والتوصيل تحت الشريحة الأولى"),
            items(
                "slides",
                "الشرائح",
                [
                    text("eyebrow", "سطر صغير فوق العنوان"),
                    text("title", "العنوان"),
                    long("text", "النص"),
                    *BUTTON,
                    text("button2_label", "نص الزر الثاني"),
                    link("button2_link", "رابط الزر الثاني"),
                    image(),
                ],
                1,
                5,
                "شريحة",
            ),
        ],
    },
    "features": {
        "label": "مزايا أو خدمات",
        "description": "بطاقات بصورة وعنوان ونص",
        "kinds": ("home", "landing"),
        "fields": [
            *HEAD,
            field("shapes", "bool", "خلفية الأشكال ثلاثية الأبعاد"),
            field("columns", "select", "عدد الأعمدة", options=[["2", "2"], ["3", "3"], ["4", "4"]]),
            items("items", "البطاقات", [text("title", "العنوان"), long("text", "النص"), image()], 1, 8, "بطاقة"),
        ],
    },
    "products": {
        "label": "شبكة المنتجات",
        "description": "المنتجات المنشورة مع السعر والمخزون",
        "kinds": ("home", "landing"),
        "fields": [*HEAD, field("limit", "number", "عدد المنتجات", min=1, max=12), field("show_all_link", "bool", "رابط «كل المنتجات»")],
    },
    "steps": {
        "label": "خطوات الطلب",
        "description": "خطوات مرقّمة",
        "kinds": ("home", "landing"),
        "fields": [*HEAD, items("items", "الخطوات", [text("title", "العنوان"), long("text", "النص"), image()], 1, 6, "خطوة")],
    },
    "promo": {
        "label": "شريط ترويجي داكن",
        "description": "عنوان كبير وزر وصورة على خلفية داكنة",
        "kinds": ("home", "landing"),
        "fields": [text("eyebrow", "سطر صغير فوق العنوان"), *HEAD, *BUTTON, image()],
    },
    "banner": {
        "label": "صورة عريضة",
        "description": "صورة كبيرة مع نص وزر اختياريين",
        "kinds": ("home", "landing"),
        "fields": [image(), *HEAD, *BUTTON, field("dark", "bool", "نص فاتح فوق الصورة")],
    },
    "text": {
        "label": "نص حر",
        "description": "عنوان وفقرات",
        "kinds": ("home", "landing"),
        "fields": [text("title", "العنوان"), long("body", "النص"), field("align", "select", "المحاذاة", options=[["start", "إلى البداية"], ["center", "في الوسط"]])],
    },
    "video": {
        "label": "فيديو YouTube",
        "description": "فيديو من YouTube أو Shorts",
        "kinds": ("home", "landing"),
        "fields": [*HEAD, field("url", "video", "رابط الفيديو")],
    },
    "trust": {
        "label": "شارات الثقة",
        "description": "أيقونات صغيرة مع جملة قصيرة",
        "kinds": ("home", "landing"),
        "fields": [items("items", "الشارات", [text("title", "النص"), image()], 1, 6, "شارة")],
    },
    "countdown": {
        "label": "عدّاد تنازلي",
        "description": "يعدّ حتى تاريخ حقيقي، ويختفي أو يعرض رسالة بعده",
        "kinds": ("home", "landing"),
        "fields": [*HEAD, field("ends_at", "datetime", "ينتهي في"), text("ended_text", "الرسالة بعد الانتهاء (فارغ = إخفاء القسم)")],
    },
    "reviews": {
        "label": "آراء الزبائن",
        "description": "الآراء المنشورة من «آراء الزبائن»",
        "kinds": ("home", "landing"),
        "fields": [*HEAD, field("limit", "number", "عدد الآراء", min=1, max=12)],
    },
    "faq": {
        "label": "الأسئلة الشائعة",
        "description": "أسئلة وأجوبة قابلة للطي",
        "kinds": ("home", "landing"),
        "fields": [text("title", "العنوان"), items("items", "الأسئلة", [text("question", "السؤال"), long("answer", "الجواب")], 1, 15, "سؤال")],
    },
    "contact": {
        "label": "تواصل معنا",
        "description": "استمارة الرسائل وبطاقة الاتصال",
        "kinds": ("home",),
        "fields": [text("title", "عنوان الاستمارة"), image("image", "صورة بطاقة الاتصال")],
    },
    "product_hero": {
        "label": "واجهة المنتج",
        "description": "العنوان والسعر وصور المنتج وزر الطلب",
        "kinds": ("landing",),
        "fields": [
            text("badge", "شارة صغيرة"),
            text("title", "العنوان (فارغ = اسم المنتج)"),
            long("text", "النص"),
            image("image", "الصورة (فارغ = صورة المنتج)"),
            text("button_label", "نص الزر"),
            field("show_trust", "bool", "إظهار الدفع عند الاستلام والتوصيل"),
        ],
    },
    "gallery": {
        "label": "معرض صور",
        "description": "صور إضافية (فارغ = صور المنتج)",
        "kinds": ("home", "landing"),
        "fields": [text("title", "العنوان"), items("images", "الصور", [image(), text("caption", "تعليق")], 0, 12, "صورة")],
    },
    "before_after": {
        "label": "قبل وبعد",
        "description": "صورتان جنباً إلى جنب",
        "kinds": ("home", "landing"),
        "fields": [
            *HEAD,
            image("before_image", "صورة «قبل»"),
            text("before_label", "عنوان «قبل»"),
            image("after_image", "صورة «بعد»"),
            text("after_label", "عنوان «بعد»"),
        ],
    },
    "guarantee": {
        "label": "الضمان",
        "description": "وعد واضح للزبون مع صورة",
        "kinds": ("home", "landing"),
        "fields": [*HEAD, image()],
    },
    "order_form": {
        "label": "استمارة الطلب",
        "description": "الاسم والهاتف والولاية والبلدية والعنوان، الدفع عند الاستلام",
        "kinds": ("landing",),
        "single": True,
        "fields": [*HEAD, text("button_label", "نص زر التأكيد")],
    },
}

SITE_FIELDS = {
    "theme": [
        field("palette", "select", "الألوان", options=[[key, value["label"]] for key, value in PALETTES.items()] + [["custom", "ألوان خاصة"]]),
        field("primary", "color", "اللون الرئيسي (الأزرار والروابط)"),
        field("dark", "color", "اللون الداكن (الخلفيات)"),
        field("font", "select", "الخط العربي", options=[[key, value["label"]] for key, value in FONTS.items()]),
        field("radius", "select", "الزوايا", options=[["round", "دائرية"], ["soft", "خفيفة"], ["square", "حادة"]]),
        image("logo", "الشعار (فارغ = الشعار الحالي)"),
    ],
    "header": [
        field("show_topbar", "bool", "الشريط العلوي (الهاتف وأوقات العمل)"),
        text("topbar_text", "نص الشريط العلوي"),
        text("cta_label", "نص زر الطلب في الأعلى"),
        link("cta_link", "رابط زر الطلب"),
    ],
    "menu": [items("links", "روابط القائمة", [text("label", "النص"), link("link", "الرابط")], 1, 8, "رابط")],
    "footer": [
        long("about", "نبذة في أسفل الموقع"),
        items(
            "columns",
            "أعمدة الروابط",
            [text("title", "عنوان العمود"), items("links", "الروابط", [text("label", "النص"), link("link", "الرابط")], 0, 8, "رابط")],
            0,
            3,
            "عمود",
        ),
        field("show_policies", "bool", "عمود السياسات"),
    ],
}

PAGE_FIELDS = {
    "home": [],
    "landing": [
        field("hide_menu", "bool", "إخفاء القائمة والسلة (ينصح به للإعلانات)"),
        field("sticky_bar", "bool", "زر «اطلب الآن» ثابت أسفل الهاتف"),
        text("seo_title", "عنوان الصفحة في Google وفيسبوك"),
        long("seo_description", "وصف قصير للمشاركة", limit=300),
        image("share_image", "صورة المشاركة (فارغ = صورة المنتج)"),
    ],
}

TEMPLATES = {
    "quick": {"label": "عرض سريع", "description": "واجهة المنتج، شارات الثقة، الاستمارة، أسئلة قصيرة"},
    "story": {"label": "قصة كاملة", "description": "مزايا، صور، خطوات، آراء، ضمان، أسئلة، ثم الاستمارة"},
    "blank": {"label": "بسيطة", "description": "واجهة المنتج واستمارة الطلب فقط"},
}


def schema():
    return {
        "sections": [{"type": key, **value} for key, value in SECTIONS.items()],
        "site": SITE_FIELDS,
        "page": PAGE_FIELDS,
        "palettes": PALETTES,
        "fonts": {key: {"label": value["label"]} for key, value in FONTS.items()},
        "templates": TEMPLATES,
    }


def new_id():
    return secrets.token_hex(4)


def clean_string(value, limit):
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)):
        raise DesignError("قيمة غير صالحة")
    return str(value).strip()[:limit]


def clean_text(value, limit):
    if isinstance(value, dict):
        return {lang: clean_string(value.get(lang), limit) for lang in LANGUAGES}
    single = clean_string(value, limit)
    return {lang: single for lang in LANGUAGES}


def clean_image(value):
    value = clean_string(value, 300)
    if not value:
        return ""
    if not value.startswith(IMAGE_PREFIXES) or ".." in value or not IMAGE_PATTERN.match(value):
        raise DesignError("الصورة يجب أن تكون من صور المتجر")
    return value


def clean_link(value):
    value = clean_string(value, MAX_LINK)
    if not value:
        return ""
    if value.startswith("//") or not LINK_PATTERN.match(value):
        raise DesignError("الرابط يبدأ بـ / لصفحات الموقع أو # لقسم في الصفحة أو https://")
    return value


def clean_value(spec, value, path):
    kind = spec["kind"]
    try:
        if kind == "text":
            return clean_text(value, spec.get("limit", SHORT))
        if kind == "long":
            return clean_text(value, spec.get("limit", LONG))
        if kind == "image":
            return clean_image(value)
        if kind == "link":
            return clean_link(value)
        if kind == "bool":
            return bool(value)
        if kind == "color":
            value = clean_string(value, 7)
            if value and not HEX.match(value):
                raise DesignError("اللون يكتب بصيغة #RRGGBB")
            return value.upper()
        if kind == "select":
            allowed = [option[0] for option in spec["options"]]
            value = clean_string(value, 40)
            return value if value in allowed else allowed[0]
        if kind == "number":
            number = int(value if value not in (None, "") else spec["min"])
            if number < spec["min"] or number > spec["max"]:
                raise DesignError(f"اختر رقماً بين {spec['min']} و {spec['max']}")
            return number
        if kind == "datetime":
            value = clean_string(value, 40)
            if value and parse_datetime(value) is None:
                raise DesignError("تاريخ غير صالح")
            return value
        if kind == "video":
            value = clean_string(value, MAX_LINK)
            if value and not YOUTUBE.match(value):
                raise DesignError("ضع رابط فيديو من YouTube")
            return value
        if kind == "list":
            rows = value if isinstance(value, list) else []
            if len(rows) > spec["max"]:
                raise DesignError(f"الحد {spec['max']} {spec['item_label']}")
            cleaned = []
            for position, row in enumerate(rows):
                row = row if isinstance(row, dict) else {}
                item = clean_fields(spec["fields"], row, f"{path}.{position}")
                item["id"] = row.get("id") if isinstance(row.get("id"), str) and SECTION_ID.match(row.get("id")) else new_id()
                cleaned.append(item)
            return cleaned
    except (TypeError, ValueError):
        raise DesignError("قيمة غير صالحة", path)
    except DesignError as error:
        raise DesignError(error.message, error.path or path)
    raise DesignError("نوع حقل غير معروف", path)


def clean_fields(fields, data, path=""):
    data = data if isinstance(data, dict) else {}
    return {spec["key"]: clean_value(spec, data.get(spec["key"]), f"{path}.{spec['key']}".strip(".")) for spec in fields}


def clean_section(section, kind, position):
    if not isinstance(section, dict) or section.get("type") not in SECTIONS:
        raise DesignError("نوع قسم غير معروف", f"sections.{position}")
    spec = SECTIONS[section["type"]]
    if kind not in spec["kinds"]:
        raise DesignError(f"قسم «{spec['label']}» غير متاح في هذه الصفحة", f"sections.{position}")
    anchor = clean_string(section.get("anchor"), 30).lower()
    if not ANCHOR.match(anchor):
        raise DesignError("معرّف القسم حروف لاتينية صغيرة وأرقام وشرطة", f"sections.{position}.anchor")
    section_id = section.get("id")
    return {
        "id": section_id if isinstance(section_id, str) and SECTION_ID.match(section_id) else new_id(),
        "type": section["type"],
        "hidden": bool(section.get("hidden")),
        "anchor": anchor,
        "settings": clean_fields(spec["fields"], section.get("settings"), f"sections.{position}.settings"),
    }


def clean_document(kind, data, strict=False):
    data = data if isinstance(data, dict) else {}
    if kind == "site":
        cleaned = {part: clean_fields(fields, data.get(part), part) for part, fields in SITE_FIELDS.items()}
        theme = cleaned["theme"]
        if theme["palette"] != "custom":
            preset = PALETTES[theme["palette"]]
            theme["primary"], theme["dark"] = preset["primary"], preset["dark"]
        check_colors(theme)
        return cleaned
    sections = data.get("sections") if isinstance(data.get("sections"), list) else []
    if len(sections) > MAX_SECTIONS:
        raise DesignError(f"الحد {MAX_SECTIONS} قسماً في الصفحة", "sections")
    cleaned = {
        "page": clean_fields(PAGE_FIELDS[kind], data.get("page"), "page"),
        "sections": [clean_section(section, kind, position) for position, section in enumerate(sections)],
    }
    seen = set()
    for section in cleaned["sections"]:
        if section["id"] in seen:
            section["id"] = new_id()
        seen.add(section["id"])
    singles = [section for section in cleaned["sections"] if section["type"] == "order_form"]
    if len(singles) > 1:
        raise DesignError("استمارة طلب واحدة فقط في الصفحة", "sections")
    if strict and kind == "landing" and not [section for section in singles if not section["hidden"]]:
        raise DesignError("أضف قسم «استمارة الطلب» واجعله ظاهراً قبل النشر", "sections")
    return cleaned


def channel(value):
    value = value / 255
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def luminance(color):
    red, green, blue = (int(color[index:index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast_with_white(color):
    return 1.05 / (luminance(color) + 0.05)


def check_colors(theme):
    if not theme["primary"] or not theme["dark"]:
        raise DesignError("اختر اللونين", "theme")
    if contrast_with_white(theme["primary"]) < 3:
        raise DesignError("اللون الرئيسي فاتح جداً، الكتابة البيضاء على الأزرار لن تُقرأ", "theme.primary")
    if contrast_with_white(theme["dark"]) < 7:
        raise DesignError("اللون الداكن فاتح جداً للخلفيات الداكنة، اختر لوناً أغمق", "theme.dark")


def mix(color, other, amount):
    first = [int(color[index:index + 2], 16) for index in (1, 3, 5)]
    second = [int(other[index:index + 2], 16) for index in (1, 3, 5)]
    return "#" + "".join(f"{round(a + (b - a) * amount):02X}" for a, b in zip(first, second))


def theme_vars(theme):
    primary, dark = theme.get("primary") or "#1F6BFF", theme.get("dark") or "#0A1A3A"
    font = FONTS.get(theme.get("font"), FONTS["plex"])
    return {
        "--blue": primary,
        "--blue-dark": mix(primary, "#000000", 0.18),
        "--sky": mix(primary, "#FFFFFF", 0.4),
        "--tint": mix(primary, "#FFFFFF", 0.9),
        "--pick": mix(primary, "#FFFFFF", 0.95),
        "--navy": dark,
        "--deep": mix(dark, "#000000", 0.4),
        "--navy-2": mix(dark, "#FFFFFF", 0.1),
        "--line-dark": mix(dark, "#FFFFFF", 0.22),
        "--radius": RADII.get(theme.get("radius"), RADII["round"]),
        "--ar-body": f"{font['body']},system-ui,sans-serif",
        "--ar-head": f"{font['head']},{font['body']},sans-serif",
    }


def font_href(theme):
    google = FONTS.get(theme.get("font"), FONTS["plex"])["google"]
    return f"https://fonts.googleapis.com/css2?{google}&display=swap" if google else ""


def both(key):
    return {lang: text_for(lang)[key] for lang in LANGUAGES}


def same(value):
    return {lang: value for lang in LANGUAGES}


def pair(ar, en):
    return {"ar": ar, "en": en}


def art(name):
    return illustration(name)


def section(kind, settings, anchor=""):
    return {"id": new_id(), "type": kind, "hidden": False, "anchor": anchor, "settings": settings}


def row(**values):
    return {"id": new_id(), **values}


def default_site():
    links = [("nav_home", "/"), ("nav_products", "/products/"), ("nav_services", "/#services"), ("nav_track", "/track/"), ("nav_faq", "/#faq"), ("nav_contact", "/#contact")]
    return {
        "theme": {"palette": "vizora", "primary": "#1F6BFF", "dark": "#0A1A3A", "font": "plex", "radius": "round", "logo": ""},
        "header": {"show_topbar": True, "topbar_text": both("topbar_promise"), "cta_label": both("order_now"), "cta_link": "/products/"},
        "menu": {"links": [row(label=both(key), link=target) for key, target in links]},
        "footer": {
            "about": both("footer_about"),
            "columns": [
                row(
                    title=both("footer_store"),
                    links=[row(label=both(key), link=target) for key, target in links[1:2] + links[3:]],
                )
            ],
            "show_policies": True,
        },
    }


def default_home():
    sections = [
        section(
            "hero",
            {
                "show_ticks": True,
                "slides": [
                    row(eyebrow=both("hero_eyebrow"), title=both("hero_title"), text=both("hero_text"), button_label=both("hero_cta"), button_link="/products/", button2_label=both("hero_contact"), button2_link="/#contact", image=art("hero-1")),
                    row(eyebrow=both("trust_cod"), title=both("hero_slide2_title"), text=both("hero_slide2_text"), button_label=both("order_now"), button_link="/products/", button2_label=same(""), button2_link="", image=art("hero-2")),
                    row(eyebrow=both("trust_wilayas"), title=both("hero_slide3_title"), text=both("hero_slide3_text"), button_label=both("nav_track"), button_link="/track/", button2_label=same(""), button2_link="", image=art("hero-3")),
                ],
            },
        ),
        section(
            "features",
            {
                "title": both("services_title"),
                "text": both("services_text"),
                "shapes": True,
                "columns": "4",
                "items": [
                    row(title=both(f"service{number}_title"), text=both(f"service{number}_text"), image=art(picture))
                    for number, picture in ((1, "card-hero"), (2, "service-topup"), (3, "service-games"), (4, "step-2-call"))
                ],
            },
            "services",
        ),
        section("products", {"title": both("products_title"), "text": both("products_text"), "limit": 6, "show_all_link": True}, "products"),
        section(
            "steps",
            {
                "title": both("how_title"),
                "text": both("how_text"),
                "items": [
                    row(title=both(f"step{number}_title"), text=both(f"step{number}_text"), image=art(picture))
                    for number, picture in ((1, "step-1-order"), (2, "step-2-call"), (3, "step-3-ship"), (4, "step-4-receive"))
                ],
            },
            "how",
        ),
        section(
            "promo",
            {"eyebrow": both("promo_eyebrow"), "title": both("promo_title"), "text": both("promo_text"), "button_label": both("order_now"), "button_link": "/products/", "image": art("promo")},
        ),
        section("reviews", {"title": both("reviews_title"), "text": both("reviews_text"), "limit": 6}, "reviews"),
        section(
            "faq",
            {"title": both("faq_title"), "items": [row(question=both(f"faq{number}_q"), answer=both(f"faq{number}_a")) for number in (1, 2, 3, 4)]},
            "faq",
        ),
        section("contact", {"title": both("contact_title"), "image": art("contact")}, "contact"),
    ]
    return clean_document("home", {"page": {}, "sections": sections})


LANDING_TEXT = {
    "ar": {
        "badge": "الدفع عند الاستلام",
        "button": "اطلب الآن",
        "trust": ["الدفع عند الاستلام", "توصيل إلى 58 ولاية", "نتصل بك للتأكيد"],
        "form_title": "اطلب الآن، وادفع عند الاستلام",
        "form_text": "اكتب معلوماتك وسنتصل بك لتأكيد الطلبية.",
        "form_button": "تأكيد الطلب",
        "faq_title": "أسئلة شائعة",
        "faq": [
            ("هل أدفع قبل الاستلام؟", "لا، تدفع نقداً عند استلام الطلبية فقط."),
            ("كم يستغرق التوصيل؟", "حسب ولايتك. نتصل بك أولاً للتأكيد ثم تخرج الطلبية مع Maystro."),
            ("هل تساعدونني بعد الشراء؟", "نعم، فريقنا يرافقك ويعالج معك أي مشكلة."),
        ],
        "benefits_title": "لماذا تختارنا؟",
        "benefits": [
            ("مرافقة خطوة بخطوة", "نشرح لك كل شيء حتى تصبح جاهزاً."),
            ("دفع آمن", "لا تدفع شيئاً قبل أن تستلم."),
            ("توصيل سريع", "إلى المنزل أو إلى أقرب مكتب توصيل."),
            ("دعم بعد الشراء", "فريقنا معك عبر الهاتف وواتساب."),
        ],
        "gallery_title": "صور المنتج",
        "steps_title": "كيف تطلب؟",
        "steps": [("اطلب من الصفحة", "اكتب اسمك ورقمك وولايتك."), ("نتصل بك", "نؤكد الطلبية والعنوان."), ("نرسلها", "مع Maystro Delivery."), ("ادفع عند الاستلام", "نقداً للموزّع.")],
        "reviews_title": "آراء زبائننا",
        "guarantee_title": "ضماننا لك",
        "guarantee_text": "إذا ظهرت لك أي مشكلة بعد الاستلام، نتابعها معك حتى تُحل.",
    },
    "en": {
        "badge": "Cash on delivery",
        "button": "Order now",
        "trust": ["Cash on delivery", "Delivery to 58 wilayas", "We call to confirm"],
        "form_title": "Order now, pay on delivery",
        "form_text": "Enter your details and we will call you to confirm.",
        "form_button": "Confirm my order",
        "faq_title": "Questions",
        "faq": [
            ("Do I pay before delivery?", "No, you pay in cash only when you receive the order."),
            ("How long does delivery take?", "It depends on your wilaya. We call you first, then the order ships with Maystro."),
            ("Do you help after the purchase?", "Yes, our team stays with you and solves any problem."),
        ],
        "benefits_title": "Why choose us?",
        "benefits": [
            ("Step-by-step help", "We explain everything until you are ready."),
            ("Safe payment", "You pay nothing before you receive it."),
            ("Fast delivery", "To your home or the nearest delivery desk."),
            ("After-sale support", "Our team is with you by phone and WhatsApp."),
        ],
        "gallery_title": "Photos",
        "steps_title": "How to order",
        "steps": [("Order on this page", "Enter your name, phone and wilaya."), ("We call you", "We confirm the order and address."), ("We ship it", "With Maystro Delivery."), ("Pay on delivery", "In cash to the courier.")],
        "reviews_title": "What our customers say",
        "guarantee_title": "Our promise",
        "guarantee_text": "If anything goes wrong after delivery, we follow it with you until it is solved.",
    },
}


def landing_template(name, lang, store_product):
    words = LANDING_TEXT[lang]
    title = (store_product.title_en if lang == "en" else "") or store_product.title_ar
    summary = (store_product.summary_en if lang == "en" else "") or store_product.summary_ar

    def one(value):
        return {"ar": value if lang == "ar" else "", "en": value if lang == "en" else ""}

    hero = section("product_hero", {"badge": one(words["badge"]), "title": one(""), "text": one(summary), "image": "", "button_label": one(words["button"]), "show_trust": True})
    form = section("order_form", {"title": one(words["form_title"]), "text": one(words["form_text"]), "button_label": one(words["form_button"])}, "order")
    faq = section("faq", {"title": one(words["faq_title"]), "items": [row(question=one(question), answer=one(answer)) for question, answer in words["faq"]]}, "faq")
    trust = section("trust", {"items": [row(title=one(label), image=art(picture)) for label, picture in zip(words["trust"], ("feature-cod", "feature-delivery", "step-2-call"))]})
    if name == "blank":
        sections = [hero, form]
    elif name == "story":
        sections = [
            hero,
            section(
                "features",
                {
                    "title": one(words["benefits_title"]),
                    "text": one(""),
                    "shapes": False,
                    "columns": "4",
                    "items": [row(title=one(head), text=one(body), image=art(picture)) for (head, body), picture in zip(words["benefits"], ("card-hero", "feature-cod", "feature-delivery", "feature-guarantee"))],
                },
            ),
            section("gallery", {"title": one(words["gallery_title"]), "images": []}),
            section(
                "steps",
                {"title": one(words["steps_title"]), "text": one(""), "items": [row(title=one(head), text=one(body), image=art(picture)) for (head, body), picture in zip(words["steps"], ("step-1-order", "step-2-call", "step-3-ship", "step-4-receive"))]},
            ),
            section("reviews", {"title": one(words["reviews_title"]), "text": one(""), "limit": 6}),
            section("guarantee", {"title": one(words["guarantee_title"]), "text": one(words["guarantee_text"]), "image": art("feature-guarantee")}),
            faq,
            form,
        ]
    else:
        sections = [hero, trust, form, faq]
    page = {"hide_menu": True, "sticky_bar": True, "seo_title": one(title), "seo_description": one(summary[:300]), "share_image": ""}
    return clean_document("landing", {"page": page, "sections": sections})
