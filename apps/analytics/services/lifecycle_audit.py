"""Audit AggregationRun terminal outcomes once per business run."""

from __future__ import annotations

from apps.analytics.models import AggregationRun, AggregationTrigger
from apps.audit.models import AuditAction, AuditResourceType, AuditStatus
from apps.audit.services import record_audit_event, record_system_event


def _payload(run: AggregationRun) -> dict[str, object]:
    return {
        "trigger": run.trigger,
        "candles_created": run.candles_created,
        "candles_updated": run.candles_updated,
        "candles_unchanged": run.candles_unchanged,
        "error_summary": run.error_summary[:500] if run.error_summary else "",
    }


def audit_run_succeeded(run: AggregationRun) -> None:
    """Record successful aggregation completion for the run."""

    payload = _payload(run)
    actor = run.requested_by
    if run.trigger == AggregationTrigger.INGESTION or actor is None:
        record_system_event(
            action=AuditAction.AGGREGATION_COMPLETED,
            resource_type=AuditResourceType.ANALYTICS,
            status=AuditStatus.SUCCESS,
            resource_id=str(run.pk),
            metadata=payload,
        )
        return
    record_audit_event(
        actor=actor,
        action=AuditAction.AGGREGATION_COMPLETED,
        resource_type=AuditResourceType.ANALYTICS,
        status=AuditStatus.SUCCESS,
        resource_id=str(run.pk),
        metadata=payload,
    )


def audit_run_failed(run: AggregationRun) -> None:
    """Record aggregation failure for the run."""

    payload = _payload(run)
    actor = run.requested_by
    if run.trigger == AggregationTrigger.INGESTION or actor is None:
        record_system_event(
            action=AuditAction.AGGREGATION_FAILED,
            resource_type=AuditResourceType.ANALYTICS,
            status=AuditStatus.FAILURE,
            resource_id=str(run.pk),
            metadata=payload,
        )
        return
    record_audit_event(
        actor=actor,
        action=AuditAction.AGGREGATION_FAILED,
        resource_type=AuditResourceType.ANALYTICS,
        status=AuditStatus.FAILURE,
        resource_id=str(run.pk),
        metadata=payload,
    )
