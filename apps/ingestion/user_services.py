"""User-triggered file storage, rule management, scan and dispatch.

Parsing and market-data persistence stay in later features. This module only
registers files and hands each ingestion UUID to Celery.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    SUPPORTED_FILE_FORMATS,
    DataType,
    Ingestion,
    IngestionRule,
    IngestionScan,
    IngestionState,
    ScanStatus,
    StorageBackend,
    _safe_storage_prefix,
)
from .selectors import get_owned_rule, list_owned_rules
from .services import (
    _record_transition,
    _schedule_dispatch,
    dispatch_ingestion,
    scan_ingestion_rule,
)
from .storage import LocalStorageAdapter, StorageError

_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_HISTORICAL_FLOOR = datetime(1970, 1, 2, tzinfo=UTC)


class IngestionRequestError(Exception):
    """A user request failed validation before any processing side effect."""

    def __init__(self, message: str, errors: dict[str, list[str]]) -> None:
        super().__init__(message)
        self.message = message
        self.errors = errors


def _request_error(field: str, detail: str) -> IngestionRequestError:
    return IngestionRequestError("Validation failed", {field: [detail]})


def _root_adapter() -> LocalStorageAdapter:
    return LocalStorageAdapter(settings.INGESTION_LOCAL_ROOT, "")


def _user_location(owner: User, storage_prefix: str) -> str:
    return f"users/{owner.pk}/{storage_prefix}"


def _checked_prefix(storage_prefix: str) -> str:
    prefix = storage_prefix.strip()
    if not _safe_storage_prefix(prefix):
        raise _request_error(
            "storage_prefix",
            "Storage prefix must be a single relative path segment.",
        )
    return prefix


def _checked_filename(original_name: str) -> tuple[str, str]:
    name = (original_name or "").strip()
    if not name or "/" in name or "\\" in name or ".." in Path(name).parts:
        raise _request_error("files", "File names must not contain a path.")
    if not _FILENAME_PATTERN.match(name):
        raise _request_error("files", "File names contain unsupported characters.")
    extension = Path(name).suffix.lower().lstrip(".")
    if extension not in SUPPORTED_FILE_FORMATS:
        allowed = ", ".join(SUPPORTED_FILE_FORMATS)
        raise _request_error("files", f"Unsupported file extension. Supported: {allowed}.")
    return name, extension


def _formats_for_parser(parser_config: dict[str, object]) -> list[str]:
    raw_format = parser_config.get("format", "csv")
    return [str(raw_format).lower()]


def _apply_rule_fields(rule: IngestionRule, owner: User, data: dict[str, object]) -> None:
    prefix = data.get("storage_prefix", rule.storage_prefix)
    if isinstance(prefix, str) and prefix:
        checked = _checked_prefix(prefix)
        rule.storage_prefix = checked
        rule.location_reference = _user_location(owner, checked)
    for field in (
        "name",
        "data_type",
        "file_pattern",
        "parser_config",
        "instrument_mapping",
        "exchange_mapping",
        "validation_rule_set_id",
        "chunk_size",
        "is_enabled",
    ):
        if field in data:
            setattr(rule, field, data[field])
    if isinstance(rule.parser_config, dict):
        rule.supported_formats = _formats_for_parser(rule.parser_config)
    rule.storage_backend = StorageBackend.LOCAL


def _persist_rule(rule: IngestionRule) -> IngestionRule:
    try:
        rule.full_clean()
    except ValidationError as exc:
        errors = {
            field: [str(item) for item in messages] for field, messages in exc.message_dict.items()
        }
        raise IngestionRequestError("Validation failed", errors) from exc
    rule.save()
    try:
        _root_adapter().ensure_directory(rule.location_reference)
    except StorageError as exc:
        raise IngestionRequestError(
            "Validation failed",
            {"storage_prefix": ["The configured local directory could not be created."]},
        ) from exc
    return rule


def create_user_rule(owner: User, data: dict[str, object]) -> IngestionRule:
    """Create a rule whose storage location is derived from the owner."""

    rule = IngestionRule(
        owner=owner,
        initial_watermark=_HISTORICAL_FLOOR,
        stability_policy={"required_stable_scans": 2},
    )
    _apply_rule_fields(rule, owner, data)
    if not rule.data_type:
        raise _request_error("data_type", "A data type is required.")
    if rule.data_type not in {DataType.DAILY_OHLCV, DataType.TICK_DATA}:
        raise _request_error("data_type", "Data type must be DAILY_OHLCV or TICK_DATA.")
    return _persist_rule(rule)


def update_user_rule(
    rule: IngestionRule,
    owner: User,
    data: dict[str, object],
) -> IngestionRule:
    """Update an owned rule without accepting a client owner or filesystem path."""

    _apply_rule_fields(rule, owner, data)
    return _persist_rule(rule)


def scan_owned_rule(owner: User, rule: IngestionRule) -> IngestionScan:
    """Discover eligible files for one owned rule without dispatching tasks."""

    if not rule.is_enabled:
        raise _request_error("rule_id", "The rule is disabled.")

    started_at = timezone.now()
    result = scan_ingestion_rule(rule, dispatch=False)
    status = ScanStatus.FAILED if result.errors else ScanStatus.SUCCESS
    return IngestionScan.objects.create(
        owner=owner,
        rule=rule,
        status=status,
        discovered_count=result.listed,
        queued_count=result.queued + result.requeued,
        skipped_count=result.duplicates + result.unstable + result.historical,
        failed_count=result.errors,
        started_at=started_at,
        completed_at=timezone.now(),
    )


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def resolve_owned_rule(owner: User, reference: str) -> IngestionRule | None:
    """Find one owned rule by its id or by the exact name the user created."""

    parsed = _parse_uuid(reference)
    if parsed is not None:
        return get_owned_rule(owner, parsed)
    matches = list(list_owned_rules(owner).filter(name=reference)[:2])
    if len(matches) > 1:
        raise _request_error("rule_id", "More than one rule uses that name. Pass the rule id.")
    if not matches:
        return None
    return matches[0]


def _dispatch_ids(ingestion_ids: tuple[uuid.UUID, ...]) -> None:
    for ingestion_id in ingestion_ids:
        dispatch_ingestion(ingestion_id)


def _match_ingestion_references(
    owner: User,
    references: Sequence[str],
) -> tuple[list[uuid.UUID], int]:
    """Resolve file names or ids to owned ingestion primary keys."""

    requested = list(dict.fromkeys(references))
    parsed_ids = [parsed for item in requested if (parsed := _parse_uuid(item)) is not None]
    names = [item for item in requested if _parse_uuid(item) is None]
    by_id = {row.pk: row for row in Ingestion.objects.filter(owner=owner, pk__in=parsed_ids)}
    by_name: dict[str, list[Ingestion]] = {}
    for row in Ingestion.objects.filter(owner=owner, file_name__in=names):
        by_name.setdefault(row.file_name, []).append(row)
    if any(len(rows) > 1 for rows in by_name.values()):
        raise _request_error(
            "ingestion_ids",
            "A file name matches more than one ingestion. Pass its id.",
        )

    matched: list[uuid.UUID] = []
    unknown = 0
    for item in requested:
        parsed = _parse_uuid(item)
        row = by_id.get(parsed) if parsed is not None else _single_named_row(by_name, item)
        if row is None:
            unknown += 1
        elif row.pk not in matched:
            matched.append(row.pk)
    return matched, unknown


def _single_named_row(by_name: dict[str, list[Ingestion]], name: str) -> Ingestion | None:
    rows = by_name.get(name, [])
    if not rows:
        return None
    return rows[0]


def dispatch_owned_ingestions(
    owner: User,
    references: Sequence[str] | None,
) -> dict[str, object]:
    """Dispatch queued ingestions for this user after the surrounding commit.

    Each reference is the uploaded file name or the ingestion id. The response
    returns both, so a later request can send the file name.
    """

    queryset = Ingestion.objects.filter(owner=owner)
    unknown = 0
    if references is not None:
        matched, unknown = _match_ingestion_references(owner, references)
        queryset = queryset.filter(pk__in=matched)

    with transaction.atomic():
        eligible = list(
            queryset.filter(state=IngestionState.QUEUED, celery_task_id="")
            .select_for_update()
            .order_by("file_name", "pk")
        )
        eligible_ids = [row.pk for row in eligible]
        skipped = queryset.exclude(pk__in=eligible_ids).count() + unknown
        transaction.on_commit(lambda ids=tuple(eligible_ids): _dispatch_ids(ids))

    return {
        "queued": len(eligible),
        "skipped": skipped,
        "ingestion_ids": [{"name": row.file_name, "id": str(row.pk)} for row in eligible],
    }


def retry_owned_ingestion(ingestion: Ingestion) -> Ingestion:
    """Queue a failed ingestion again without clearing committed progress."""

    if ingestion.state != IngestionState.FAILED:
        raise _request_error("status", "Only a failed ingestion can be retried.")

    with transaction.atomic():
        previous_state = ingestion.state
        ingestion.state = IngestionState.RETRYING
        ingestion.save(update_fields=["state", "updated_at"])
        _record_transition(
            ingestion,
            from_state=previous_state,
            to_state=IngestionState.RETRYING,
            reason="retry",
        )
        ingestion.state = IngestionState.QUEUED
        ingestion.celery_task_id = ""
        ingestion.finished_at = None
        ingestion.save(update_fields=["state", "celery_task_id", "finished_at", "updated_at"])
        _record_transition(
            ingestion,
            from_state=IngestionState.RETRYING,
            to_state=IngestionState.QUEUED,
            reason="retry",
        )
        _schedule_dispatch(ingestion, enabled=True)
    return ingestion
