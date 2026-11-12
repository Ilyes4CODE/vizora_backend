from datetime import date, datetime, time, timedelta

from django.utils import timezone
from rest_framework.exceptions import ValidationError

MONTH_NAMES = ["جانفي", "فيفري", "مارس", "أفريل", "ماي", "جوان", "جويلية", "أوت", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


def local_datetime(day, moment=time.min):
    return timezone.make_aware(datetime.combine(day, moment))


def first_of_next_month(day):
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


def first_of_previous_month(day):
    if day.month == 1:
        return date(day.year - 1, 12, 1)
    return date(day.year, day.month - 1, 1)


def parse_month(value):
    if not value:
        today = timezone.localdate()
        return date(today.year, today.month, 1)
    try:
        return datetime.strptime(value, "%Y-%m").date()
    except ValueError as error:
        raise ValidationError({"month": "استعمل الصيغة YYYY-MM"}) from error


def parse_day(value, field):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError) as error:
        raise ValidationError({field: "استعمل الصيغة YYYY-MM-DD"}) from error


def month_bounds(first_day):
    return local_datetime(first_day), local_datetime(first_of_next_month(first_day))


def day_bounds(day):
    return local_datetime(day), local_datetime(day + timedelta(days=1))


def month_key(first_day):
    return first_day.strftime("%Y-%m")


def month_label(first_day):
    return f"{MONTH_NAMES[first_day.month - 1]} {first_day.year}"


def iterate_days(first_day, last_day):
    current = first_day
    while current <= last_day:
        yield current
        current += timedelta(days=1)
