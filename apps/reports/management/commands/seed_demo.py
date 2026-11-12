import random
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.catalog.models import Product, StockMovement
from apps.core.models import Wilaya
from apps.finance.models import Campaign, Expense, Settlement, StatementLine
from apps.finance.services import create_settlement
from apps.orders.models import Order, OrderEvent, OrderItem, OrderStatus
from apps.orders.services import next_morning
from apps.shipping.models import Shipment, Stage

DAILY_OCTOBER = [52, 48, 61, 57, 55, 63, 66, 58, 49, 54, 62, 60, 66, 68, 59, 53, 50, 64, 67, 62, 58, 66, 68, 61, 55, 51, 60, 65, 66, 58, 60]
BATCHES = [(1, 7, 238), (8, 14, 251), (15, 21, 266), (22, 31, 306)]
DELIVERED_WILAYAS = {16: 214, 31: 143, 19: 112, 9: 96, 25: 88}
RETURNED_WILAYAS = {16: 29, 31: 24, 19: 17, 9: 13, 25: 21}
OTHER_DELIVERED = 408
OTHER_RETURNED = 94
FIRST_NAMES = ["كريم", "هاجر", "رضا", "إيمان", "توفيق", "لمياء", "عادل", "صبرينة", "مراد", "نبيل", "خديجة", "سفيان", "بلال", "أسماء", "عمر"]
LAST_NAMES = ["مزيان", "سعيداني", "عمراني", "بن عيسى", "شريف", "خليفي", "نايلي", "دحماني", "زروقي", "عثماني", "سلامي", "قادري", "رحماني", "لعريبي", "حمادي"]


def expand(mapping):
    return [key for key, count in mapping.items() for _ in range(count)]


class Command(BaseCommand):
    help = "Fill an empty development database with the sample data shown in the designs"

    def add_arguments(self, parser):
        parser.add_argument("--password", required=True)

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo runs only when DJANGO_DEBUG is true")
        if Order.objects.exists() or Product.objects.exists():
            raise CommandError("The database already has data")
        self.rng = random.Random(2026)
        self.now = timezone.now()
        self.orders = []
        self.items = []
        self.shipments = []
        self.sequence = 0
        with transaction.atomic():
            self.create_people(options["password"])
            self.create_catalog()
            self.create_october()
            self.create_current_month()
            self.persist()
            self.finish()
        self.stdout.write(self.style.SUCCESS(f"Created {Order.objects.count()} orders"))

    def create_people(self, password):
        people = [
            ("amine", "أمين بوزيد", Role.ADMIN),
            ("sara", "سارة قاسمي", Role.CONFIRMATION),
            ("yacine", "ياسين حداد", Role.CONFIRMATION),
            ("nesrine", "نسرين بلقاسم", Role.CONFIRMATION),
            ("ilyas", "إلياس مناصري", Role.STOCK),
        ]
        self.users = {
            username: User.objects.create_user(username=username, password=password, full_name=name, role=role)
            for username, name, role in people
        }
        self.agents = [self.users["sara"], self.users["yacine"], self.users["nesrine"]]

    def create_catalog(self):
        self.product_a = Product.objects.create(name="المنتج الأول", sku="P-1", price=3900, cost=1400, low_stock_threshold=150, on_hand=468)
        self.product_b = Product.objects.create(name="المنتج الثاني", sku="P-2", price=5500, cost=2100, low_stock_threshold=80, on_hand=64)
        for offset in range(1, 31):
            for product, quantity in ((self.product_a, 29), (self.product_b, 13)):
                movement = StockMovement.objects.create(
                    product=product, kind=StockMovement.Kind.SHIP, quantity=-quantity, balance_after=product.on_hand
                )
                StockMovement.objects.filter(pk=movement.pk).update(created_at=self.now - timedelta(days=offset))
        self.campaigns = [
            (Campaign.objects.create(name="Facebook · المنتج الأول", platform="facebook", product=self.product_a), 268000, 864, 622, 506, [self.product_a] * 506),
            (Campaign.objects.create(name="Facebook · المنتج الثاني", platform="facebook", product=self.product_b), 176000, 438, 315, 249, [self.product_b] * 249),
            (Campaign.objects.create(name="Instagram · ريلز المنتج الأول", platform="instagram", product=self.product_a), 104000, 342, 247, 196, [self.product_a] * 196),
            (Campaign.objects.create(name="Instagram · قصص", platform="instagram"), 64000, 198, 142, 110, [self.product_a] * 40 + [self.product_b] * 70),
        ]
        for campaign, spend, *_ in self.campaigns:
            Expense.objects.create(date=date(2026, 10, 25), title=f"إنفاق {campaign.name}", category=Expense.Category.ADS, amount=spend, campaign=campaign, created_by=self.users["amine"])
        for day, title, category, amount in [
            (31, "رواتب شهر أكتوبر", Expense.Category.SALARIES, 240000),
            (28, "شراء علب تغليف", Expense.Category.PACKAGING, 32400),
            (12, "مواد تغليف", Expense.Category.PACKAGING, 47160),
            (20, "إيجار المخزن", Expense.Category.OTHER, 30000),
            (22, "مصاريف متنوعة", Expense.Category.OTHER, 15000),
            (15, "دفعة بضاعة المنتج الأول", Expense.Category.GOODS, 420000),
        ]:
            Expense.objects.create(date=date(2026, 10, day), title=title, category=category, amount=amount, created_by=self.users["amine"])

    def moment(self, day, low, high):
        return timezone.make_aware(datetime.combine(day, time(self.rng.randint(low, high), self.rng.randint(0, 59))))

    def add_order(self, product, status, wilaya_code, created_at, campaign=None, **fields):
        self.sequence += 1
        desk = self.rng.random() < 0.25
        fee = 400 if desk else 600
        agent = self.rng.choice(self.agents)
        order = Order(
            reference=f"VZ-{10000 + self.sequence}",
            customer_name=f"{self.rng.choice(FIRST_NAMES)} {self.rng.choice(LAST_NAMES)}",
            phone=f"0{self.rng.choice('567')}{self.rng.randint(0, 99999999):08d}",
            wilaya_id=wilaya_code,
            commune="",
            delivery_type="desk" if desk else "home",
            subtotal=product.price,
            delivery_fee=fee,
            total=product.price + fee,
            status=status,
            campaign=campaign,
            assigned_to=agent,
            call_attempts=1,
            created_at=created_at,
            **fields,
        )
        self.orders.append(order)
        self.items.append(product)
        return order

    def add_shipment(self, order, stage, updated_at, return_fee=0):
        self.shipments.append((order, stage, updated_at, return_fee))

    def create_october(self):
        days = [index + 1 for index, count in enumerate(DAILY_OCTOBER) for _ in range(count)]
        delivered_days = []
        other_days = []
        for first, last, count in BATCHES:
            pool = [day for day in days if first <= day <= last]
            self.rng.shuffle(pool)
            delivered_days += [(day, last) for day in pool[:count]]
            other_days += pool[count:]
        delivered_slots = []
        shipped_slots = []
        cancelled_slots = []
        for campaign, _, orders, confirmed, delivered, products in self.campaigns:
            delivered_slots += [(campaign, product) for product in products]
            fallback = campaign.product
            shipped_slots += [(campaign, fallback) for _ in range(confirmed - delivered)]
            cancelled_slots += [(campaign, fallback) for _ in range(orders - confirmed)]
        self.rng.shuffle(delivered_slots)
        codes = [wilaya.code for wilaya in Wilaya.objects.all() if wilaya.code not in DELIVERED_WILAYAS]
        delivered_wilayas = expand(DELIVERED_WILAYAS) + [self.rng.choice(codes) for _ in range(OTHER_DELIVERED)]
        self.rng.shuffle(delivered_wilayas)
        for (day, batch_end), (campaign, product), wilaya in zip(delivered_days, delivered_slots, delivered_wilayas):
            created = self.moment(date(2026, 10, day), 8, 12)
            delivered_at = self.moment(date(2026, 10, min(day + 1, batch_end)), 15, 18)
            order = self.add_order(product, OrderStatus.DELIVERED, wilaya, created, campaign, confirmed_at=created + timedelta(hours=1), shipped_at=created + timedelta(hours=2), delivered_at=delivered_at)
            self.add_shipment(order, Stage.DELIVERED, delivered_at)
        self.rng.shuffle(other_days)
        self.rng.shuffle(shipped_slots)
        outcomes = [OrderStatus.RETURNED] * 198 + [OrderStatus.SHIPPING] * 67
        self.rng.shuffle(outcomes)
        returned_wilayas = expand(RETURNED_WILAYAS) + [self.rng.choice(codes) for _ in range(OTHER_RETURNED)]
        self.rng.shuffle(returned_wilayas)
        day_iterator = iter(other_days)
        wilaya_iterator = iter(returned_wilayas)
        self.october_transit = {self.product_a.pk: 0, self.product_b.pk: 0}
        self.transit_orders = []
        for (campaign, product), outcome in zip(shipped_slots, outcomes):
            product = product or self.rng.choice([self.product_a, self.product_b])
            day = next(day_iterator)
            created = self.moment(date(2026, 10, day), 8, 12)
            base = {"confirmed_at": created + timedelta(hours=1), "shipped_at": created + timedelta(hours=2)}
            if outcome == OrderStatus.RETURNED:
                returned_at = self.moment(date(2026, 10, min(day + 3, 31)), 15, 18)
                order = self.add_order(product, outcome, next(wilaya_iterator), created, campaign, returned_at=returned_at, **base)
                self.add_shipment(order, Stage.RETURNED, returned_at, 350)
            else:
                order = self.add_order(product, outcome, self.rng.choice(codes), created, campaign, **base)
                self.october_transit[product.pk] += 1
                self.transit_orders.append(order)
        for campaign, product in cancelled_slots:
            day = next(day_iterator)
            created = self.moment(date(2026, 10, day), 8, 12)
            self.add_order(product or self.product_a, OrderStatus.CANCELLED, self.rng.choice(codes), created, campaign, cancelled_at=created + timedelta(hours=2), cancel_reason="cancelled")

    def create_current_month(self):
        today = timezone.localdate()
        codes = [wilaya.code for wilaya in Wilaya.objects.all()]
        earlier = [today.replace(day=day) for day in range(1, max(today.day, 2))] or [today]
        self.today_confirmed = []

        def created_on(day=None):
            return min(self.moment(day or self.rng.choice(earlier), 8, 12), self.now)

        for _ in range(31):
            self.add_order(self.product_a if self.rng.random() < 0.7 else self.product_b, OrderStatus.PENDING, self.rng.choice(codes), created_on(today))
        confirmed_products = [self.product_a] * 56 + [self.product_b] * 18
        self.rng.shuffle(confirmed_products)
        for index, product in enumerate(confirmed_products):
            is_today = index < 52
            created = created_on(today if is_today else None)
            order = self.add_order(product, OrderStatus.CONFIRMED, self.rng.choice(codes), created, confirmed_at=created)
            if is_today:
                self.today_confirmed.append(order)
        shipping_products = (
            [self.product_a] * (98 - self.october_transit[self.product_a.pk])
            + [self.product_b] * (45 - self.october_transit[self.product_b.pk])
        )
        for product in shipping_products:
            created = created_on()
            order = self.add_order(product, OrderStatus.SHIPPING, self.rng.choice(codes), created, confirmed_at=created, shipped_at=created)
            self.transit_orders.append(order)
        stages = [Stage.TRANSIT] * 58 + [Stage.OUT] * 41 + [Stage.DESK] * 35 + [Stage.FAILED] * 9
        self.rng.shuffle(stages)
        due = 0
        for order, stage in zip(self.transit_orders, stages):
            self.add_shipment(order, stage, self.now)
            if stage != Stage.TRANSIT:
                if due < 12:
                    due += 1
                else:
                    order.suivi_next_at = next_morning()
        for _ in range(186):
            created = created_on()
            order = self.add_order(self.product_a, OrderStatus.DELIVERED, self.rng.choice(codes), created, confirmed_at=created, shipped_at=created, delivered_at=created)
            self.add_shipment(order, Stage.DELIVERED, created)
        for _ in range(22):
            created = created_on()
            order = self.add_order(self.product_a, OrderStatus.RETURNED, self.rng.choice(codes), created, confirmed_at=created, shipped_at=created, returned_at=created)
            self.add_shipment(order, Stage.RETURNED, created, 350)
        for _ in range(56):
            created = created_on()
            self.add_order(self.product_a, OrderStatus.CANCELLED, self.rng.choice(codes), created, cancelled_at=created, cancel_reason="cancelled")

    def persist(self):
        Order.objects.bulk_create(self.orders, batch_size=500)
        OrderItem.objects.bulk_create(
            [
                OrderItem(order=order, product=product, quantity=1, unit_price=product.price, unit_cost=product.cost)
                for order, product in zip(self.orders, self.items)
            ],
            batch_size=500,
        )
        Shipment.objects.bulk_create(
            [
                Shipment(
                    order=order,
                    tracking_number=f"MD-{770000 + index}",
                    external_id=str(index),
                    stage=stage,
                    return_fee=fee,
                    last_update_at=updated,
                    created_at=order.shipped_at or order.created_at,
                )
                for index, (order, stage, updated, fee) in enumerate(self.shipments, start=1)
            ],
            batch_size=500,
        )
        counts = [(self.agents[0], 21), (self.agents[1], 18), (self.agents[2], 13)]
        agents = [agent for agent, count in counts for _ in range(count)]
        OrderEvent.objects.bulk_create(
            [
                OrderEvent(order=order, stage=OrderEvent.Stage.CONFIRM, outcome="confirmed", message=f"مؤكدة · {agent.full_name}", user=agent, created_at=order.created_at)
                for order, agent in zip(self.today_confirmed, agents)
            ]
        )

    def finish(self):
        admin = self.users["amine"]
        created = [create_settlement(date(2026, 10, first), date(2026, 10, last), admin) for first, last, _ in BATCHES]
        for settlement, paid_on in zip(created[:3], [date(2026, 10, 12), date(2026, 10, 19), date(2026, 10, 26)]):
            settlement.status = Settlement.Status.PAID
            settlement.paid_on = paid_on
            settlement.save(update_fields=["status", "paid_on"])
        latest = created[3]
        shipments = list(latest.shipments.select_related("order"))
        skipped = next(s for s in shipments if s.stage == Stage.DELIVERED and s.order.subtotal == 3900)
        StatementLine.objects.bulk_create(
            [
                StatementLine(
                    settlement=latest,
                    tracking_number=s.tracking_number,
                    amount=s.order.subtotal if s.stage == Stage.DELIVERED else -s.return_fee,
                )
                for s in shipments
                if s.pk != skipped.pk
            ]
        )
        latest.statement_uploaded_at = self.now
        latest.save(update_fields=["statement_uploaded_at"])
