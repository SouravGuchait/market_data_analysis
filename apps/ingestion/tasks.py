"""Celery entry points for scheduled discovery and per-file processing.

Feature 006 exposes conversion and Pandas validation as services. Feature 007
owns claiming an ingestion, calling the pipeline, and persisting batches.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from .models import Ingestion
from .services import scan_enabled_rules, source_storage_key

logger = logging.getLogger("apps.ingestion.tasks")


@shared_task(name="apps.ingestion.tasks.scan_enabled_ingestion_rules")
def scan_enabled_ingestion_rules() -> list[dict[str, Any]]:
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
def process_ingestion_file(self: Any, ingestion_id: str) -> None:
    """Per-file worker boundary reserved for Feature 007 orchestration.

    Conversion and validation are available through
    ``apps.ingestion.pipeline.run_conversion_pipeline``. This task keeps the
    record QUEUED until that orchestration claims and persists batches.
    """

    ingestion = (
        Ingestion.objects.select_related("rule", "storage_file").filter(pk=ingestion_id).first()
    )
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
            "data_type": ingestion.data_type,
            "chunk_size": ingestion.chunk_size,
            "source_key": source_storage_key(ingestion),
        },
    )


@shared_task(name="apps.ingestion.tasks.cleanup_ingestion_staging")
def cleanup_ingestion_staging() -> dict[str, int]:
    """Remove abandoned or expired staging directories."""

    from .staging import cleanup_abandoned_staging

    removed = cleanup_abandoned_staging()
    return {"removed": removed}


__all__ = [
    "cleanup_ingestion_staging",
    "process_ingestion_file",
    "scan_enabled_ingestion_rules",
]
