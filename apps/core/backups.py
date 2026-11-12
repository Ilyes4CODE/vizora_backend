import gzip
import json
import tempfile
import uuid
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.color import no_style
from django.db import connection, transaction
from django.utils import timezone

from .crypto import InvalidToken, decrypt_bytes, encrypt_bytes
from .exceptions import ApiError
from .models import Backup

DATA_LABELS = [
    "core.wilaya",
    "core.commune",
    "core.businessprofile",
    "core.securitysettings",
    "core.auditlog",
    "accounts",
    "catalog",
    "finance",
    "orders",
    "shipping",
]
PRESERVED_TABLES = {"django_content_type", "auth_permission", Backup._meta.db_table}


def backup_directory():
    directory = Path(settings.BACKUP_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def backup_path(backup):
    return backup_directory() / backup.file_name


def create_backup(kind, user=None):
    buffer = StringIO()
    call_command("dumpdata", *DATA_LABELS, stdout=buffer)
    payload = encrypt_bytes(gzip.compress(buffer.getvalue().encode("utf-8")))
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    file_name = f"vizora-{stamp}-{uuid.uuid4().hex[:8]}.enc"
    (backup_directory() / file_name).write_bytes(payload)
    backup = Backup.objects.create(
        file_name=file_name, size_bytes=len(payload), kind=kind, created_by_name=user.full_name if user else ""
    )
    prune_automatic_backups()
    return backup


def prune_automatic_backups():
    expired = Backup.objects.filter(kind=Backup.Kind.AUTO).order_by("-created_at", "-id")[settings.BACKUP_RETENTION:]
    for backup in expired:
        delete_backup(backup)


def delete_backup(backup):
    backup_path(backup).unlink(missing_ok=True)
    backup.delete()


def read_backup(backup):
    path = backup_path(backup)
    if not path.exists():
        raise ApiError("ملف النسخة غير موجود", "backup_missing", 404)
    try:
        text = gzip.decompress(decrypt_bytes(path.read_bytes())).decode("utf-8")
        json.loads(text)
    except (InvalidToken, OSError, ValueError) as error:
        raise ApiError("ملف النسخة تالف أو لا يمكن فك تشفيره", "backup_corrupt", 422) from error
    return text


def restore_backup(backup, user=None):
    text = read_backup(backup)
    safety_copy = create_backup(Backup.Kind.PRE_RESTORE, user=user)
    tables = [
        name
        for name in connection.introspection.django_table_names(only_existing=True)
        if name not in PRESERVED_TABLES
    ]
    with tempfile.TemporaryDirectory() as folder:
        fixture = Path(folder) / "restore.json"
        fixture.write_text(text, encoding="utf-8")
        with transaction.atomic():
            connection.check_constraints()
            statements = connection.ops.sql_flush(no_style(), tables, allow_cascade=True)
            connection.ops.execute_sql_flush(statements)
            call_command("loaddata", str(fixture), verbosity=0)
    return safety_copy
