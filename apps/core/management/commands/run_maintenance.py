from django.core.management.base import BaseCommand

from apps.core.maintenance import run_maintenance
from apps.shipping.live import refresh_stock


class Command(BaseCommand):
    help = "Run the daily backup, the Maystro parcel sync and the Maystro stock refresh once, for a cron job"

    def handle(self, *args, **options):
        done = run_maintenance({})
        if refresh_stock():
            done.append("stock")
        self.stdout.write(", ".join(done) if done else "nothing due")
