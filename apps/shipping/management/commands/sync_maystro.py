from django.core.management.base import BaseCommand, CommandError

from apps.core.exceptions import ApiError
from apps.shipping.services import sync_parcels


class Command(BaseCommand):
    help = "Pull the latest parcel statuses from Maystro"

    def handle(self, *args, **options):
        try:
            result = sync_parcels()
        except ApiError as error:
            raise CommandError(str(error.detail)) from error
        self.stdout.write(f"checked={result['checked']} changed={result['changed']}")
