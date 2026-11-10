"""Integration checks against real MySQL, Redis and the Celery broker.

Run explicitly with::

    RUN_INTEGRATION=1 pytest -m integration --ds=config.settings.development
"""

from __future__ import annotations

import os

import pytest
from django.core.cache import cache
from django.db import connections
from pytest_django.plugin import DjangoDbBlocker

from config.celery import app as celery_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION") != "1",
        reason="set RUN_INTEGRATION=1 to run external dependency checks",
    ),
]


def test_mysql_connectivity(django_db_blocker: DjangoDbBlocker) -> None:
    # Connectivity is checked against the configured database without creating
    # a Django test database, so this test does not require DDL privileges.
    with django_db_blocker.unblock():
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            assert cursor.fetchone() == (1,)


def test_redis_cache_round_trip() -> None:
    cache.set("integration:health", "ok", timeout=5)

    assert cache.get("integration:health") == "ok"


def test_celery_broker_connectivity() -> None:
    connection = celery_app.connection_for_write()
    try:
        connection.ensure_connection(max_retries=1)
    finally:
        connection.release()
