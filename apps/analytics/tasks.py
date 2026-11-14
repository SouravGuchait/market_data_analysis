"""Celery tasks for candle aggregation runs."""

from __future__ import annotations

import logging
from uuid import UUID

from celery import shared_task
from django.db import OperationalError
from django.db.utils import InterfaceError

from apps.analytics.models import AggregationRun, AggregationRunStatus
from apps.analytics.services.aggregation import (
    aggregate_scope,
    mark_run_failed,
    mark_run_running,
    mark_run_succeeded,
    scope_from_run,
)
from apps.analytics.services.lifecycle_audit import audit_run_failed, audit_run_succeeded

logger = logging.getLogger("apps.analytics.tasks")

_TRANSIENT = (OperationalError, InterfaceError, ConnectionError, TimeoutError, OSError)


def _load_run(run_id: str) -> AggregationRun | None:
    try:
        return AggregationRun.objects.get(pk=UUID(run_id))
    except (AggregationRun.DoesNotExist, ValueError):
        return None


def _already_terminal(run: AggregationRun) -> bool:
    return run.status in {
        AggregationRunStatus.SUCCEEDED,
        AggregationRunStatus.FAILED,
    }


def _execute_run(run: AggregationRun) -> str:
    mark_run_running(run)
    try:
        totals = aggregate_scope(scope_from_run(run))
    except _TRANSIENT:
        raise
    except Exception as exc:
        mark_run_failed(run, exc)
        audit_run_failed(run)
        logger.exception(
            "Aggregation run failed permanently.",
            extra={"aggregation_run_id": str(run.pk)},
        )
        return AggregationRunStatus.FAILED
    mark_run_succeeded(run, totals)
    audit_run_succeeded(run)
    return AggregationRunStatus.SUCCEEDED


@shared_task(
    bind=True,
    autoretry_for=_TRANSIENT,
    retry_backoff=True,
    retry_kwargs={"max_retries": 5},
    name="apps.analytics.tasks.process_aggregation_run",
)
def process_aggregation_run(self, run_id: str) -> str:
    """Execute one persisted aggregation run and audit its terminal outcome."""

    run = _load_run(run_id)
    if run is None:
        logger.warning("Aggregation run not found.", extra={"aggregation_run_id": run_id})
        return "missing"
    if _already_terminal(run):
        return run.status
    return _execute_run(run)
