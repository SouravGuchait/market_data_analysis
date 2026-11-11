"""Shared Django settings for every environment.

Environment variables are read once at import time.  Environment-specific
modules (``development``, ``production``, ``test``) are responsible for
providing safe defaults or failing fast when a required value is missing.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from apps.common.logging import ensure_log_directory

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env_str(name: str, default: str | None = None) -> str:
    """Return a required string environment variable."""

    value = os.getenv(name, default)
    if value is None or value.strip() == "":
        raise ImproperlyConfigured(f"Missing required environment variable: {name}")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    """Return a boolean environment variable using explicit truthy values."""

    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default

    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ImproperlyConfigured(f"Environment variable {name} must be a boolean value, got {raw!r}")


def env_int(name: str, default: int) -> int:
    """Return an integer environment variable."""

    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default

    try:
        return int(raw)
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"Environment variable {name} must be an integer, got {raw!r}"
        ) from exc


def env_list(name: str, default: tuple[str, ...] = ()) -> list[str]:
    """Return a comma-separated environment variable as a list."""

    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def resolve_path(value: str) -> Path:
    """Resolve a configured path relative to the project root."""

    path = Path(value).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set")

DEBUG = env_bool("DJANGO_DEBUG", default=False)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

if os.getenv("DEBUG", "True") != "True":
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "apps.common",
    "apps.accounts",
    "apps.exchanges",
    "apps.instruments",
    "apps.market_data",
    "apps.ingestion",
    "apps.portfolios",
    "apps.analytics",
    "apps.audit",
    "django_zxcvbn_password_validator",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.common.middleware.RequestIDMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env_str("DB_NAME"),
        "USER": env_str("DB_USER"),
        "PASSWORD": env_str("DB_PASSWORD"),
        "HOST": env_str("DB_HOST"),
        "PORT": env_str("DB_PORT"),
        "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
        "ATOMIC_REQUESTS": False,
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            "isolation_level": env_str("DB_ISOLATION_LEVEL", "read committed"),
        },
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env_str("CACHE_URL"),
        "KEY_PREFIX": env_str("CACHE_KEY_PREFIX", "mda"),
        "TIMEOUT": env_int("CACHE_DEFAULT_TIMEOUT", 300),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
    {
        # Enforces zxcvbn validation on the backend
        "NAME": "django_zxcvbn_password_validator.ZxcvbnPasswordValidator",
    },
]


LANGUAGE_CODE = "en-us"
TIME_ZONE = env_str("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

APP_VERSION = env_str("APP_VERSION", "0.1.0")
INGESTION_LOCAL_ROOT = str(resolve_path(env_str("INGESTION_LOCAL_ROOT", "sample_data")))
INGESTION_SCAN_INTERVAL_SECONDS = max(1, env_int("INGESTION_SCAN_INTERVAL_SECONDS", 60))
INGESTION_HASH_CHUNK_SIZE_BYTES = max(1, env_int("INGESTION_HASH_CHUNK_SIZE_BYTES", 1024 * 1024))
API_PREFIX = "api/v1/"

# ---------------------------------------------------------------------------
# DRF / OpenAPI
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_THROTTLE_CLASSES": (
        # ScopedRateThrottle looks at the 'throttle_scope' variable inside your views
        "rest_framework.throttling.ScopedRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon_register": "5/min",  # Max 5 registrations per minute per IP
        "anon_login": "5/min",  # Max 10 login attempts per minute per IP
        "anon_token_refresh": "5/min",  # Max 5 token refreshes per minute per IP
        "anon_password_reset": "3/min",  # Max 3 reset requests per minute per IP
        "anon_password_reset_confirm": "3/min",  # Max 3 confirmation requests per minute per IP
        "user_profile": "5/min",  # High limit for logged-in users checking profiles
        "user": "1000/hour",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,  # Issue new refresh token on every refresh call
    "BLACKLIST_AFTER_ROTATION": True,  # Blacklist the old refresh token
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# --- 4. EMAIL & RESET CONFIGURATION ---
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 25))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True") == "True"
EMAIL_TIMEOUT = 10

DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "noreply@marketanalytics.com")
FRONTEND_RESET_URL = os.getenv("FRONTEND_RESET_URL", "http://localhost:3000/reset-password")
PASSWORD_RESET_TIMEOUT_DAYS = 1  # 24 hours expiry

SPECTACULAR_SETTINGS = {
    "TITLE": "Market Data Analytics API",
    "DESCRIPTION": "Multi-user market data analytics backend.",
    "VERSION": APP_VERSION,
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": f"/{API_PREFIX}/",
    "COMPONENT_SPLIT_REQUEST": True,
    "SORT_OPERATIONS": False,
}

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------

CELERY_BROKER_URL = env_str("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env_str("CELERY_RESULT_BACKEND")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
# Keep Django's console + rotating-file handlers for worker and beat output
# instead of letting Celery replace the root logger configuration.
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
CELERY_BEAT_SCHEDULE = {
    "scan-enabled-ingestion-rules": {
        "task": "apps.ingestion.tasks.scan_enabled_ingestion_rules",
        "schedule": timedelta(seconds=INGESTION_SCAN_INTERVAL_SECONDS),
    },
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL = env_str("LOG_LEVEL", "INFO").upper()
LOG_FILE_PATH = str(resolve_path(env_str("LOG_FILE_PATH", "/var/log/mda/app.log")))
LOG_FILE_ENABLED = env_bool("LOG_FILE_ENABLED", default=True)
LOG_MAX_BYTES = env_int("LOG_MAX_BYTES", 10 * 1024 * 1024)
LOG_BACKUP_COUNT = env_int("LOG_BACKUP_COUNT", 5)

if LOG_FILE_ENABLED:
    try:
        ensure_log_directory(LOG_FILE_PATH)
    except PermissionError:
        if os.getenv("DJANGO_SETTINGS_MODULE") != "config.settings.development":
            raise
        LOG_FILE_PATH = str(BASE_DIR / "logs" / "app.log")
        ensure_log_directory(LOG_FILE_PATH)

LOG_HANDLERS = ["console"]
if LOG_FILE_ENABLED:
    LOG_HANDLERS.append("file")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "structured": {
            "()": "apps.common.logging.StructuredFormatter",
        },
    },
    "filters": {
        "request_id": {
            "()": "apps.common.logging.RequestIDFilter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "structured",
            "filters": ["request_id"],
        },
        **(
            {
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "filename": LOG_FILE_PATH,
                    "maxBytes": LOG_MAX_BYTES,
                    "backupCount": LOG_BACKUP_COUNT,
                    "encoding": "utf-8",
                    "formatter": "structured",
                    "filters": ["request_id"],
                    "delay": False,
                }
            }
            if LOG_FILE_ENABLED
            else {}
        ),
    },
    "root": {
        "handlers": LOG_HANDLERS,
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django": {
            "handlers": LOG_HANDLERS,
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "django.server": {
            "handlers": LOG_HANDLERS,
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "celery": {
            "handlers": LOG_HANDLERS,
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "apps": {
            "handlers": LOG_HANDLERS,
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}
