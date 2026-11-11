"""Celery entry points for scheduled discovery and per-file dispatch.

File parsing, validation and tick persistence are implemented by the later
ingestion-worker feature. This module only owns the scheduler task and the
bounded per-file task boundary that the discovery service dispatches.
"""

from __future__ import annotations

import logging

from celery import Task, shared_task

from .models import Ingestion, JsonValue
from .services import scan_enabled_rules

logger = logging.getLogger("apps.ingestion.tasks")


@shared_task(name="apps.ingestion.tasks.scan_enabled_ingestion_rules")
def scan_enabled_ingestion_rules() -> list[dict[str, JsonValue]]:
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
)
def process_ingestion_file(self: Task, ingestion_id: str) -> None:
    """Per-file worker boundary.

    The task deliberately leaves the record QUEUED until the parsing and
    validation feature implements the processing stages. Keeping the task
    boundary here allows each discovered file to be retried independently and
    prevents discovery from being coupled to a specific parser.
    """

    ingestion = Ingestion.objects.select_related("rule").filter(pk=ingestion_id).first()
    if ingestion is None:
        logger.warning(
            "Ingestion worker received an unknown ingestion id.",
            extra={"ingestion_id": ingestion_id},
        )
        return

    task_id = getattr(self.request, "id", None)
    if task_id and ingestion.celery_task_id != task_id:
        Ingestion.objects.filter(pk=ingestion.pk).update(celery_task_id=task_id)

    logger.info(
        "Ingestion file queued for worker processing.",
        extra={
            "ingestion_id": str(ingestion.pk),
            "task_id": task_id,
            "state": ingestion.state,
            "file_name": ingestion.file_name,
        },
    )


__all__ = ["process_ingestion_file", "scan_enabled_ingestion_rules"]
