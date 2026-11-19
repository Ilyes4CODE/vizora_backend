import os
from datetime import timedelta
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name):
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def env_required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise ImproperlyConfigured(f"Missing required environment variable: {name}")
    return value


SECRET_KEY = env_required("DJANGO_SECRET_KEY")
FIELD_ENCRYPTION_KEY = env_required("FIELD_ENCRYPTION_KEY")
DEBUG = env_bool("DJANGO_DEBUG")
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS") or ["localhost", "127.0.0.1"]
STORE_HOSTS = env_list("STORE_HOSTS") or ["vizorawear.com", "www.vizorawear.com"]
ALLOWED_HOSTS += [host for host in STORE_HOSTS if host not in ALLOWED_HOSTS]

CONVERSIONS_META_URL = os.environ.get("CONVERSIONS_META_URL", "https://graph.facebook.com/v23.0")
CONVERSIONS_TIKTOK_URL = os.environ.get("CONVERSIONS_TIKTOK_URL", "https://business-api.tiktok.com/open_api/v1.3/event/track/")
CONVERSIONS_SYNC = env_bool("CONVERSIONS_SYNC")
STORE_PUBLIC_URL = os.environ.get("STORE_PUBLIC_URL", "").rstrip("/")
STORE_PREVIEW_ANCESTORS = env_list("STORE_PREVIEW_ANCESTORS") or ["app://vizora"]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
    "apps.core",
    "apps.accounts",
    "apps.catalog",
    "apps.finance",
    "apps.orders",
    "apps.shipping",
    "apps.reports",
    "apps.storefront",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.NoStoreApiMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "apps.storefront.middleware.StoreHostMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]

DATABASES = {
    "default": dj_database_url.parse(
        os.environ.get("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=60,
    )
}

if DATABASES["default"]["ENGINE"].endswith("sqlite3"):
    DATABASES["default"].setdefault("OPTIONS", {}).update(
        {
            "transaction_mode": "IMMEDIATE",
            "timeout": 30,
            "init_command": "PRAGMA journal_mode=WAL;PRAGMA synchronous=NORMAL;",
        }
    )

if DATABASES["default"]["ENGINE"].endswith("mysql"):
    try:
        import MySQLdb
    except ImportError:
        import pymysql

        pymysql.install_as_MySQLdb()
    DATABASES["default"].setdefault("OPTIONS", {}).update(
        {"charset": "utf8mb4", "init_command": "SET sql_mode='STRICT_TRANS_TABLES'"}
    )

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ar"
TIME_ZONE = "Africa/Algiers"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = DEBUG
WHITENOISE_MAX_AGE = 60 * 60 * 24 * 30
MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_DIR") or BASE_DIR / "media")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR") or BASE_DIR / "backups")
BACKUP_RETENTION = 30
MAX_STAFF_ACCOUNTS = 6
MAX_IMPORT_FILE_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 5000
DUPLICATE_WINDOW_DAYS = 7
CALL_RETRY_HOURS = 2
PARCEL_STUCK_HOURS = 48

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.accounts.authentication.ActivityJWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["apps.accounts.permissions.HasSection"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_RATES": {"login": "10/min"},
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
    "EXCEPTION_HANDLER": "apps.core.handlers.api_exception_handler",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Vizora Global CRM API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "DISABLE_ERRORS_AND_WARNINGS": True,
    "SERVE_PERMISSIONS": [
        "rest_framework.permissions.AllowAny" if DEBUG else "rest_framework.permissions.IsAuthenticated"
    ],
}

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_ALL_ORIGINS = DEBUG

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

TEST_RUNNER = "tests.runner.ApiCoverageRunner"
