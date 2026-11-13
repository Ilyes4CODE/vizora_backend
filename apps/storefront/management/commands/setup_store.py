from django.core.management.base import BaseCommand

from apps.storefront.setup import run_setup


class Command(BaseCommand):
    help = "Create the website pages and put the Maystro products on the website, keeping anything already set up"

    def handle(self, *args, **options):
        for line in run_setup():
            self.stdout.write(line)
