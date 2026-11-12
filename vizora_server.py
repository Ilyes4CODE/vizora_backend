import argparse
import ctypes
import json
import logging
import os
import secrets
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

MAINTENANCE_FIRST_DELAY_SECONDS = 90
MAINTENANCE_EVERY_SECONDS = 300
PARENT_CHECK_SECONDS = 3
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0


def load_keys(data_dir):
    keys_file = data_dir / "keys.json"
    if keys_file.exists():
        return json.loads(keys_file.read_text(encoding="utf-8"))
    from cryptography.fernet import Fernet

    keys = {"secret_key": secrets.token_urlsafe(50), "field_key": Fernet.generate_key().decode()}
    temporary = keys_file.with_suffix(".tmp")
    temporary.write_text(json.dumps(keys), encoding="utf-8")
    temporary.replace(keys_file)
    return keys


def allowed_hosts(extra=""):
    hosts = ["127.0.0.1", "localhost"]
    for host in extra.split(","):
        host = host.strip()
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def prepare_environment(data_dir, extra_hosts=""):
    for folder in (data_dir, data_dir / "backups", data_dir / "media", data_dir / "logs"):
        folder.mkdir(parents=True, exist_ok=True)
    keys = load_keys(data_dir)
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    os.environ["DJANGO_SECRET_KEY"] = keys["secret_key"]
    os.environ["FIELD_ENCRYPTION_KEY"] = keys["field_key"]
    os.environ["DATABASE_URL"] = f"sqlite:///{(data_dir / 'vizora.sqlite3').as_posix()}"
    os.environ["BACKUP_DIR"] = str(data_dir / "backups")
    os.environ["MEDIA_DIR"] = str(data_dir / "media")
    os.environ["DJANGO_DEBUG"] = "false"
    os.environ["DJANGO_ALLOWED_HOSTS"] = ",".join(allowed_hosts(extra_hosts))
    os.environ["CORS_ALLOWED_ORIGINS"] = "app://vizora"


class QuietUnknownHosts(logging.Filter):
    def filter(self, record):
        if record.name == "django.security.DisallowedHost":
            record.levelno = logging.WARNING
            record.levelname = "WARNING"
            record.exc_info = None
            record.exc_text = None
        return True


def configure_logging(data_dir):
    handler = RotatingFileHandler(data_dir / "logs" / "server.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(QuietUnknownHosts())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def parent_alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) != WAIT_OBJECT_0
    finally:
        kernel.CloseHandle(handle)


def watch_parent(pid):
    while parent_alive(pid):
        time.sleep(PARENT_CHECK_SECONDS)
    logging.getLogger("vizora.server").info("desktop app closed, stopping server")
    os._exit(0)


def maintenance_loop():
    from django.db import connection

    from apps.core.maintenance import run_maintenance

    logger = logging.getLogger("vizora.maintenance")
    state = {}
    time.sleep(MAINTENANCE_FIRST_DELAY_SECONDS)
    while True:
        try:
            done = run_maintenance(state)
            if done:
                logger.info("finished: %s", ", ".join(done))
        except Exception:
            logger.exception("maintenance failed")
        finally:
            connection.close()
        time.sleep(MAINTENANCE_EVERY_SECONDS)


def main():
    parser = argparse.ArgumentParser(prog="vizora-server")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--allowed-hosts", default="")
    parser.add_argument("--no-maintenance", action="store_true")
    options = parser.parse_args()

    data_dir = Path(options.data_dir).resolve()
    prepare_environment(data_dir, options.allowed_hosts)
    configure_logging(data_dir)
    logger = logging.getLogger("vizora.server")

    import django
    from django.core.management import call_command

    django.setup()
    call_command("migrate", interactive=False, verbosity=0)
    logger.info("database ready at %s", data_dir)
    if options.host not in ("127.0.0.1", "localhost"):
        logger.info("phone access open on %s:%s for %s", options.host, options.port, os.environ["DJANGO_ALLOWED_HOSTS"])

    if options.parent_pid:
        threading.Thread(target=watch_parent, args=(options.parent_pid,), daemon=True).start()
    if not options.no_maintenance:
        threading.Thread(target=maintenance_loop, daemon=True).start()

    from django.core.wsgi import get_wsgi_application
    from waitress import serve

    print(f"VIZORA_READY {options.port}", flush=True)
    serve(get_wsgi_application(), host=options.host, port=options.port, threads=8, ident="vizora")


if __name__ == "__main__":
    sys.exit(main())
