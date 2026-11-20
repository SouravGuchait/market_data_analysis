"""Deterministic test settings.

Tests use in-memory SQLite and a local-memory cache so that the default test
run never depends on MySQL or Redis.  Real dependency checks live in the
``integration`` test marker.
"""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key")
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "testserver,localhost,127.0.0.1")
os.environ["LOG_FILE_ENABLED"] = "False"
os.environ["ENABLE_VALIDATION_RULES"] = "True"

from .base import *  # noqa: E402,F403
from .base import resolve_path  # noqa: E402

DEBUG = False

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "ATOMIC_REQUESTS": False,
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "mda-test",
    }
}

INGESTION_LOCAL_ROOT = str(resolve_path("data/test-ingestion"))
INGESTION_SYSTEM_ROOT = str(resolve_path("data/test-system"))
INGESTION_S3_BUCKET = ""
INGESTION_S3_PREFIX = ""
INGESTION_S3_REGION = ""

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

# TestCase rolls back the outer transaction, so on_commit hooks never fire.
CACHE_BUMP_EAGER = True

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

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
    },
    "root": {
        "handlers": ["console"],
        "level": "CRITICAL",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "CRITICAL",
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": "CRITICAL",
            "propagate": False,
        },
    },
}
