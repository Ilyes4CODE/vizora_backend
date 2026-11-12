from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.catalog.models import Product, StockMovement
from apps.catalog.services import apply_movement
from apps.core.excel import normalize_text
from apps.core.exceptions import ApiError, conflict
from apps.core.models import Commune, Wilaya
from apps.orders.models import Order, OrderStatus
from apps.orders.services import add_event

from .maystro import STATUS_LABELS, MaystroError, get_client
from .models import MaystroConfig, MaystroOrder, Shipment, ShipmentEvent, Stage
from .stock import is_maystro_managed

ACTIVE_STAGES = (Stage.TRANSIT, Stage.OUT, Stage.DESK, Stage.FAILED)
DISPLAY_STAGES = ("transit", "out", "desk", "stuck")


def stuck_condition():
    threshold = timezone.now() - timedelta(hours=settings.PARCEL_STUCK_HOURS)
    return Q(stage=Stage.FAILED) | Q(stage__in=ACTIVE_STAGES, last_update_at__lt=threshold)


def active_shipments():
    return Shipment.objects.filter(stage__in=ACTIVE_STAGES)


def filter_display_stage(queryset, stage):
    if stage == "stuck":
        return queryset.filter(stuck_condition())
    if stage in ("transit", "out", "desk"):
        return queryset.filter(stage=stage).exclude(stuck_condition())
    return queryset.filter(stage=stage)


def stage_counts():
    base = active_shipments()
    counts = {stage: filter_display_stage(base, stage).count() for stage in DISPLAY_STAGES}
    return {"all": base.count(), **counts}


def display_stage(shipment):
    if shipment.is_terminal:
        return shipment.stage
    threshold = timezone.now() - timedelta(hours=settings.PARCEL_STUCK_HOURS)
    if shipment.stage == Stage.FAILED or shipment.last_update_at < threshold:
        return "stuck"
    return shipment.stage


def send_one(order_id, user, client):
    with transaction.atomic():
        order = Order.objects.select_for_update().select_related("wilaya").get(pk=order_id)
        if order.status != OrderStatus.CONFIRMED:
            raise conflict("الطلبية غير مؤكدة", "invalid_transition")
        for item in order.items.select_related("product"):
            if not is_maystro_managed(item.product):
                apply_movement(item.product_id, StockMovement.Kind.SHIP, -item.quantity, order_reference=order.reference)
        created = client.create_order(order)
        now = timezone.now()
        shipment = Shipment.objects.create(
            order=order,
            tracking_number=created["tracking_number"],
            external_id=created.get("external_id", ""),
            stage=Stage.TRANSIT,
            last_update_at=now,
            created_at=now,
        )
        order.status = OrderStatus.SHIPPING
        order.shipped_at = now
        order.save(update_fields=["status", "shipped_at"])
        add_event(order, f"أُرسلت إلى Maystro · {shipment.tracking_number}", user=user)
        return shipment


def send_orders(order_ids, user):
    client = get_client()
    sent = []
    failed = []
    for order_id in order_ids:
        try:
            shipment = send_one(order_id, user, client)
            sent.append({"order": order_id, "tracking_number": shipment.tracking_number})
        except Order.DoesNotExist:
            failed.append({"order": order_id, "code": "not_found", "detail": "الطلبية غير موجودة"})
        except MaystroError as error:
            failed.append({"order": order_id, "code": error.code, "detail": error.message})
        except ApiError as error:
            failed.append({"order": order_id, "code": error.get_codes(), "detail": str(error.detail)})
    return {"sent": sent, "failed": failed}


@transaction.atomic
def apply_status_update(shipment_id, raw_status, message="", occurred_at=None, stage=None):
    shipment = Shipment.objects.select_for_update().select_related("order").get(pk=shipment_id)
    config = MaystroConfig.load()
    moment = occurred_at or timezone.now()
    raw = str(raw_status).strip()
    resolved = stage or config.status_map.get(raw.lower())
    if resolved == Stage.OUT and shipment.order.delivery_type == "desk":
        resolved = Stage.DESK
    _, created = ShipmentEvent.objects.get_or_create(
        shipment=shipment,
        raw_status=raw,
        occurred_at=moment,
        defaults={"stage": resolved or "", "message": message or STATUS_LABELS.get(raw, "")},
    )
    if not created:
        return shipment, False
    shipment.last_update_at = timezone.now()
    shipment.raw_status = raw
    is_older = shipment.last_event_at is not None and moment < shipment.last_event_at
    if resolved is None or shipment.is_terminal or is_older:
        shipment.save(update_fields=["last_update_at", "raw_status"])
        return shipment, False
    previous_stage = shipment.stage
    shipment.last_event_at = moment
    shipment.stage = resolved
    order = shipment.order
    if resolved == Stage.OUT and previous_stage != Stage.OUT:
        shipment.delivery_attempts += 1
    if resolved in (Stage.OUT, Stage.DESK, Stage.FAILED):
        order.suivi_next_at = None
        order.save(update_fields=["suivi_next_at"])
    if resolved == Stage.DELIVERED:
        order.status = OrderStatus.DELIVERED
        order.delivered_at = moment
        order.save(update_fields=["status", "delivered_at"])
    if resolved == Stage.RETURNED:
        order.status = OrderStatus.RETURNED
        order.returned_at = moment
        order.save(update_fields=["status", "returned_at"])
        shipment.return_fee = config.return_fee
    if resolved == Stage.ABORTED:
        order.status = OrderStatus.CANCELLED
        order.cancelled_at = moment
        order.cancel_reason = "courier_aborted"
        order.save(update_fields=["status", "cancelled_at", "cancel_reason"])
    shipment.save()
    add_event(order, Stage(resolved).label, at=moment)
    return shipment, True


def parse_moment(value):
    if not value:
        return None
    moment = parse_datetime(str(value))
    if moment is None:
        return None
    return moment if timezone.is_aware(moment) else timezone.make_aware(moment)


def mirror_status(shipment, fresh_after):
    if not shipment.external_id:
        return None
    row = MaystroOrder.objects.filter(maystro_id=shipment.external_id, synced_at__gte=fresh_after).first()
    return {"status": str(row.status), "occurred_at": None} if row else None


def run_mirror_sync(config):
    from .account import sync_account

    if not config.encrypted_token or config.mirror_full_sync_at is None:
        return None
    try:
        return sync_account(full=False)
    except ApiError as error:
        if error.get_codes() == "sync_running":
            return None
        raise


def sync_parcels():
    config = MaystroConfig.load()
    if not config.encrypted_token:
        raise conflict("أدخل مفتاح Maystro أولاً في «الحماية والنسخ» ← «ربط Maystro»", "maystro_not_configured")
    client = get_client(config)
    checked = changed = 0
    started = timezone.now()
    mirror = run_mirror_sync(config)
    if config.encrypted_token:
        from .live import refresh_parcels

        try:
            mirror = refresh_parcels(force=True) or mirror
        except ApiError as error:
            MaystroConfig.objects.filter(pk=config.pk).update(last_sync_at=timezone.now(), last_sync_ok=False, last_sync_error=str(error.detail)[:255])
            raise
    config = MaystroConfig.load()
    config.last_sync_at = timezone.now()
    try:
        for shipment in active_shipments():
            checked += 1
            update = mirror_status(shipment, started) if mirror else None
            update = update or client.fetch_status(shipment)
            if update is None or update["status"] == shipment.raw_status:
                continue
            _, did_change = apply_status_update(shipment.pk, update["status"], occurred_at=parse_moment(update.get("occurred_at")))
            changed += int(did_change)
    except MaystroError as error:
        config.last_sync_ok = False
        config.last_sync_error = error.message[:255]
        config.save()
        raise ApiError(error.message, error.code, 502) from error
    config.last_sync_ok = True
    config.last_sync_error = ""
    config.save()
    return {"checked": checked, "changed": changed, "last_sync_at": config.last_sync_at, "mirror": mirror}


def link_products(remote_products):
    index = {}
    for item in remote_products:
        for key in (item.get("logistical_description"), item.get("product_id")):
            if key:
                index[normalize_text(key)] = str(item["id"])
    unlinked = []
    linked = 0
    for product in Product.objects.filter(is_active=True):
        match = index.get(normalize_text(product.name)) or (index.get(normalize_text(product.sku)) if product.sku else None)
        if match is None:
            unlinked.append(product.name)
            continue
        product.courier_ref = match
        product.save(update_fields=["courier_ref"])
        linked += 1
    return linked, unlinked


def priced_options(client, commune_id):
    try:
        return client.delivery_options(commune_id)
    except MaystroError as error:
        if error.status_code in (400, 404) or (error.status_code or 0) >= 500:
            return []
        raise


def sync_wilaya(wilaya, remote, client):
    wilaya.courier_ref = str(remote["display_id"])
    remote_communes = [item for item in client.communes(remote["display_id"]) if isinstance(item, dict)]
    for item in remote_communes:
        Commune.objects.update_or_create(
            wilaya=wilaya,
            name_ar=item["name"],
            defaults={"name_fr": item["name"], "courier_ref": str(item["id"])},
        )
    center = remote.get("center_commune") or (remote_communes[0]["id"] if remote_communes else None)
    if center:
        options = priced_options(client, center)
        prices = {option.get("type"): option.get("price") for option in options if isinstance(option, dict)}
        home = prices.get("home")
        desk = prices.get("stopdesk")
        wilaya.home_available = home is not None
        wilaya.desk_available = desk is not None
        wilaya.home_fee = wilaya.home_fee if home is None else int(home)
        wilaya.desk_fee = wilaya.desk_fee if desk is None else int(desk)
    wilaya.save()
    return len(remote_communes)


def sync_catalog():
    config = MaystroConfig.load()
    if not config.encrypted_token:
        raise conflict("أدخل مفتاح Maystro أولاً في «الحماية والنسخ» ← «ربط Maystro»", "maystro_not_configured")
    client = get_client(config)
    try:
        served = {int(item["code"]): item for item in client.wilayas() if isinstance(item, dict) and item.get("code")}
        communes = 0
        for wilaya in Wilaya.objects.all():
            if wilaya.code in served:
                communes += sync_wilaya(wilaya, served[wilaya.code], client)
            else:
                Wilaya.objects.filter(pk=wilaya.pk).update(courier_ref="", home_available=False, desk_available=False)
        linked, unlinked = link_products(client.products())
    except MaystroError as error:
        raise ApiError(error.message, error.code, 502) from error
    return {"wilayas": len(served), "communes": communes, "products_linked": linked, "products_unlinked": unlinked}


@transaction.atomic
def receive_return(shipment_id, condition, user):
    shipment = Shipment.objects.select_for_update().select_related("order").get(pk=shipment_id)
    if shipment.stage not in (Stage.RETURNED, Stage.ABORTED):
        raise conflict("هذا الطرد ليس مرتجعاً", "invalid_transition")
    if shipment.return_received_at is not None:
        raise conflict("تم استلام هذا المرتجع مسبقاً", "return_already_received")
    shipment.return_condition = condition
    shipment.return_received_at = timezone.now()
    shipment.save(update_fields=["return_condition", "return_received_at"])
    if condition == "good":
        for item in shipment.order.items.select_related("product"):
            if is_maystro_managed(item.product):
                continue
            apply_movement(
                item.product_id, StockMovement.Kind.RETURN, item.quantity, user=user, order_reference=shipment.order.reference
            )
    add_event(shipment.order, "استُلم المرتجع" if condition == "good" else "استُلم المرتجع تالفاً", user=user)
    return shipment
