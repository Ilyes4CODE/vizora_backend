import argparse
import base64
import getpass
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import quote

BACKEND = Path(__file__).resolve().parent.parent
PORTS = {"postgres": 5432, "mysql": 3306}


def database_url(engine, name, user, password, host):
    return f"{engine}://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{PORTS[engine]}/{quote(name, safe='')}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the .env file of the Vizora server")
    parser.add_argument("--domain", required=True)
    parser.add_argument("--engine", choices=sorted(PORTS), required=True)
    parser.add_argument("--db-name", required=True)
    parser.add_argument("--db-user", required=True)
    parser.add_argument("--db-host", default="localhost")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--output", default=str(BACKEND / ".env"))
    options = parser.parse_args(argv)

    target = Path(options.output)
    if target.exists() and not options.force:
        sys.exit(f"{target} already exists. Keep it: its FIELD_ENCRYPTION_KEY opens the saved Maystro token and the backups.")
    password = getpass.getpass("Database password: ")
    if not password:
        sys.exit("The database password is empty.")
    lines = [
        f"DJANGO_SECRET_KEY={secrets.token_urlsafe(50)}",
        f"FIELD_ENCRYPTION_KEY={base64.urlsafe_b64encode(os.urandom(32)).decode()}",
        "DJANGO_DEBUG=false",
        f"DJANGO_ALLOWED_HOSTS={options.domain}",
        "CORS_ALLOWED_ORIGINS=app://vizora",
        f"DATABASE_URL={database_url(options.engine, options.db_name, options.db_user, password, options.db_host)}",
        f"MEDIA_DIR={BACKEND / 'media'}",
        f"BACKUP_DIR={BACKEND / 'backups'}",
    ]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(target, 0o600)
    print(f"wrote {target}")
    print("Copy FIELD_ENCRYPTION_KEY somewhere safe: without it the backups cannot be opened.")


if __name__ == "__main__":
    main()
