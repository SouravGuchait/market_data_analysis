"""Create aggregation runs and enqueue Celery processing safely."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.contrib.auth.base_user import AbstractBaseUser
from django.db import transaction
from django.utils import timezone

from apps.analytics.models import AggregationRun, AggregationRunStatus, AggregationTrigger
from apps.analytics.services.aggregation import plan_from_ingestion
from apps.analytics.services.timeframes import SUPPORTED_TIMEFRAMES
from apps.audit.models import AuditAction, AuditResourceType, AuditStatus
from apps.audit.services import record_audit_event, record_system_event

logger = logging.getLogger("apps.analytics.dispatch")


@dataclass(frozen=True)
class AggregationRequest:
    """Inputs required to create one aggregation run."""

    trigger: str
    range_start: datetime
    range_end: datetime
    instrument_ids: Sequence[int]
    exchange_ids: Sequence[int]
    timeframes: Sequence[str] | None = None
    requested_by: AbstractBaseUser | None = None
    ingestion_id: UUID | None = None


def _audit_triggered(run: AggregationRun, *, actor: AbstractBaseUser | None) -> None:
    metadata = {
        "trigger": run.trigger,
        "range_start": run.range_start.isoformat(),
        "range_end": run.range_end.isoformat(),
        "instrument_ids": run.instrument_ids,
        "exchange_ids": run.exchange_ids,
        "timeframes": run.timeframes,
    }
    if actor is None:
        record_system_event(
            action=AuditAction.AGGREGATION_TRIGGERED,
            resource_type=AuditResourceType.ANALYTICS,
            status=AuditStatus.ACCEPTED,
            resource_id=str(run.pk),
            metadata=metadata,
        )
        return
    record_audit_event(
        actor=actor,
        action=AuditAction.AGGREGATION_TRIGGERED,
        resource_type=AuditResourceType.ANALYTICS,
        status=AuditStatus.ACCEPTED,
        resource_id=str(run.pk),
        metadata=metadata,
    )


def create_aggregation_run(request: AggregationRequest) -> AggregationRun:
    """Persist a pending aggregation run and audit the trigger once."""

    selected = list(request.timeframes) if request.timeframes else list(SUPPORTED_TIMEFRAMES)
    run = AggregationRun.objects.create(
        status=AggregationRunStatus.PENDING,
        trigger=request.trigger,
        requested_by=request.requested_by,
        ingestion_id=request.ingestion_id,
        instrument_ids=[int(value) for value in request.instrument_ids],
        exchange_ids=[int(value) for value in request.exchange_ids],
        timeframes=selected,
        range_start=request.range_start,
        range_end=request.range_end,
    )
    _audit_triggered(run, actor=request.requested_by)
    return run


def enqueue_aggregation_run(run_id: UUID) -> None:
    """Dispatch Celery processing; record dispatch failures for retry."""

    from apps.analytics.tasks import process_aggregation_run

    run = AggregationRun.objects.filter(pk=run_id).first()
    if run is None:
        return
    run.dispatch_attempts += 1
    try:
        delay = getattr(process_aggregation_run, "delay", None)
        if not callable(delay):
            raise RuntimeError("Aggregation Celery task is missing delay().")
        async_result = delay(str(run_id))
    except Exception as exc:
        run.status = AggregationRunStatus.DISPATCH_FAILED
        run.error_summary = f"Dispatch failed: {exc}"[:2000]
        run.save(update_fields=["status", "error_summary", "dispatch_attempts", "updated_at"])
        logger.exception(
            "Failed to enqueue aggregation run.",
            extra={"aggregation_run_id": str(run_id)},
        )
        return
    run.status = AggregationRunStatus.QUEUED
    run.celery_task_id = str(getattr(async_result, "id", "") or "")
    run.error_summary = ""
    run.save(
        update_fields=[
            "status",
            "celery_task_id",
            "error_summary",
            "dispatch_attempts",
            "updated_at",
        ]
    )


def schedule_run_on_commit(run_id: UUID) -> None:
    """Enqueue after the surrounding transaction commits."""

    transaction.on_commit(lambda: enqueue_aggregation_run(run_id))


def schedule_ingestion_aggregation(ingestion_id: UUID) -> AggregationRun | None:
    """Create and schedule aggregation for trades persisted by one ingestion."""

    plan = plan_from_ingestion(ingestion_id)
    if plan.is_empty or plan.range_start is None or plan.range_end is None:
        return None
    run = create_aggregation_run(
        AggregationRequest(
            trigger=AggregationTrigger.INGESTION,
            range_start=plan.range_start,
            range_end=plan.range_end,
            instrument_ids=plan.instrument_ids,
            exchange_ids=plan.exchange_ids,
            requested_by=None,
            ingestion_id=ingestion_id,
        )
    )
    schedule_run_on_commit(run.pk)
    return run


def retry_dispatch(run: AggregationRun) -> AggregationRun:
    """Re-enqueue a run that previously failed to reach the broker."""

    if run.status not in {
        AggregationRunStatus.DISPATCH_FAILED,
        AggregationRunStatus.PENDING,
    }:
        return run
    run.status = AggregationRunStatus.PENDING
    run.updated_at = timezone.now()
    run.save(update_fields=["status", "updated_at"])
    enqueue_aggregation_run(run.pk)
    run.refresh_from_db()
    return run
