import re
import secrets

from rest_framework import serializers

KINDS = ("size", "color", "other")
DEFAULT_NAMES = {"size": ("المقاس", "Size"), "color": ("اللون", "Color"), "other": ("الخيار", "Option")}
MAX_OPTIONS = 2
MAX_VALUES = 30
LABEL_LIMIT = 40
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
ID = re.compile(r"^[A-Za-z0-9_-]{1,24}$")


def new_id():
    return secrets.token_hex(4)


def text(value, limit=LABEL_LIMIT):
    return " ".join(str(value or "").split())[:limit]


def localized(value, limit=LABEL_LIMIT):
    if isinstance(value, dict):
        return {"ar": text(value.get("ar"), limit), "en": text(value.get("en"), limit)}
    single = text(value, limit)
    return {"ar": single, "en": single}


def clean_options(raw):
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise serializers.ValidationError("الخيارات غير صالحة")
    if len(raw) > MAX_OPTIONS:
        raise serializers.ValidationError(f"الحد {MAX_OPTIONS} خيارات لكل منتج، مثل المقاس واللون")
    cleaned = []
    for option in raw:
        if not isinstance(option, dict):
            raise serializers.ValidationError("الخيارات غير صالحة")
        kind = option.get("kind") if option.get("kind") in KINDS else "other"
        name = localized(option.get("name"))
        name["ar"] = name["ar"] or DEFAULT_NAMES[kind][0]
        name["en"] = name["en"] or DEFAULT_NAMES[kind][1]
        values = option.get("values") if isinstance(option.get("values"), list) else []
        if not values:
            raise serializers.ValidationError(f"أضف قيمة واحدة على الأقل في «{name['ar']}»")
        if len(values) > MAX_VALUES:
            raise serializers.ValidationError(f"الحد {MAX_VALUES} قيمة في «{name['ar']}»")
        rows = []
        seen = set()
        for value in values:
            if not isinstance(value, dict):
                raise serializers.ValidationError("الخيارات غير صالحة")
            label = localized(value.get("label"))
            label["ar"] = label["ar"] or label["en"]
            label["en"] = label["en"] or label["ar"]
            if not label["ar"]:
                raise serializers.ValidationError(f"اكتب كل القيم في «{name['ar']}»")
            if label["ar"].lower() in seen:
                raise serializers.ValidationError(f"القيمة «{label['ar']}» مكررة في «{name['ar']}»")
            seen.add(label["ar"].lower())
            color = text(value.get("color"), 7)
            if color and not HEX.match(color):
                raise serializers.ValidationError("اللون يكتب بصيغة #RRGGBB")
            value_id = value.get("id")
            rows.append(
                {
                    "id": value_id if isinstance(value_id, str) and ID.match(value_id) else new_id(),
                    "label": label,
                    "color": color.upper() if kind == "color" else "",
                    "available": value.get("available", True) is not False,
                }
            )
        option_id = option.get("id")
        cleaned.append({"id": option_id if isinstance(option_id, str) and ID.match(option_id) else new_id(), "kind": kind, "name": name, "values": rows})
    ids = [option["id"] for option in cleaned]
    if len(set(ids)) != len(ids):
        cleaned[-1]["id"] = new_id()
    return cleaned


def option_rows(options, lang):
    return [
        {
            "id": option["id"],
            "kind": option["kind"],
            "name": option["name"].get(lang) or option["name"]["ar"],
            "values": [
                {"id": value["id"], "label": value["label"].get(lang) or value["label"]["ar"], "color": value["color"], "available": value["available"]}
                for value in option["values"]
            ],
        }
        for option in options or []
    ]


def choose(options, picked):
    picked = picked if isinstance(picked, dict) else {}
    parts = []
    for option in options or []:
        value = next((entry for entry in option["values"] if entry["id"] == str(picked.get(option["id"], ""))), None)
        if value is None:
            return None, "err_option"
        if not value["available"]:
            return None, "err_option_out"
        parts.append(f"{option['name']['ar']}: {value['label']['ar']}")
    return " · ".join(parts), None
