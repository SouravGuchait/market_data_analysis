"""Production settings for ``prod_up``.

When ``MDA_PROD_ENV_FILE`` is set (for example by ``scripts/install_dev_commands.sh``
or CI), that file is loaded before the shared settings. Process environment
values win over the file. If the variable is unset, or the path is missing,
loading is skipped so Compose ``env_file`` or the host environment can supply
values directly.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_mda_prod_env_file = os.environ.get("MDA_PROD_ENV_FILE", "").strip()
if _mda_prod_env_file:
    _prod_env_path = Path(_mda_prod_env_file).expanduser()
    if _prod_env_path.is_file():
        load_dotenv(_prod_env_path, override=False)

from django.core.exceptions import ImproperlyConfigured  # noqa: E402

from .base import *  # noqa: E402,F403
from .base import env_bool, env_int, env_list, env_str, resolve_path  # noqa: E402

DEBUG = False

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
        "OPTIONS": {
            "socket_connect_timeout": env_int("CACHE_SOCKET_CONNECT_TIMEOUT", 5),
            "socket_timeout": env_int("CACHE_SOCKET_TIMEOUT", 5),
        },
    }
}

CELERY_BROKER_URL = env_str("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env_str("CELERY_RESULT_BACKEND")

INGESTION_LOCAL_ROOT = str(resolve_path(env_str("INGESTION_LOCAL_ROOT", "/data/ingestion")))
INGESTION_SYSTEM_ROOT = str(resolve_path(env_str("INGESTION_SYSTEM_ROOT", "/var/lib/mda/system")))
INGESTION_S3_BUCKET = os.getenv("INGESTION_S3_BUCKET", "").strip()
INGESTION_S3_PREFIX = os.getenv("INGESTION_S3_PREFIX", "").strip()
INGESTION_S3_REGION = os.getenv("INGESTION_S3_REGION", "").strip()

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")

if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set")

if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must contain at least one host")

if not CSRF_TRUSTED_ORIGINS:
    raise ImproperlyConfigured("DJANGO_CSRF_TRUSTED_ORIGINS must contain at least one origin")

if "LOG_FILE_PATH" not in os.environ:
    raise ImproperlyConfigured("LOG_FILE_PATH must be set explicitly in production")

SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=False)
SESSION_COOKIE_SECURE = env_bool("DJANGO_SESSION_COOKIE_SECURE", default=False)
CSRF_COOKIE_SECURE = env_bool("DJANGO_CSRF_COOKIE_SECURE", default=False)
SESSION_COOKIE_HTTPONLY = True
SECURE_HSTS_SECONDS = env_int("DJANGO_SECURE_HSTS_SECONDS", 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
X_FRAME_OPTIONS = "DENY"
