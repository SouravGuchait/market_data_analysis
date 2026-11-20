"""Local development settings for ``dev_up``.

``docker-compose.yml`` starts this module. The env file is loaded before the
shared settings so database, cache, and broker values are already present.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = Path(os.environ.get("MDA_ENV_FILE", BASE_DIR / ".env")).expanduser()
load_dotenv(ENV_FILE)

from .base import *  # noqa: E402,F403
from .base import env_int, env_str, resolve_path  # noqa: E402

DEBUG = True
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env_str("DB_NAME"),
        "USER": env_str("DB_USER"),
        "PASSWORD": env_str("DB_PASSWORD"),
        "HOST": env_str("DB_HOST", "127.0.0.1"),
        "PORT": env_str("DB_PORT", "3306"),
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
        "LOCATION": env_str("CACHE_URL", "redis://127.0.0.1:6379/1"),
        "KEY_PREFIX": env_str("CACHE_KEY_PREFIX", "mda"),
        "TIMEOUT": env_int("CACHE_DEFAULT_TIMEOUT", 300),
        "OPTIONS": {
            "socket_connect_timeout": env_int("CACHE_SOCKET_CONNECT_TIMEOUT", 1),
            "socket_timeout": env_int("CACHE_SOCKET_TIMEOUT", 1),
        },
    }
}

CELERY_BROKER_URL = env_str(
    "CELERY_BROKER_URL",
    "amqp://mda_app:change-me@127.0.0.1:5672//",
)
CELERY_RESULT_BACKEND = env_str("CELERY_RESULT_BACKEND", "rpc://")

INGESTION_LOCAL_ROOT = str(resolve_path(env_str("INGESTION_LOCAL_ROOT", "sample_data")))
INGESTION_SYSTEM_ROOT = str(resolve_path(env_str("INGESTION_SYSTEM_ROOT", "var/mda-system")))
INGESTION_S3_BUCKET = os.getenv("INGESTION_S3_BUCKET", "").strip()
INGESTION_S3_PREFIX = os.getenv("INGESTION_S3_PREFIX", "").strip()
INGESTION_S3_REGION = os.getenv("INGESTION_S3_REGION", "").strip()
