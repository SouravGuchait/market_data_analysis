"""Scheduled discovery and ingestion-record orchestration.

One scan lists objects through a storage adapter, applies per-object stability
and watermark rules, calculates a content identity and creates one ingestion
record per eligible file. Task dispatch happens only after the record commits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.models import AuditAction, AuditResourceType, AuditStatus
from apps.audit.services import record_system_event

from .models import (
    Ingestion,
    IngestionConfigurationError,
    IngestionObjectState,
    IngestionRule,
    IngestionState,
    IngestionStateTransition,
    JsonValue,
    ScanStatus,
    StabilityPolicy,
    resolve_stability_policy,
)
from .storage import (
    StorageAdapter,
    StorageObject,
    get_storage_adapter,
)

logger = logging.getLogger("apps.ingestion.services")

_MAX_ERROR_LENGTH = 500
_MAX_FAILED_OBJECTS = 20
_REQUEUEABLE_STATES = {IngestionState.FAILED, IngestionState.SKIPPED}


@dataclass
class RuleScanResult:
    """Observable outcome of scanning one rule."""

    rule_id: str
    rule_name: str
    status: str
    listed: int = 0
    queued: int = 0
    requeued: int = 0
    duplicates: int = 0
    unstable: int = 0
    historical: int = 0
    errors: int = 0
    message: str = ""
    failed_objects: list[str] = field(default_factory=list)

    def add_outcome(self, outcome: str) -> None:
        counters = {
            "queued": "queued",
            "requeued": "requeued",
            "duplicate": "duplicates",
            "unstable": "unstable",
            "historical": "historical",
        }
        field_name = counters.get(outcome)
        if field_name is not None:
            setattr(self, field_name, getattr(self, field_name) + 1)

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "status": self.status,
            "listed": self.listed,
            "queued": self.queued,
            "requeued": self.requeued,
            "duplicates": self.duplicates,
            "unstable": self.unstable,
            "historical": self.historical,
            "errors": self.errors,
            "message": self.message,
            "failed_objects": list(self.failed_objects),
        }


def _safe_message(exc: BaseException) -> str:
    message = str(exc).strip().replace("\n", " ")
    return (message or type(exc).__name__)[:_MAX_ERROR_LENGTH]


def _set_rule_scan_state(rule: IngestionRule, **fields: object) -> None:
    for name, value in fields.items():
        setattr(rule, name, value)
    rule.save(update_fields=[*fields, "updated_at"])


def _record_transition(
    ingestion: Ingestion,
    *,
    from_state: str,
    to_state: str,
    reason: str = "",
    metadata: dict[str, JsonValue] | None = None,
) -> None:
    IngestionStateTransition.objects.create(
        ingestion=ingestion,
        from_state=from_state,
        to_state=to_state,
        reason=reason[:255],
        metadata=metadata or {},
    )


def _audit_scan_started(rule: IngestionRule) -> None:
    record_system_event(
        action=AuditAction.INGESTION_STARTED,
        resource_type=AuditResourceType.INGESTION,
        resource_id=rule.pk,
        status=AuditStatus.ACCEPTED,
        metadata={
            "scope": "scan",
            "rule_name": rule.name,
            "storage_backend": rule.storage_backend,
        },
    )


def _audit_scan_finished(rule: IngestionRule, result: RuleScanResult) -> None:
    metadata = {
        "scope": "scan",
        "rule_name": rule.name,
        "listed": result.listed,
        "queued": result.queued,
        "requeued": result.requeued,
        "duplicates": result.duplicates,
        "unstable": result.unstable,
        "historical": result.historical,
        "errors": result.errors,
    }
    if result.status == ScanStatus.SUCCESS.value:
        record_system_event(
            action=AuditAction.INGESTION_COMPLETED,
            resource_type=AuditResourceType.INGESTION,
            resource_id=rule.pk,
            status=AuditStatus.SUCCESS,
            metadata=metadata,
        )
        return

    record_system_event(
        action=AuditAction.INGESTION_FAILED,
        resource_type=AuditResourceType.INGESTION,
        resource_id=rule.pk,
        status=AuditStatus.FAILURE,
        metadata={**metadata, "error_category": "scan_failed"},
    )


def rule_is_due(rule: IngestionRule, now: datetime) -> bool:
    """Return whether the rule's scan interval has elapsed."""

    if rule.last_scan_completed_at is None:
        return True
    due_at = rule.last_scan_completed_at + timedelta(seconds=rule.scan_interval_seconds)
    return due_at <= now


def _baseline_watermark(rule: IngestionRule) -> datetime:
    """Return the boundary before which untouched files are historical."""

    return rule.last_scan_watermark or rule.initial_watermark or rule.created_at


def _is_candidate(
    rule: IngestionRule,
    state: IngestionObjectState,
    obj: StorageObject,
    policy: StabilityPolicy,
) -> bool:
    """Avoid re-hashing objects already processed at the same metadata."""

    changed = (
        not state.last_processed_content_hash
        or state.last_processed_modified_at is None
        or obj.modified_at > state.last_processed_modified_at
    )
    latest_state = None
    if not changed:
        latest_state = (
            Ingestion.objects.filter(rule=rule, object_key=obj.key)
            .order_by("-created_at")
            .values_list("state", flat=True)
            .first()
        )
    watermark = rule.last_scan_watermark
    within_overlap = watermark is not None and obj.modified_at >= (
        watermark - timedelta(seconds=policy.overlap_seconds)
    )
    return changed or latest_state in _REQUEUEABLE_STATES or within_overlap


def _mark_state_processed(
    state: IngestionObjectState,
    obj: StorageObject,
    content_hash: str,
    now: datetime,
) -> None:
    state.last_content_hash = content_hash
    state.last_processed_content_hash = content_hash
    state.last_processed_modified_at = obj.modified_at
    state.last_ingested_at = now
    state.save(
        update_fields=[
            "last_content_hash",
            "last_processed_content_hash",
            "last_processed_modified_at",
            "last_ingested_at",
            "updated_at",
        ]
    )


def _audit_ingestion_queued(rule: IngestionRule, ingestion: Ingestion) -> None:
    record_system_event(
        action=AuditAction.INGESTION_STARTED,
        resource_type=AuditResourceType.INGESTION,
        resource_id=ingestion.pk,
        status=AuditStatus.ACCEPTED,
        metadata={
            "rule_name": rule.name,
            "file_name": ingestion.file_name,
            "content_hash": ingestion.content_hash,
            "state": ingestion.state,
        },
    )


def _create_ingestion(
    rule: IngestionRule,
    obj: StorageObject,
    content_hash: str,
) -> Ingestion:
    with transaction.atomic():
        ingestion = Ingestion.objects.create(
            rule=rule,
            storage_backend=rule.storage_backend,
            object_key=obj.key,
            file_name=obj.name,
            file_size_bytes=obj.size_bytes,
            file_modified_at=obj.modified_at,
            content_hash=content_hash,
            state=IngestionState.QUEUED,
        )
        _record_transition(
            ingestion,
            from_state="",
            to_state=IngestionState.DISCOVERED,
            reason="discovered",
        )
        _record_transition(
            ingestion,
            from_state=IngestionState.DISCOVERED,
            to_state=IngestionState.QUEUED,
            reason="queued",
        )
        _audit_ingestion_queued(rule, ingestion)
        transaction.on_commit(lambda ingestion_id=ingestion.pk: dispatch_ingestion(ingestion_id))
    return ingestion


def _requeue_ingestion(
    rule: IngestionRule,
    ingestion: Ingestion,
    obj: StorageObject,
) -> Ingestion:
    with transaction.atomic():
        previous_state = ingestion.state
        ingestion.state = IngestionState.QUEUED
        ingestion.celery_task_id = ""
        ingestion.started_at = None
        ingestion.finished_at = None
        ingestion.error_summary = ""
        ingestion.file_size_bytes = obj.size_bytes
        ingestion.file_modified_at = obj.modified_at
        ingestion.save(
            update_fields=[
                "state",
                "celery_task_id",
                "started_at",
                "finished_at",
                "error_summary",
                "file_size_bytes",
                "file_modified_at",
                "updated_at",
            ]
        )
        _record_transition(
            ingestion,
            from_state=previous_state,
            to_state=IngestionState.QUEUED,
            reason="re-discovered after an incomplete attempt",
        )
        _audit_ingestion_queued(rule, ingestion)
        transaction.on_commit(lambda ingestion_id=ingestion.pk: dispatch_ingestion(ingestion_id))
    return ingestion


def _existing_ingestion_outcome(
    rule: IngestionRule,
    state: IngestionObjectState,
    obj: StorageObject,
    content_hash: str,
    now: datetime,
) -> tuple[str, UUID | None]:
    existing = (
        Ingestion.objects.filter(
            rule=rule,
            object_key=obj.key,
            content_hash=content_hash,
        )
        .order_by("-created_at")
        .first()
    )
    if existing is None:
        return "", None

    if existing.state in _REQUEUEABLE_STATES:
        ingestion = _requeue_ingestion(rule, existing, obj)
        return "requeued", ingestion.pk

    _mark_state_processed(state, obj, content_hash, now)
    return "duplicate", None


def _observe_object(
    rule: IngestionRule,
    obj: StorageObject,
    now: datetime,
) -> tuple[IngestionObjectState, bool]:
    state, created = IngestionObjectState.objects.get_or_create(
        rule=rule,
        object_key=obj.key,
        defaults={"object_name": obj.name, "first_seen_at": now, "last_seen_at": now},
    )
    same_snapshot = (
        state.stable_scan_count > 0
        and state.observed_size_bytes == obj.size_bytes
        and state.observed_modified_at == obj.modified_at
    )
    state.object_name = obj.name
    state.stable_scan_count = state.stable_scan_count + 1 if same_snapshot else 1
    state.observed_size_bytes = obj.size_bytes
    state.observed_modified_at = obj.modified_at
    state.last_seen_at = now
    state.save(
        update_fields=[
            "object_name",
            "stable_scan_count",
            "observed_size_bytes",
            "observed_modified_at",
            "last_seen_at",
            "updated_at",
        ]
    )
    return state, created


@dataclass(frozen=True)
class ObjectScanContext:
    created: bool
    now: datetime
    policy: StabilityPolicy


def _preflight_object(
    rule: IngestionRule,
    state: IngestionObjectState,
    obj: StorageObject,
    context: ObjectScanContext,
) -> str | None:
    outcome = None
    historical = obj.modified_at <= _baseline_watermark(rule)
    if (
        historical
        and not state.last_processed_content_hash
        and (context.created or state.is_historical)
    ):
        state.is_historical = True
        state.save(update_fields=["is_historical", "updated_at"])
        outcome = "historical"
    elif state.is_historical:
        state.is_historical = False
        state.save(update_fields=["is_historical", "updated_at"])

    if outcome is None:
        too_new = state.stable_scan_count < context.policy.required_stable_scans
        too_recent = (
            context.now - obj.modified_at
        ).total_seconds() < context.policy.min_file_age_seconds
        if too_new or too_recent:
            outcome = "unstable"
        elif not _is_candidate(rule, state, obj, context.policy):
            outcome = "duplicate"
    return outcome


def _verify_stable_snapshot(
    state: IngestionObjectState,
    obj: StorageObject,
    adapter: StorageAdapter,
    now: datetime,
) -> str | None:
    current = adapter.stat(obj.key)
    if current.size_bytes == obj.size_bytes and current.modified_at == obj.modified_at:
        return None
    state.stable_scan_count = 1
    state.observed_size_bytes = current.size_bytes
    state.observed_modified_at = current.modified_at
    state.last_seen_at = now
    state.save(
        update_fields=[
            "stable_scan_count",
            "observed_size_bytes",
            "observed_modified_at",
            "last_seen_at",
            "updated_at",
        ]
    )
    return "unstable"


def _create_or_requeue(
    rule: IngestionRule,
    state: IngestionObjectState,
    obj: StorageObject,
    content_hash: str,
    now: datetime,
) -> tuple[str, UUID | None]:
    existing_outcome, ingestion_id = _existing_ingestion_outcome(
        rule, state, obj, content_hash, now
    )
    if existing_outcome:
        return existing_outcome, ingestion_id
    try:
        ingestion = _create_ingestion(rule, obj, content_hash)
    except IntegrityError:
        logger.warning(
            "Ingestion record already exists after a concurrent discovery.",
            extra={"rule_id": str(rule.pk), "object_key": obj.key},
        )
        _mark_state_processed(state, obj, content_hash, now)
        return "duplicate", None
    return "queued", ingestion.pk


def _process_object(
    rule: IngestionRule,
    adapter: StorageAdapter,
    obj: StorageObject,
    *,
    now: datetime,
    policy: StabilityPolicy,
) -> tuple[str, UUID | None]:
    state, created = _observe_object(rule, obj, now)
    outcome = _preflight_object(
        rule,
        state,
        obj,
        ObjectScanContext(created=created, now=now, policy=policy),
    )
    if outcome is not None:
        return outcome, None

    content_hash = adapter.content_hash(obj.key)
    if _verify_stable_snapshot(state, obj, adapter, now) is not None:
        return "unstable", None
    return _create_or_requeue(rule, state, obj, content_hash, now)


def _inspect_objects(
    result: RuleScanResult,
    rule: IngestionRule,
    adapter: StorageAdapter,
    objects: list[StorageObject],
    context: ObjectScanContext,
) -> None:
    for obj in objects:
        try:
            outcome, _ = _process_object(rule, adapter, obj, now=context.now, policy=context.policy)
        except Exception as exc:
            result.errors += 1
            if len(result.failed_objects) < _MAX_FAILED_OBJECTS:
                result.failed_objects.append(obj.key)
            logger.exception(
                "Failed to inspect one ingestion object.",
                extra={
                    "rule_id": str(rule.pk),
                    "rule_name": rule.name,
                    "object_key": obj.key,
                    "error_category": type(exc).__name__,
                },
            )
            continue
        result.add_outcome(outcome)


def _complete_scan(rule: IngestionRule, result: RuleScanResult, started_at: datetime) -> None:
    completed_at = timezone.now()
    if result.errors:
        result.status = ScanStatus.FAILED.value
        result.message = "One or more objects could not be inspected."
        _set_rule_scan_state(
            rule,
            last_scan_completed_at=completed_at,
            last_scan_status=ScanStatus.FAILED,
            last_scan_error=result.message,
            last_scan_summary=result.as_dict(),
        )
    else:
        result.status = ScanStatus.SUCCESS.value
        result.message = "Scan completed."
        _set_rule_scan_state(
            rule,
            last_scan_completed_at=completed_at,
            last_scan_status=ScanStatus.SUCCESS,
            last_scan_error="",
            last_scan_summary=result.as_dict(),
            last_scan_watermark=started_at,
        )
    _audit_scan_finished(rule, result)


def _fail_scan(rule: IngestionRule, result: RuleScanResult, exc: Exception) -> None:
    completed_at = timezone.now()
    result.status = ScanStatus.FAILED.value
    result.errors += 1
    result.message = _safe_message(exc)
    _set_rule_scan_state(
        rule,
        last_scan_completed_at=completed_at,
        last_scan_status=ScanStatus.FAILED,
        last_scan_error=result.message,
        last_scan_summary=result.as_dict(),
    )
    logger.exception(
        "Ingestion rule scan failed.",
        extra={
            "rule_id": str(rule.pk),
            "rule_name": rule.name,
            "storage_backend": rule.storage_backend,
            "error_category": type(exc).__name__,
        },
    )
    _audit_scan_finished(rule, result)


def scan_ingestion_rule(
    rule: IngestionRule,
    *,
    now: datetime | None = None,
) -> RuleScanResult:
    """Scan one rule and return its observable result.

    Errors are recorded on the rule and in the audit trail; the caller can keep
    scanning unrelated rules.
    """

    started_at = now or timezone.now()
    result = RuleScanResult(
        rule_id=str(rule.pk), rule_name=rule.name, status=ScanStatus.RUNNING.value
    )
    _set_rule_scan_state(
        rule,
        last_scan_started_at=started_at,
        last_scan_status=ScanStatus.RUNNING,
        last_scan_error="",
    )
    _audit_scan_started(rule)

    try:
        policy = resolve_stability_policy(rule.stability_policy)
        adapter = get_storage_adapter(rule.storage_backend, rule.location_reference)
        objects = adapter.list_objects(pattern=rule.file_pattern, formats=rule.supported_formats)
        result.listed = len(objects)
        _inspect_objects(
            result,
            rule,
            adapter,
            objects,
            ObjectScanContext(created=False, now=started_at, policy=policy),
        )
        _complete_scan(rule, result, started_at)
    except Exception as exc:
        _fail_scan(rule, result, exc)
    return result


def scan_enabled_rules(
    *,
    now: datetime | None = None,
    force: bool = False,
) -> list[RuleScanResult]:
    """Scan every enabled rule that is due.

    ``force`` bypasses the per-rule scan interval and is used by manual
    operations and tests; it is not exposed as an API parameter.
    """

    scan_time = now or timezone.now()
    results: list[RuleScanResult] = []

    for rule in IngestionRule.objects.filter(is_enabled=True).order_by("name", "id"):
        if not force and not rule_is_due(rule, scan_time):
            results.append(
                RuleScanResult(
                    rule_id=str(rule.pk),
                    rule_name=rule.name,
                    status="not_due",
                    message="The rule scan interval has not elapsed.",
                )
            )
            continue

        try:
            results.append(scan_ingestion_rule(rule, now=scan_time))
        except Exception as exc:
            # The rule-level scan already handles expected failures. This guard
            # keeps a database or audit failure from cancelling other rules.
            logger.exception(
                "Unexpected ingestion scan failure.",
                extra={"rule_id": str(rule.pk), "rule_name": rule.name},
            )
            results.append(
                RuleScanResult(
                    rule_id=str(rule.pk),
                    rule_name=rule.name,
                    status=ScanStatus.FAILED.value,
                    errors=1,
                    message=_safe_message(exc),
                )
            )
    return results


def dispatch_ingestion(ingestion_id: UUID) -> None:
    """Dispatch the worker task for a committed ingestion record.

    A broker failure marks the record FAILED so a later scan can re-queue the
    same content instead of silently losing the file.
    """

    ingestion = Ingestion.objects.select_related("rule").filter(pk=ingestion_id).first()
    if ingestion is None:
        logger.warning(
            "Skipped dispatch for a missing ingestion record.",
            extra={"ingestion_id": str(ingestion_id)},
        )
        return

    try:
        from celery._state import get_current_app

        app = get_current_app()
        if app is None:
            raise RuntimeError("Celery application is not available.")
        task = app.tasks.get("apps.ingestion.tasks.process_ingestion_file")
        if task is None:
            raise RuntimeError("Ingestion worker task is not registered.")
        async_result = task.apply_async(args=[str(ingestion.pk)])
    except Exception as exc:
        logger.exception(
            "Unable to dispatch ingestion task.",
            extra={"ingestion_id": str(ingestion.pk), "error_category": type(exc).__name__},
        )
        with transaction.atomic():
            previous_state = ingestion.state
            Ingestion.objects.filter(pk=ingestion.pk).update(
                state=IngestionState.FAILED,
                error_summary=f"Task dispatch failed: {type(exc).__name__}",
            )
            _record_transition(
                ingestion,
                from_state=previous_state,
                to_state=IngestionState.FAILED,
                reason="task_dispatch_failed",
            )
            record_system_event(
                action=AuditAction.INGESTION_FAILED,
                resource_type=AuditResourceType.INGESTION,
                resource_id=ingestion.pk,
                status=AuditStatus.FAILURE,
                metadata={
                    "rule_name": ingestion.rule.name,
                    "file_name": ingestion.file_name,
                    "error_category": type(exc).__name__,
                },
            )
        return

    Ingestion.objects.filter(pk=ingestion.pk).update(celery_task_id=async_result.id)

    processed_at = timezone.now()
    IngestionObjectState.objects.filter(
        rule_id=ingestion.rule.pk,
        object_key=ingestion.object_key,
    ).update(
        last_content_hash=ingestion.content_hash,
        last_processed_content_hash=ingestion.content_hash,
        last_processed_modified_at=ingestion.file_modified_at,
        last_ingested_at=processed_at,
        updated_at=processed_at,
    )


__all__ = [
    "IngestionConfigurationError",
    "RuleScanResult",
    "dispatch_ingestion",
    "rule_is_due",
    "scan_enabled_rules",
    "scan_ingestion_rule",
]
