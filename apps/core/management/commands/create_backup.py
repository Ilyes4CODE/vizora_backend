from django.core.management.base import BaseCommand

from apps.core.backups import create_backup
from apps.core.models import Backup


class Command(BaseCommand):
    help = "Create an automatic encrypted backup"

    def handle(self, *args, **options):
        backup = create_backup(Backup.Kind.AUTO)
        self.stdout.write(backup.file_name)
