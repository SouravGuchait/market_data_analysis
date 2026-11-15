"""Integration-test settings for real MySQL, Redis, and Celery broker.

Requires ``RUN_INTEGRATION=1`` and reachable ``DB_*``, ``CACHE_URL``,
``CELERY_BROKER_URL``, and ``CELERY_RESULT_BACKEND`` environment values.
Unlike ``development``, this module does not load a project ``.env`` file.
"""

from __future__ import annotations

import os

os.environ.setdefault(
    "DJANGO_SECRET_KEY",
    "integration-test-secret-key-at-least-32-bytes",
)
os.environ.setdefault(
    "DJANGO_ALLOWED_HOSTS",
    "testserver,localhost,127.0.0.1,0.0.0.0",
)

# Keep host/port/cache/broker from the caller (Compose .env is fine).
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "3306")
os.environ.setdefault("CACHE_URL", "redis://127.0.0.1:6379/15")
os.environ.setdefault("CELERY_BROKER_URL", "amqp://mda_app:mda_app@127.0.0.1:5672//")
os.environ.setdefault("CELERY_RESULT_BACKEND", "rpc://")
os.environ["LOG_FILE_ENABLED"] = "False"
os.environ.setdefault("INGESTION_LOCAL_ROOT", "data/integration-ingestion")
os.environ.setdefault("INGESTION_SYSTEM_ROOT", "data/integration-system")

# Isolate from the app DB name in a sourced .env (e.g. mda_backend).
os.environ["DB_NAME"] = os.environ.get("INTEGRATION_DB_NAME", "mda_integration")

# pytest-django needs CREATE DATABASE. A sourced .env often sets DB_USER=mda_app,
# which cannot create test_mda_*. Prefer explicit integration creds, then root.
_integration_user = os.environ.get("INTEGRATION_DB_USER")
_root_password = os.environ.get("DB_ROOT_PASSWORD")
_previous_user = os.environ.get("DB_USER", "root")
if _integration_user:
    os.environ["DB_USER"] = _integration_user
    os.environ["DB_PASSWORD"] = os.environ.get("INTEGRATION_DB_PASSWORD", "")
elif _root_password:
    os.environ["DB_USER"] = "root"
    os.environ["DB_PASSWORD"] = _root_password
else:
    # Force root — do not keep a sourced DB_USER=mda_app via setdefault.
    os.environ["DB_USER"] = "root"
    if _previous_user != "root" or not os.environ.get("DB_PASSWORD"):
        os.environ["DB_PASSWORD"] = "root"

from .base import *  # noqa: E402,F403
from .base import REST_FRAMEWORK as _REST_FRAMEWORK  # noqa: E402

DEBUG = False

CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_EAGER_PROPAGATES = False
CACHE_BUMP_EAGER = False

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Views still declare ScopedRateThrottle; keep scopes but raise limits for CI.
_rates = {str(scope): "10000/min" for scope in _REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {})}
REST_FRAMEWORK = {
    **_REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": _rates,
}

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
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "celery": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
