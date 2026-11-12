from datetime import timedelta

from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from apps.core.dates import local_datetime, month_bounds, month_key
from apps.core.exceptions import ApiError, conflict
from apps.core.excel import match_column, read_rows
from apps.orders.models import Order, OrderItem, OrderStatus
from apps.shipping.models import Shipment, Stage

from .models import Campaign, Expense, Settlement, StatementLine

TRACKING_ALIASES = ["tracking", "tracking_number", "tracking number", "رقم التتبع", "code", "suivi", "numero de suivi", "n suivi"]
AMOUNT_ALIASES = ["amount", "montant", "net", "المبلغ", "الصافي"]


def share(part, whole, digits=1):
    return round(part * 100 / whole, digits) if whole else 0


def safe_cost(spend, count):
    return round(spend / count) if count else 0


def profit_summary(first_day):
    start, end = month_bounds(first_day)
    delivered = Order.objects.filter(status=OrderStatus.DELIVERED, delivered_at__gte=start, delivered_at__lt=end)
    sales = delivered.aggregate(value=Sum("subtotal"))["value"] or 0
    goods = OrderItem.objects.filter(order__in=delivered).aggregate(value=Sum(F("unit_cost") * F("quantity")))["value"] or 0
    return_fees = Shipment.objects.filter(
        order__status=OrderStatus.RETURNED, order__returned_at__gte=start, order__returned_at__lt=end
    ).aggregate(value=Sum("return_fee"))["value"] or 0
    recorded = dict(
        Expense.objects.filter(date__gte=first_day, date__lt=end.date())
        .exclude(category=Expense.Category.GOODS)
        .values_list("category")
        .annotate(value=Sum("amount"))
    )
    lines = [
        ("goods_cost", "تكلفة البضاعة", goods),
        ("ads", Expense.Category.ADS.label, recorded.get("ads", 0)),
        ("salaries", Expense.Category.SALARIES.label, recorded.get("salaries", 0)),
        ("packaging", Expense.Category.PACKAGING.label, recorded.get("packaging", 0)),
        ("return_fees", "رسوم الإرجاع", return_fees),
        ("other", Expense.Category.OTHER.label, recorded.get("other", 0)),
    ]
    total = sum(amount for _, _, amount in lines)
    breakdown = sorted(
        [{"key": key, "label": label, "amount": amount, "share": share(amount, total)} for key, label, amount in lines],
        key=lambda line: line["amount"],
        reverse=True,
    )
    return {
        "month": month_key(first_day),
        "sales": sales,
        "delivered_parcels": delivered.count(),
        "expenses_total": total,
        "expenses_share": share(total, sales),
        "net_profit": sales - total,
        "margin": share(sales - total, sales),
        "breakdown": breakdown,
    }


def campaign_performance(first_day, platform=None):
    start, end = month_bounds(first_day)
    campaigns = Campaign.objects.all()
    if platform:
        campaigns = campaigns.filter(platform=platform)
    rows = []
    for campaign in campaigns:
        spend = campaign.expenses.filter(date__gte=first_day, date__lt=end.date()).aggregate(value=Sum("amount"))["value"] or 0
        orders = campaign.orders.filter(created_at__gte=start, created_at__lt=end)
        total = orders.count()
        confirmed = orders.exclude(confirmed_at=None).count()
        delivered = orders.filter(status=OrderStatus.DELIVERED).count()
        rows.append(
            {
                "id": campaign.pk,
                "name": campaign.name,
                "platform": campaign.platform,
                "spend": spend,
                "orders": total,
                "confirmed": confirmed,
                "delivered": delivered,
                "cost_per_order": safe_cost(spend, total),
                "cost_per_delivered": safe_cost(spend, delivered),
            }
        )
    spend = sum(row["spend"] for row in rows)
    orders = sum(row["orders"] for row in rows)
    delivered = sum(row["delivered"] for row in rows)
    ranking = sorted(
        [{"id": row["id"], "name": row["name"], "cost_per_delivered": row["cost_per_delivered"]} for row in rows if row["delivered"]],
        key=lambda row: row["cost_per_delivered"],
    )
    return {
        "month": month_key(first_day),
        "totals": {
            "spend": spend,
            "campaigns": len(rows),
            "orders": orders,
            "delivered": delivered,
            "cost_per_order": safe_cost(spend, orders),
            "cost_per_delivered": safe_cost(spend, delivered),
        },
        "ranking": ranking,
        "campaigns": rows,
    }


def settlement_totals(settlement):
    shipments = list(settlement.shipments.select_related("order"))
    delivered = [s for s in shipments if s.stage == Stage.DELIVERED]
    returned = [s for s in shipments if s.stage == Stage.RETURNED]
    gross = sum(s.order.subtotal for s in delivered)
    fees = sum(s.return_fee for s in returned)
    net = gross - fees
    lines = {line.tracking_number: line.amount for line in settlement.statement_lines.all()}
    result = {
        "delivered_parcels": len(delivered),
        "returned_parcels": len(returned),
        "gross": gross,
        "return_fees": fees,
        "net": net,
        "statement_total": None,
        "difference": None,
        "missing_in_statement": [],
        "unknown_in_system": [],
        "amount_mismatches": [],
    }
    if settlement.statement_uploaded_at is None:
        return result
    expected = {s.tracking_number: s.order.subtotal for s in delivered}
    expected.update({s.tracking_number: -s.return_fee for s in returned})
    result["statement_total"] = sum(lines.values())
    result["difference"] = result["statement_total"] - net
    result["missing_in_statement"] = sorted(number for number in expected if number not in lines)
    result["unknown_in_system"] = sorted(number for number in lines if number not in expected)
    result["amount_mismatches"] = [
        {"tracking_number": number, "expected": expected[number], "statement": amount}
        for number, amount in sorted(lines.items())
        if number in expected and expected[number] != amount
    ]
    return result


def unsettled_shipments(period_start, period_end):
    start = local_datetime(period_start)
    end = local_datetime(period_end + timedelta(days=1))
    return Shipment.objects.filter(settlement=None).filter(
        Q(stage=Stage.DELIVERED, order__delivered_at__gte=start, order__delivered_at__lt=end)
        | Q(stage=Stage.RETURNED, order__returned_at__gte=start, order__returned_at__lt=end)
    )


@transaction.atomic
def create_settlement(period_start, period_end, user):
    overlapping = Settlement.objects.filter(period_start__lte=period_end, period_end__gte=period_start)
    if overlapping.exists():
        raise conflict("توجد دفعة تغطي هذه الفترة", "settlement_overlap")
    shipments = unsettled_shipments(period_start, period_end)
    if not shipments.exists():
        raise ApiError("لا توجد طرود في هذه الفترة", "no_parcels")
    settlement = Settlement.objects.create(period_start=period_start, period_end=period_end, created_by=user)
    shipments.update(settlement=settlement)
    return settlement


@transaction.atomic
def import_statement(settlement, uploaded):
    headers, rows = read_rows(uploaded)
    tracking_column = match_column(headers, TRACKING_ALIASES)
    amount_column = match_column(headers, AMOUNT_ALIASES)
    if tracking_column is None or amount_column is None:
        raise ApiError("يجب أن يحتوي الكشف على عمودي رقم التتبع والمبلغ", "statement_columns_missing")
    tracking_index = headers.index(tracking_column)
    amount_index = headers.index(amount_column)
    parsed = {}
    for row in rows:
        tracking = str(row[tracking_index] or "").strip()
        if not tracking:
            continue
        try:
            parsed[tracking] = int(float(str(row[amount_index]).replace(",", "").replace(" ", "")))
        except ValueError as error:
            raise ApiError(f"مبلغ غير صالح للطرد {tracking}", "statement_bad_amount") from error
    settlement.statement_lines.all().delete()
    StatementLine.objects.bulk_create(
        [StatementLine(settlement=settlement, tracking_number=number, amount=amount) for number, amount in parsed.items()]
    )
    settlement.statement_uploaded_at = timezone.now()
    settlement.save(update_fields=["statement_uploaded_at"])
    return settlement


def dues_summary(first_day):
    start, end = month_bounds(first_day)
    settlements = Settlement.objects.filter(period_start__gte=first_day, period_start__lt=end.date())
    net = paid = 0
    for settlement in settlements:
        amount = settlement_totals(settlement)["net"]
        net += amount
        if settlement.status == Settlement.Status.PAID:
            paid += amount
    held = Shipment.objects.filter(settlement=None, stage=Stage.DELIVERED).aggregate(value=Sum("order__subtotal"))["value"] or 0
    return {
        "month": month_key(first_day),
        "net": net,
        "paid": paid,
        "remaining": net - paid,
        "paid_share": share(paid, net),
        "not_yet_in_settlement": held,
    }
