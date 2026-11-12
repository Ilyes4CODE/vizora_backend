from django.core.management.base import BaseCommand, CommandError

from apps.core.exceptions import ApiError
from apps.shipping.account import sync_account


class Command(BaseCommand):
    help = "Copy the store's Maystro orders into the local mirror used for money and statistics"

    def add_arguments(self, parser):
        parser.add_argument("--full", action="store_true")

    def handle(self, *args, **options):
        try:
            result = sync_account(full=options["full"])
        except ApiError as error:
            raise CommandError(str(error.detail)) from error
        self.stdout.write(
            f"full={result['full']} pages={result['pages']} fetched={result['fetched']} created={result['created']} "
            f"updated={result['updated']} linked={result['linked']} products={result['products']} points={result['points']}"
        )
