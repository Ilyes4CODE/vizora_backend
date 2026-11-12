import logging
from datetime import timedelta

from django.utils import timezone

from apps.core.backups import create_backup
from apps.core.exceptions import ApiError
from apps.core.models import Backup
from apps.shipping.maystro import MaystroError
from apps.shipping.models import MaystroConfig
from apps.shipping.services import sync_parcels

BACKUP_EVERY = timedelta(hours=24)
MAYSTRO_EVERY = timedelta(minutes=30)
logger = logging.getLogger("vizora.maintenance")


def backup_due(now):
    latest = Backup.objects.filter(kind=Backup.Kind.AUTO).order_by("-created_at").first()
    return latest is None or now - latest.created_at >= BACKUP_EVERY


def maystro_ready():
    config = MaystroConfig.load()
    return config.mode == MaystroConfig.Mode.LIVE and bool(config.encrypted_token)


def attempt(name, job, done):
    try:
        job()
    except (ApiError, MaystroError) as error:
        logger.warning("%s skipped: %s", name, getattr(error, "detail", error))
        return
    done.append(name)


def run_maintenance(state, now=None):
    now = now or timezone.now()
    done = []
    if backup_due(now):
        attempt("backup", lambda: create_backup(Backup.Kind.AUTO), done)
    last_maystro = state.get("maystro")
    if maystro_ready() and (last_maystro is None or now - last_maystro >= MAYSTRO_EVERY):
        state["maystro"] = now
        attempt("maystro", sync_parcels, done)
    return done
