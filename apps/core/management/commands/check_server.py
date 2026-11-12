import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.accounts.models import Role, User
from apps.core.crypto import decrypt_text, encrypt_text
from apps.shipping.maystro import MaystroError, get_client
from apps.shipping.models import MaystroConfig

MINIMUM_POSTGRES = 140000


class Command(BaseCommand):
    help = "Check that this server is ready: database, migrations, keys, folders, admin account and Maystro"

    def add_arguments(self, parser):
        parser.add_argument("--domain", default="")

    def handle(self, *args, **options):
        self.failures = 0
        self.verify("debug mode is off", lambda: not settings.DEBUG, "set DJANGO_DEBUG=false in .env")
        if options["domain"]:
            self.verify(f"{options['domain']} is an allowed host", lambda: options["domain"] in settings.ALLOWED_HOSTS, "add the domain to DJANGO_ALLOWED_HOSTS")
        self.verify("desktop app origin is allowed", lambda: "app://vizora" in settings.CORS_ALLOWED_ORIGINS, "set CORS_ALLOWED_ORIGINS=app://vizora")
        self.verify("database answers", self.database, "check DATABASE_URL, the database user and its password")
        self.verify("every migration is applied", self.migrations, "run: python manage.py migrate")
        self.verify("encryption key works", lambda: decrypt_text(encrypt_text("vizora")) == "vizora", "FIELD_ENCRYPTION_KEY is missing or invalid")
        self.verify("media folder is writable", lambda: self.writable(Path(settings.MEDIA_ROOT)), "create the media folder next to manage.py")
        self.verify("backup folder is writable", lambda: self.writable(Path(settings.BACKUP_DIR)), "create the backups folder next to manage.py")
        self.verify("an admin account exists", lambda: User.objects.filter(role=Role.ADMIN, is_active=True).exists(), "run: python manage.py createsuperuser")
        self.maystro()
        self.stdout.write("")
        if self.failures:
            self.stdout.write(self.style.ERROR(f"{self.failures} check(s) failed"))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("server is ready"))

    def verify(self, name, test, hint):
        try:
            result = test()
        except Exception as error:
            result = False
            hint = f"{hint} ({error.__class__.__name__}: {error})"
        if result is False or result is None:
            self.failures += 1
            self.stdout.write(self.style.ERROR(f"FAIL {name}: {hint}"))
            return
        detail = f" ({result})" if isinstance(result, str) else ""
        self.stdout.write(self.style.SUCCESS(f"PASS {name}{detail}"))

    def database(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        vendor = connection.vendor
        if vendor == "postgresql":
            version = connection.pg_version
            if version < MINIMUM_POSTGRES:
                raise RuntimeError(f"PostgreSQL {version // 10000} is too old, Django needs 14 or newer")
            return f"PostgreSQL {version // 10000}"
        if vendor == "mysql":
            return f"{'MariaDB' if connection.mysql_is_mariadb else 'MySQL'} {'.'.join(str(part) for part in connection.mysql_version)}"
        return vendor

    def migrations(self):
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if pending:
            raise RuntimeError(f"{len(pending)} migration(s) not applied")
        return True

    def writable(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(dir=folder)
        os.close(handle)
        os.unlink(name)
        return str(folder)

    def maystro(self):
        config = MaystroConfig.load()
        if not config.encrypted_token:
            self.stdout.write(self.style.WARNING("INFO no Maystro token yet, enter it in the desktop app settings"))
            return

        def ping():
            try:
                get_client(config).ping()
            except MaystroError as error:
                raise RuntimeError(error.message) from error
            return True

        self.verify("Maystro answers with the saved token", ping, "enter the Maystro token again in the desktop app settings")
