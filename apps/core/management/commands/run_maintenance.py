from django.core.management.base import BaseCommand

from apps.core.exceptions import ApiError
from apps.core.maintenance import run_maintenance
from apps.orders.sheets import sync_due
from apps.shipping.account import sync_account
from apps.shipping.live import live_mode, refresh_money, refresh_stock
from apps.shipping.models import MaystroConfig


class Command(BaseCommand):
    help = "Run the daily backup, the Maystro parcel sync and the Maystro stock refresh once, for a cron job"

    def handle(self, *args, **options):
        done = run_maintenance({})
        done += [f"sheet {line}" for line in sync_due()]
        if refresh_stock():
            done.append("stock")
        if refresh_money():
            done.append("balance and payments")
        if live_mode() and MaystroConfig.load().mirror_full_sync_at is None:
            try:
                result = sync_account(full=True)
            except ApiError as error:
                done.append(f"maystro full sync not finished: {error.detail}")
            else:
                done.append(f"maystro full sync ({result['fetched']} orders)")
        self.stdout.write(", ".join(done) if done else "nothing due")
