"""Atomic claim of one ingestion record for a Celery worker."""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.ingestion.models import Ingestion, IngestionState
from apps.ingestion.services import _record_transition

logger = logging.getLogger("apps.ingestion.claim")

_CLAIMABLE = frozenset({IngestionState.QUEUED, IngestionState.RETRYING})


def claim_ingestion(ingestion_id: str, *, task_id: str = "") -> Ingestion | None:
    """Claim QUEUED/RETRYING → PROCESSING. Return None when ineligible."""

    with transaction.atomic():
        updated = (
            Ingestion.objects.filter(pk=ingestion_id, state__in=_CLAIMABLE)
            .select_for_update()
            .first()
        )
        if updated is None:
            logger.info(
                "Ingestion claim skipped; record missing or ineligible.",
                extra={"ingestion_id": ingestion_id},
            )
            return None
        previous = updated.state
        updated.state = IngestionState.PROCESSING
        updated.started_at = updated.started_at or timezone.now()
        updated.error_summary = ""
        fields = ["state", "started_at", "error_summary", "updated_at"]
        if task_id:
            updated.celery_task_id = task_id
            fields.append("celery_task_id")
        updated.save(update_fields=fields)
        _record_transition(
            updated,
            from_state=previous,
            to_state=IngestionState.PROCESSING,
            reason="worker_claim",
            metadata={"task_id": task_id} if task_id else {},
        )
        return (
            Ingestion.objects.select_related("rule", "storage_file", "owner")
            .filter(pk=updated.pk)
            .get()
        )
