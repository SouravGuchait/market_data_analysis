"""Final ingestion status, failure handling, and audit events."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction, AuditResourceType, AuditStatus
from apps.audit.services import record_audit_event, record_system_event
from apps.ingestion.models import Ingestion, IngestionState
from apps.ingestion.services import _record_transition

logger = logging.getLogger("apps.ingestion.finalize")


def _audit(
    ingestion: Ingestion,
    *,
    action: str,
    status: str,
    metadata: Mapping[str, object],
) -> None:
    payload = dict(metadata)
    owner = ingestion.owner
    if owner is None:
        record_system_event(
            action=action,
            resource_type=AuditResourceType.INGESTION,
            status=status,
            resource_id=str(ingestion.pk),
            metadata=payload,
        )
        return
    record_audit_event(
        actor=owner,
        action=action,
        resource_type=AuditResourceType.INGESTION,
        status=status,
        resource_id=str(ingestion.pk),
        metadata=payload,
    )


def audit_processing_started(ingestion: Ingestion) -> None:
    _audit(
        ingestion,
        action=AuditAction.INGESTION_STARTED,
        status=AuditStatus.ACCEPTED,
        metadata={"scope": "processing", "filename": ingestion.file_name},
    )


def resolve_final_state(ingestion: Ingestion) -> str:
    """Map counters to SUCCESS / PARTIAL_SUCCESS / FAILED."""

    accepted = ingestion.accepted_rows
    rejected = ingestion.rejected_rows
    if accepted > 0 and rejected > 0:
        return IngestionState.PARTIAL_SUCCESS
    if accepted == 0 and rejected > 0:
        return IngestionState.FAILED
    # Accepted rows, duplicates-only, or empty readable file → SUCCESS.
    return IngestionState.SUCCESS


def finalize_ingestion(ingestion: Ingestion) -> Ingestion:
    """Set terminal status from counters and emit completion audit."""

    ingestion.refresh_from_db()
    final_state = resolve_final_state(ingestion)
    with transaction.atomic():
        locked = Ingestion.objects.select_for_update().get(pk=ingestion.pk)
        previous = locked.state
        locked.state = final_state
        locked.finished_at = timezone.now()
        locked.error_summary = ""
        locked.save(update_fields=["state", "finished_at", "error_summary", "updated_at"])
        _record_transition(
            locked,
            from_state=previous,
            to_state=final_state,
            reason="processing_complete",
        )
    ingestion.refresh_from_db()
    audit_status = (
        AuditStatus.PARTIAL
        if final_state == IngestionState.PARTIAL_SUCCESS
        else AuditStatus.SUCCESS
    )
    if final_state == IngestionState.FAILED:
        audit_status = AuditStatus.FAILURE
    action = (
        AuditAction.INGESTION_FAILED
        if final_state == IngestionState.FAILED
        else AuditAction.INGESTION_COMPLETED
    )
    _audit(
        ingestion,
        action=action,
        status=audit_status,
        metadata={
            "scope": "processing",
            "final_state": final_state,
            "accepted_rows": ingestion.accepted_rows,
            "rejected_rows": ingestion.rejected_rows,
            "duplicate_rows": ingestion.duplicate_rows,
            "chunks_processed": ingestion.chunks_processed,
        },
    )
    return ingestion


def mark_retrying(ingestion: Ingestion, exc: BaseException) -> None:
    """Record a transient failure that will be retried."""

    with transaction.atomic():
        locked = Ingestion.objects.select_for_update().get(pk=ingestion.pk)
        previous = locked.state
        locked.state = IngestionState.RETRYING
        locked.error_summary = str(exc)[:2000]
        locked.save(update_fields=["state", "error_summary", "updated_at"])
        _record_transition(
            locked,
            from_state=previous,
            to_state=IngestionState.RETRYING,
            reason="transient_failure",
            metadata={"error": str(exc)[:500]},
        )
    _audit(
        ingestion,
        action=AuditAction.INGESTION_FAILED,
        status=AuditStatus.ACCEPTED,
        metadata={"scope": "retry_requested", "error": str(exc)[:500]},
    )


def handle_ingestion_failure(ingestion: Ingestion, exc: BaseException) -> None:
    """Mark permanent failure and audit."""

    with transaction.atomic():
        locked = Ingestion.objects.select_for_update().get(pk=ingestion.pk)
        previous = locked.state
        locked.state = IngestionState.FAILED
        locked.error_summary = str(exc)[:2000]
        locked.finished_at = timezone.now()
        locked.save(update_fields=["state", "error_summary", "finished_at", "updated_at"])
        _record_transition(
            locked,
            from_state=previous,
            to_state=IngestionState.FAILED,
            reason="processing_failed",
            metadata={"error": str(exc)[:500]},
        )
    _audit(
        ingestion,
        action=AuditAction.INGESTION_FAILED,
        status=AuditStatus.FAILURE,
        metadata={"scope": "processing", "error": str(exc)[:500]},
    )
    logger.exception(
        "Ingestion processing failed permanently.",
        extra={"ingestion_id": str(ingestion.pk)},
    )


def handle_retries_exhausted(ingestion: Ingestion, exc: BaseException) -> None:
    with transaction.atomic():
        locked = Ingestion.objects.select_for_update().get(pk=ingestion.pk)
        previous = locked.state
        locked.state = IngestionState.FAILED
        locked.error_summary = str(exc)[:2000]
        locked.finished_at = timezone.now()
        locked.save(update_fields=["state", "error_summary", "finished_at", "updated_at"])
        _record_transition(
            locked,
            from_state=previous,
            to_state=IngestionState.FAILED,
            reason="retries_exhausted",
            metadata={"error": str(exc)[:500]},
        )
    _audit(
        ingestion,
        action=AuditAction.INGESTION_FAILED,
        status=AuditStatus.FAILURE,
        metadata={"scope": "retry_exhausted", "error": str(exc)[:500]},
    )
