"""Scheduled discovery and ingestion-record orchestration.

One scan lists objects through a storage adapter, applies per-object stability
and watermark rules, calculates a content identity and creates one ingestion
record per eligible file. Task dispatch happens only after the record commits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
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
    ScanStatus,
    StabilityPolicy,
    StorageFile,
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

    def as_dict(self) -> dict[str, Any]:
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


def _set_rule_scan_state(rule: IngestionRule, **fields: Any) -> None:
    for name, value in fields.items():
        setattr(rule, name, value)
    rule.save(update_fields=[*fields, "updated_at"])


def _record_transition(
    ingestion: Ingestion,
    *,
    from_state: str,
    to_state: str,
    reason: str = "",
    metadata: dict[str, Any] | None = None,
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


def _metadata_requires_processing(state: IngestionObjectState, obj: StorageObject) -> bool:
    if not state.last_processed_content_hash or state.last_processed_modified_at is None:
        return True
    return obj.modified_at > state.last_processed_modified_at


def _latest_state_requires_processing(rule: IngestionRule, obj: StorageObject) -> bool:
    latest_state = (
        Ingestion.objects.filter(rule=rule, object_key=obj.key)
        .order_by("-created_at")
        .values_list("state", flat=True)
        .first()
    )
    return latest_state in _REQUEUEABLE_STATES


def _inside_overlap_window(
    rule: IngestionRule,
    obj: StorageObject,
    policy: StabilityPolicy,
) -> bool:
    watermark = rule.last_scan_watermark
    if watermark is None:
        return False
    earliest = watermark - timedelta(seconds=policy.overlap_seconds)
    return obj.modified_at >= earliest


def _is_candidate(
    rule: IngestionRule,
    state: IngestionObjectState,
    obj: StorageObject,
    policy: StabilityPolicy,
) -> bool:
    """Avoid re-hashing objects already processed at the same metadata."""

    if _metadata_requires_processing(state, obj) or _latest_state_requires_processing(rule, obj):
        return True
    return _inside_overlap_window(rule, obj, policy)


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


def source_storage_key(ingestion: Ingestion) -> str:
    """Return the relative key for an uploaded file or a discovered object.

    Uploads keep the key on ``StorageFile``. A scan of a file that was never
    uploaded uses the rule location plus the object key. The result stays
    relative to ``INGESTION_LOCAL_ROOT``.
    """

    storage_file = ingestion.storage_file
    if storage_file is not None and storage_file.storage_key:
        return storage_file.storage_key
    prefix = ingestion.rule.location_reference.strip("/")
    object_key = ingestion.object_key.strip("/")
    if prefix:
        return f"{prefix}/{object_key}"
    return object_key


def _schedule_dispatch(ingestion: Ingestion, *, enabled: bool) -> None:
    if not enabled:
        return
    transaction.on_commit(func=lambda ingestion_id=ingestion.pk: dispatch_ingestion(ingestion_id))


def _linked_storage_file(rule: IngestionRule, obj: StorageObject) -> StorageFile | None:
    if rule.owner is None:
        return None
    storage_key: str = f"{rule.location_reference.strip('/')}/{obj.key}"
    return StorageFile.objects.filter(
        owner=rule.owner,
        storage_key=storage_key,
        is_active=True,
    ).first()


def _create_ingestion(
    rule: IngestionRule,
    obj: StorageObject,
    content_hash: str,
    *,
    dispatch: bool,
) -> Ingestion:
    with transaction.atomic():
        storage_file: StorageFile | None = _linked_storage_file(rule, obj)
        file_name: str = storage_file.original_filename if storage_file else obj.name
        ingestion = Ingestion.objects.create(
            rule=rule,
            owner=rule.owner,
            storage_file=storage_file,
            storage_backend=rule.storage_backend,
            object_key=obj.key,
            file_name=file_name,
            file_size_bytes=obj.size_bytes,
            file_modified_at=obj.modified_at,
            content_hash=content_hash,
            data_type=rule.data_type,
            validation_rule_set_version=str(rule.validation_rule_set_id or ""),
            chunk_size=rule.chunk_size,
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
        _schedule_dispatch(ingestion, enabled=dispatch)
    return ingestion


def _requeue_ingestion(
    rule: IngestionRule,
    ingestion: Ingestion,
    obj: StorageObject,
    *,
    dispatch: bool,
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
        _schedule_dispatch(ingestion, enabled=dispatch)
    return ingestion


@dataclass(frozen=True)
class _Discovery:
    now: datetime
    policy: StabilityPolicy
    dispatch: bool


def _existing_ingestion_outcome(
    rule: IngestionRule,
    state: IngestionObjectState,
    obj: StorageObject,
    content_hash: str,
    discovery: _Discovery,
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
        ingestion = _requeue_ingestion(rule, existing, obj, dispatch=discovery.dispatch)
        return "requeued", ingestion.pk

    _mark_state_processed(state, obj, content_hash, discovery.now)
    return "duplicate", None


def _observe_object(
    rule: IngestionRule,
    obj: StorageObject,
    now: datetime,
) -> tuple[IngestionObjectState, bool]:
    state, created = IngestionObjectState.objects.get_or_create(
        rule=rule,
        object_key=obj.key,
        defaults={
            "object_name": obj.name,
            "first_seen_at": now,
            "last_seen_at": now,
        },
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


def _historical_outcome(
    state: IngestionObjectState,
    *,
    created: bool,
) -> tuple[str, None] | None:
    if not created and not state.is_historical:
        return None
    if not state.is_historical:
        state.is_historical = True
        state.save(update_fields=["is_historical", "updated_at"])
    return "historical", None


def _clear_historical(state: IngestionObjectState) -> None:
    if not state.is_historical:
        return
    state.is_historical = False
    state.save(update_fields=["is_historical", "updated_at"])


def _file_is_unstable(
    state: IngestionObjectState,
    obj: StorageObject,
    now: datetime,
    policy: StabilityPolicy,
) -> bool:
    if state.stable_scan_count < policy.required_stable_scans:
        return True
    return (now - obj.modified_at).total_seconds() < policy.min_file_age_seconds


def _reset_moving_file(state: IngestionObjectState, obj: StorageObject, now: datetime) -> None:
    state.stable_scan_count = 1
    state.observed_size_bytes = obj.size_bytes
    state.observed_modified_at = obj.modified_at
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


def _create_queued_ingestion(
    rule: IngestionRule,
    state: IngestionObjectState,
    obj: StorageObject,
    content_hash: str,
    discovery: _Discovery,
) -> tuple[str, UUID | None]:
    try:
        ingestion = _create_ingestion(rule, obj, content_hash, dispatch=discovery.dispatch)
    except IntegrityError:
        logger.warning(
            "Ingestion record already exists after a concurrent discovery.",
            extra={"rule_id": str(rule.pk), "object_key": obj.key},
        )
        _mark_state_processed(state, obj, content_hash, discovery.now)
        return "duplicate", None
    return "queued", ingestion.pk


def _queue_stable_object(
    rule: IngestionRule,
    adapter: StorageAdapter,
    state: IngestionObjectState,
    obj: StorageObject,
    discovery: _Discovery,
) -> tuple[str, UUID | None]:
    content_hash = adapter.content_hash(obj.key)
    after_hash = adapter.stat(obj.key)
    if after_hash.size_bytes != obj.size_bytes or after_hash.modified_at != obj.modified_at:
        _reset_moving_file(state, after_hash, discovery.now)
        return "unstable", None
    existing_outcome, ingestion_id = _existing_ingestion_outcome(
        rule,
        state,
        obj,
        content_hash,
        discovery,
    )
    if existing_outcome:
        return existing_outcome, ingestion_id
    return _create_queued_ingestion(rule, state, obj, content_hash, discovery)


def _readiness_outcome(
    rule: IngestionRule,
    state: IngestionObjectState,
    obj: StorageObject,
    discovery: _Discovery,
) -> tuple[str, None] | None:
    _clear_historical(state)
    if _file_is_unstable(state, obj, discovery.now, discovery.policy):
        return "unstable", None
    if not _is_candidate(rule, state, obj, discovery.policy):
        return "duplicate", None
    return None


def _process_object(
    rule: IngestionRule,
    adapter: StorageAdapter,
    obj: StorageObject,
    discovery: _Discovery,
) -> tuple[str, UUID | None]:
    state, created = _observe_object(rule, obj, discovery.now)
    before_watermark = obj.modified_at <= _baseline_watermark(rule)
    if before_watermark and not state.last_processed_content_hash:
        historical = _historical_outcome(state, created=created)
        if historical is not None:
            return historical
    pending = _readiness_outcome(rule, state, obj, discovery)
    if pending is not None:
        return pending
    return _queue_stable_object(rule, adapter, state, obj, discovery)


_OUTCOME_FIELDS = {
    "queued": "queued",
    "requeued": "requeued",
    "duplicate": "duplicates",
    "unstable": "unstable",
    "historical": "historical",
}


def _tally_outcome(result: RuleScanResult, outcome: str) -> None:
    field_name = _OUTCOME_FIELDS.get(outcome)
    if field_name is None:
        return
    current = getattr(result, field_name)
    if isinstance(current, int):
        setattr(result, field_name, current + 1)


def _record_object_failure(
    result: RuleScanResult,
    rule: IngestionRule,
    obj: StorageObject,
    exc: Exception,
) -> None:
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


def _inspect_objects(
    rule: IngestionRule,
    result: RuleScanResult,
    adapter: StorageAdapter,
    objects: list[StorageObject],
    discovery: _Discovery,
) -> None:
    for obj in objects:
        try:
            outcome, _ingestion_id = _process_object(rule, adapter, obj, discovery)
        except Exception as exc:
            _record_object_failure(result, rule, obj, exc)
            continue
        _tally_outcome(result, outcome)


def _finish_scan(rule: IngestionRule, result: RuleScanResult, started_at: datetime) -> None:
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
    result.status = ScanStatus.FAILED.value
    result.errors += 1
    result.message = _safe_message(exc)
    _set_rule_scan_state(
        rule,
        last_scan_completed_at=timezone.now(),
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
    dispatch: bool = True,
) -> RuleScanResult:
    """Scan one rule and return its observable result.

    Errors are recorded on the rule and in the audit trail; the caller can keep
    scanning unrelated rules.
    """

    started_at = now or timezone.now()
    result = RuleScanResult(
        rule_id=str(rule.pk),
        rule_name=rule.name,
        status=ScanStatus.RUNNING.value,
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
        objects = adapter.list_objects(
            pattern=rule.file_pattern,
            formats=rule.supported_formats,
        )
        result.listed = len(objects)
        _inspect_objects(
            rule,
            result,
            adapter,
            objects,
            _Discovery(now=started_at, policy=policy, dispatch=dispatch),
        )
    except Exception as exc:
        _fail_scan(rule, result, exc)
        return result
    _finish_scan(rule, result, started_at)
    return result


def scan_enabled_rules(
    *,
    now: datetime | None = None,
    force: bool = False,
) -> list[RuleScanResult]:
    """Scan every enabled administrative rule that is due.

    Rules owned by a user are excluded. Those rules are scanned only through
    the authenticated manual scan API.

    ``force`` bypasses the per-rule scan interval and is used by manual
    operations and tests; it is not exposed as an API parameter.
    """

    scan_time = now or timezone.now()
    results: list[RuleScanResult] = []

    queryset = IngestionRule.objects.filter(is_enabled=True, owner__isnull=True)
    for rule in queryset.order_by("name", "id"):
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
        from .tasks import process_ingestion_file

        process_task: Any = process_ingestion_file
        async_result = process_task.delay(str(ingestion.pk))
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
    "source_storage_key",
]
