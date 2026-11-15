"""Celery worker connectivity over a real RabbitMQ broker."""

from __future__ import annotations

import subprocess
import time

import pytest
from celery.result import AsyncResult

from apps.common.tasks import ping
from config.celery import app

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
PING_TIMEOUT_SECONDS = 30


def test_worker_responds_to_inspect_ping(
    celery_worker_process: subprocess.Popen[bytes],
) -> None:
    del celery_worker_process
    replies = app.control.inspect(timeout=2.0).ping()
    assert replies is not None
    assert replies


def test_ping_task_round_trips_through_broker(
    celery_worker_process: subprocess.Popen[bytes],
) -> None:
    del celery_worker_process
    assert app.conf.task_always_eager is False

    async_result = ping.delay()
    assert isinstance(async_result, AsyncResult)
    assert async_result.id

    deadline = time.monotonic() + PING_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if async_result.ready():
            break
        time.sleep(0.25)

    assert async_result.successful()
    assert async_result.get(timeout=1) == "pong"
