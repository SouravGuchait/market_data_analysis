"""Integration-test settings for real MySQL, Redis, and the Celery broker.

The GitHub integration job runs the app feature tests and ``tests/integration``
with this module. It needs reachable ``DB_*``, ``CACHE_URL``,
``CELERY_BROKER_URL``, and ``CELERY_RESULT_BACKEND`` values.
On a laptop it reads ``~/.config/env_vars/dev.env`` (or ``MDA_ENV_FILE`` when
that file is named ``dev.env``) for the published MySQL port and broker. CI
variables win. ``prod.env`` is not read.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEV_ENV_KEYS = (
    "DB_HOST",
    "DB_PORT",
    "MYSQL_HOST_PORT",
    "DB_USER",
    "DB_PASSWORD",
    "DB_ROOT_PASSWORD",
    "REDIS_HOST_PORT",
    "RABBITMQ_USER",
    "RABBITMQ_PASSWORD",
    "RABBITMQ_HOST_PORT",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
)


def _dev_env_path() -> Path | None:
    configured = os.environ.get("MDA_ENV_FILE", "").strip()
    if configured:
        selected = Path(configured).expanduser()
        if selected.name == "dev.env" and selected.is_file():
            return selected
    fallback = Path.home() / ".config" / "env_vars" / "dev.env"
    if fallback.is_file():
        return fallback
    return None


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _apply_dev_env_defaults() -> None:
    """Fill unset host settings from the local dev env file.

    CI sets DB_PORT, cache, and broker explicitly, so those values stay.
    ``prod.env`` is ignored.
    """

    path = _dev_env_path()
    if path is None:
        return
    for key, value in _parse_env_file(path).items():
        if key in _DEV_ENV_KEYS and value:
            os.environ.setdefault(key, value)


def _use_published_mysql_port() -> None:
    """Use the Compose host port when MySQL is reached through localhost."""

    host = os.environ.get("DB_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost"}:
        os.environ.setdefault("DB_PORT", "3306")
        return
    published = os.environ.get("MYSQL_HOST_PORT", "").strip()
    if published:
        os.environ["DB_PORT"] = published
        return
    os.environ.setdefault("DB_PORT", "3307")


_apply_dev_env_defaults()
_use_published_mysql_port()

os.environ.setdefault(
    "DJANGO_SECRET_KEY",
    "integration-test-secret-key-at-least-32-bytes",
)
os.environ.setdefault(
    "DJANGO_ALLOWED_HOSTS",
    "testserver,localhost,127.0.0.1,0.0.0.0",
)

# Keep an explicit caller host. Otherwise use localhost and the published ports.
os.environ.setdefault("DB_HOST", "127.0.0.1")
_redis_port = os.environ.get("REDIS_HOST_PORT", "6379")
os.environ.setdefault("CACHE_URL", f"redis://127.0.0.1:{_redis_port}/15")
_rabbit_port = os.environ.get("RABBITMQ_HOST_PORT", "5672")
_rabbit_user = os.environ.get("RABBITMQ_USER", "mda_app")
_rabbit_password = os.environ.get("RABBITMQ_PASSWORD", "mda_app")
os.environ.setdefault(
    "CELERY_BROKER_URL",
    f"amqp://{_rabbit_user}:{_rabbit_password}@127.0.0.1:{_rabbit_port}//",
)
os.environ.setdefault("CELERY_RESULT_BACKEND", "rpc://")
os.environ["LOG_FILE_ENABLED"] = "False"
os.environ["ENABLE_VALIDATION_RULES"] = "True"
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
from .base import env_int, env_str, resolve_path  # noqa: E402

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
            "socket_connect_timeout": env_int("CACHE_SOCKET_CONNECT_TIMEOUT", 1),
            "socket_timeout": env_int("CACHE_SOCKET_TIMEOUT", 1),
        },
    }
}

CELERY_BROKER_URL = env_str("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env_str("CELERY_RESULT_BACKEND")
INGESTION_LOCAL_ROOT = str(resolve_path(env_str("INGESTION_LOCAL_ROOT")))
INGESTION_SYSTEM_ROOT = str(resolve_path(env_str("INGESTION_SYSTEM_ROOT")))
INGESTION_S3_BUCKET = os.getenv("INGESTION_S3_BUCKET", "").strip()
INGESTION_S3_PREFIX = os.getenv("INGESTION_S3_PREFIX", "").strip()
INGESTION_S3_REGION = os.getenv("INGESTION_S3_REGION", "").strip()

# Publish Celery work to RabbitMQ. Feature tests that call delay() only need
# the broker to accept the message; tests/integration starts a worker.
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_EAGER_PROPAGATES = False
# pytest-django rolls the test transaction back, so on_commit cache bumps
# would never run. The cache backend is still the real Redis instance.
CACHE_BUMP_EAGER = True

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
