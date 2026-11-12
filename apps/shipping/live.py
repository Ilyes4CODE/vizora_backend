import threading
import unicodedata
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Case, F, IntegerField, Q, Value, When
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.core.exceptions import ApiError
from apps.orders.models import OrderEvent, SuiviOutcome
from apps.orders.services import add_event, next_morning, retry_time

from .account import DELIVERED, GROUPS, page_is_last, sync_balance, sync_payments, sync_products, upsert_rows, whole, without_contact
from .maystro import ACTIVE_STATUSES, NETWORK_ATTEMPTS, STATUS_LABELS, STOP_DESK_DELIVERY, MaystroError, get_client
from .models import MaystroConfig, MaystroOrder, ParcelCall, Stage
from .services import apply_status_update, parse_moment

SUIVI_STATUSES = (32, 42, 33, 31, 22)
PARCELS_FRESH_SECONDS = 45
STOCK_FRESH_SECONDS = 300
MONEY_FRESH_SECONDS = 300
MAX_LIVE_PAGES = 50
MAX_SETTLE = 10
DISPLAY_STAGES = ("transit", "out", "desk", "stuck")
TERMINAL_STAGES = (Stage.DELIVERED, Stage.RETURNED, Stage.ABORTED)
ALERT_REASONS = {
    "le client ne repond pas": "الزبون لا يرد",
    "le client est injoignable": "هاتف الزبون مغلق أو خارج التغطية",
    "le client veut reporter la commande": "الزبون يريد تأجيل الطلبية",
    "le client va venir recuperer le colis": "الزبون سيأتي لاستلام الطرد من المكتب",
    "le client est absent": "الزبون غائب",
    "client absent": "الزبون غائب",
    "colis refuse": "الزبون رفض الطرد",
    "le client a annule la commande": "الزبون ألغى الطلبية",
    "le client refuse la commande": "الزبون يرفض الطلبية",
    "commande refusee": "الزبون يرفض الطلبية",
    "adresse incorrecte": "العنوان خاطئ",
    "mauvaise adresse": "العنوان خاطئ",
    "numero erronee": "رقم الهاتف خاطئ",
    "numero errone": "رقم الهاتف خاطئ",
    "numero incorrect": "رقم الهاتف خاطئ",
}
DELIVERY_LABELS = {1: "إلى المنزل", 2: "مكتب التوصيل"}
_locks = {"parcels": threading.Lock(), "stock": threading.Lock(), "money": threading.Lock()}
_attempted = {}


def plain(text):
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    return " ".join("".join(char for char in decomposed if not unicodedata.combining(char)).lower().split())


def alert_label(reason):
    if not reason:
        return ""
    return ALERT_REASONS.get(plain(reason).rstrip(". "), reason)


def is_ready(config):
    return bool(config.encrypted_token)


def live_mode():
    return is_ready(MaystroConfig.load())


def is_due(key, synced_at, seconds):
    now = timezone.now()
    if synced_at and (now - synced_at).total_seconds() < seconds:
        return False
    last = _attempted.get(key)
    return last is None or (now - last).total_seconds() >= seconds


def forget_attempts():
    _attempted.clear()


def collect(client, statuses):
    rows = []
    for page in range(1, MAX_LIVE_PAGES + 1):
        data = client.store_orders(page, statuses)
        rows.extend(row for row in data["results"] if isinstance(row, dict) and row.get("id"))
        if page_is_last(data):
            break
    return rows


def settle_departed(client, started):
    changed = []
    stale = MaystroOrder.objects.filter(status__in=ACTIVE_STATUSES).filter(Q(live_seen_at__lt=started) | Q(live_seen_at=None))
    for row in stale.order_by(F("remote_updated_at").desc(nulls_last=True))[:MAX_SETTLE]:
        try:
            history = client.history(row.maystro_id)
        except MaystroError as error:
            if error.status_code in (400, 404):
                continue
            raise
        status = whole(history[0].get("status")) if history else None
        if status is None:
            continue
        moment = parse_moment(history[0].get("created_at")) or row.remote_updated_at
        fields = without_contact({"status": status, "remote_updated_at": moment})
        if status in ACTIVE_STATUSES:
            fields["live_seen_at"] = started
        if status == DELIVERED and row.delivered_at is None:
            fields["delivered_at"] = moment
        MaystroOrder.objects.filter(pk=row.pk).update(**fields)
        changed.append(row.pk)
    return changed


def follow_shipments(pks):
    rows = MaystroOrder.objects.filter(pk__in=pks, shipment__isnull=False).select_related("shipment")
    for row in rows:
        if str(row.status) != row.shipment.raw_status:
            apply_status_update(row.shipment_id, str(row.status), occurred_at=row.remote_updated_at)


def pull_parcels(config, force):
    started = timezone.now()
    _attempted["parcels"] = started
    client = get_client(config, NETWORK_ATTEMPTS if force else 1)
    try:
        rows = collect(client, ACTIVE_STATUSES)
        upsert_rows(rows)
        ids = [str(row["id"]) for row in rows]
        MaystroOrder.objects.filter(maystro_id__in=ids).update(live_seen_at=started)
        changed = settle_departed(client, started)
    except MaystroError as error:
        MaystroConfig.objects.filter(pk=config.pk).update(live_error=error.message[:255])
        if force:
            raise ApiError(error.message, error.code, 502) from error
        return False
    MaystroConfig.objects.filter(pk=config.pk).update(live_parcels_at=started, live_error="")
    follow_shipments(list(MaystroOrder.objects.filter(maystro_id__in=ids).values_list("pk", flat=True)) + changed)
    return True


def refresh_parcels(force=False):
    config = MaystroConfig.load()
    if not is_ready(config):
        return False
    if not force and not is_due("parcels", config.live_parcels_at, PARCELS_FRESH_SECONDS):
        return False
    lock = _locks["parcels"]
    if not lock.acquire(blocking=force):
        return False
    try:
        return pull_parcels(config, force)
    finally:
        lock.release()


def pull_stock(config, force):
    _attempted["stock"] = timezone.now()
    try:
        sync_products(get_client(config, NETWORK_ATTEMPTS if force else 1))
    except MaystroError as error:
        MaystroConfig.objects.filter(pk=config.pk).update(live_error=error.message[:255])
        if force:
            raise ApiError(error.message, error.code, 502) from error
        return False
    return True


def locked_stock_pull(config, force):
    lock = _locks["stock"]
    if not lock.acquire(blocking=force):
        return False
    try:
        return pull_stock(config, force)
    finally:
        lock.release()


def stock_in_background(config):
    try:
        locked_stock_pull(config, False)
    finally:
        connection.close()


def refresh_stock(force=False, background=False):
    config = MaystroConfig.load()
    if not is_ready(config):
        return False
    if not force and not is_due("stock", config.live_stock_at, STOCK_FRESH_SECONDS):
        return False
    if background and not force and config.live_stock_at is not None:
        _attempted["stock"] = timezone.now()
        threading.Thread(target=stock_in_background, args=(config,), daemon=True).start()
        return False
    return locked_stock_pull(config, force)


def pull_money(config, force):
    _attempted["money"] = timezone.now()
    client = get_client(config, NETWORK_ATTEMPTS if force else 1)
    try:
        sync_balance(client)
        sync_payments(client, full=config.payments_synced_at is None)
    except MaystroError as error:
        MaystroConfig.objects.filter(pk=config.pk).update(live_error=error.message[:255])
        if force:
            raise ApiError(error.message, error.code, 502) from error
        return False
    return True


def refresh_money(force=False):
    config = MaystroConfig.load()
    if not is_ready(config):
        return False
    if not force and not is_due("money", config.balance_at, MONEY_FRESH_SECONDS):
        return False
    lock = _locks["money"]
    if not lock.acquire(blocking=force):
        return False
    try:
        return pull_money(config, force)
    finally:
        lock.release()


def status_groups(config):
    groups = {}
    for key, stage in config.status_map.items():
        if str(key).isdigit():
            groups.setdefault(stage, []).append(int(key))
    return groups


def stuck_condition(config):
    threshold = timezone.now() - timedelta(hours=settings.PARCEL_STUCK_HOURS)
    return Q(status__in=status_groups(config).get(Stage.FAILED, [])) | Q(remote_updated_at__lt=threshold)


def active_parcels(config):
    queryset = MaystroOrder.objects.filter(status__in=ACTIVE_STATUSES)
    if config.live_parcels_at:
        queryset = queryset.filter(live_seen_at__gte=config.live_parcels_at)
    return queryset


def filter_parcels(config, stage):
    groups = status_groups(config)
    if stage == "delivered":
        return MaystroOrder.objects.filter(GROUPS["delivered"])
    if stage == "returned":
        return MaystroOrder.objects.filter(GROUPS["returned"])
    queryset = active_parcels(config)
    stuck = stuck_condition(config)
    out = groups.get(Stage.OUT, [])
    if stage == "stuck":
        return queryset.filter(stuck)
    if stage == "out":
        return queryset.filter(status__in=out).exclude(delivery_type=STOP_DESK_DELIVERY).exclude(stuck)
    if stage == "desk":
        return queryset.filter(Q(status__in=groups.get(Stage.DESK, [])) | Q(status__in=out, delivery_type=STOP_DESK_DELIVERY)).exclude(stuck)
    if stage == "transit":
        return queryset.filter(status__in=groups.get(Stage.TRANSIT, [])).exclude(stuck)
    return queryset


def parcel_counts(config):
    return {"all": active_parcels(config).count(), **{stage: filter_parcels(config, stage).count() for stage in DISPLAY_STAGES}}


def search_parcels(queryset, term):
    term = (term or "").strip()
    if not term:
        return queryset
    return queryset.filter(Q(display_id__icontains=term) | Q(customer_name__icontains=term) | Q(customer_phone__icontains=term) | Q(customer_phone2__icontains=term))


def parcel_stage(row, status_map):
    stage = status_map.get(str(row.status))
    if stage == Stage.OUT and row.delivery_type == STOP_DESK_DELIVERY:
        return Stage.DESK
    return stage or Stage.TRANSIT


def parcel_display_stage(row, status_map):
    stage = parcel_stage(row, status_map)
    if stage in TERMINAL_STAGES:
        return stage
    threshold = timezone.now() - timedelta(hours=settings.PARCEL_STUCK_HOURS)
    if stage == Stage.FAILED or (row.remote_updated_at and row.remote_updated_at < threshold):
        return "stuck"
    return stage


def status_label(row):
    label = STATUS_LABELS.get(str(row.status), str(row.status))
    reason = alert_label(row.alert_reason)
    return f"{label} · {reason}" if reason and row.status in (32, 42) else label


def parcel_history(row):
    client = get_client(attempts=1)
    entries = client.history(row.maystro_id)
    status_map = MaystroConfig.load().status_map
    events = []
    for index, entry in enumerate(entries):
        status = str(entry.get("status") or "")
        comment = alert_label(entry.get("comment"))
        label = STATUS_LABELS.get(status, status)
        events.append(
            {
                "id": index + 1,
                "raw_status": status,
                "stage": status_map.get(status, ""),
                "message": f"{label} · {comment}" if comment else label,
                "occurred_at": parse_moment(entry.get("created_at")),
            }
        )
    return events


def suivi_queue(config):
    now = timezone.now()
    queryset = MaystroOrder.objects.filter(status__in=SUIVI_STATUSES, is_refund=False)
    if config.live_parcels_at:
        queryset = queryset.filter(live_seen_at__gte=config.live_parcels_at)
    due = Q(suivi_next_at__isnull=True) | Q(suivi_next_at__lte=now) | Q(suivi_status__isnull=True) | ~Q(status=F("suivi_status"))
    priority = Case(*[When(status=status, then=Value(index)) for index, status in enumerate(SUIVI_STATUSES)], default=Value(len(SUIVI_STATUSES)), output_field=IntegerField())
    return queryset.filter(due).annotate(priority=priority).order_by("priority", "suivi_attempts", F("remote_updated_at").asc(nulls_last=True), "id")


@transaction.atomic
def record_parcel_call(parcel_id, outcome, user, note="", next_call_at=None):
    parcel = MaystroOrder.objects.select_for_update().get(pk=parcel_id)
    if outcome not in SuiviOutcome.values:
        raise ValidationError({"outcome": "نتيجة غير معروفة"})
    if outcome == SuiviOutcome.POSTPONED and next_call_at is None:
        raise ValidationError({"next_call_at": "حدد موعد الاتصال التالي"})
    if outcome == SuiviOutcome.NO_ANSWER:
        parcel.suivi_next_at = retry_time()
    elif outcome == SuiviOutcome.POSTPONED:
        parcel.suivi_next_at = next_call_at
    else:
        parcel.suivi_next_at = next_morning()
    parcel.suivi_attempts += 1
    parcel.suivi_status = parcel.status
    parcel.save(update_fields=["suivi_attempts", "suivi_next_at", "suivi_status"])
    message = f"{SuiviOutcome(outcome).label} · {user.full_name or user.username}"
    ParcelCall.objects.create(parcel=parcel, outcome=outcome, message=message, note=note, next_call_at=parcel.suivi_next_at, user=user)
    if parcel.shipment_id:
        add_event(parcel.shipment.order, message, user=user, stage=OrderEvent.Stage.SUIVI, outcome=outcome, note=note)
    return parcel
