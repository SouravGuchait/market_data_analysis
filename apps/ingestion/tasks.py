"""Celery entry points for scheduled discovery and per-file processing."""

from __future__ import annotations

import logging
from typing import Protocol

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError, SoftTimeLimitExceeded
from django.db import OperationalError
from django.db.utils import InterfaceError

from .services import scan_enabled_rules
from .worker import handle_worker_retry_exhausted, max_task_retries, run_ingestion_worker

logger = logging.getLogger("apps.ingestion.tasks")


class _TaskRequest(Protocol):
    id: str | None


class _BoundTask(Protocol):
    request: _TaskRequest
    max_retries: int

    def retry(self, exc: BaseException | None = None) -> None: ...


@shared_task(name="apps.ingestion.tasks.scan_enabled_ingestion_rules")
def scan_enabled_ingestion_rules() -> list[dict[str, object]]:
    """Scan every enabled rule that is due and return per-rule summaries."""

    results = scan_enabled_rules()
    logger.info(
        "Scheduled ingestion discovery completed.",
        extra={"rule_count": len(results)},
    )
    return [result.as_dict() for result in results]


@shared_task(
    bind=True,
    name="apps.ingestion.tasks.process_ingestion_file",
    acks_late=True,
    max_retries=5,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def process_ingestion_file(self: _BoundTask, ingestion_id: str) -> str | None:
    """Claim one ingestion, process staged chunks, and persist market data."""

    task_id = self.request.id or ""
    try:
        return run_ingestion_worker(ingestion_id, task_id=task_id)
    except SoftTimeLimitExceeded as exc:
        handle_worker_retry_exhausted(ingestion_id, exc)
        raise
    except (OperationalError, InterfaceError, ConnectionError, TimeoutError, OSError) as exc:
        try:
            self.retry(exc=exc)
        except MaxRetriesExceededError:
            handle_worker_retry_exhausted(ingestion_id, exc)
        return None


@shared_task(name="apps.ingestion.tasks.cleanup_ingestion_staging")
def cleanup_ingestion_staging() -> dict[str, int]:
    """Remove abandoned or expired staging directories."""

    from .staging import cleanup_abandoned_staging

    removed = cleanup_abandoned_staging()
    return {"removed": removed}


def configure_process_task_limits() -> None:
    """Apply settings-backed retry/time limits onto the bound task."""

    process_ingestion_file.max_retries = max_task_retries()


__all__ = [
    "cleanup_ingestion_staging",
    "configure_process_task_limits",
    "process_ingestion_file",
    "scan_enabled_ingestion_rules",
]
