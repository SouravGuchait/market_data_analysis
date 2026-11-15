"""Shared Celery tasks for operational probes."""

from __future__ import annotations

from celery import shared_task


@shared_task(name="apps.common.tasks.ping")
def ping() -> str:
    """Return a fixed payload so integration tests can prove worker delivery."""

    return "pong"
