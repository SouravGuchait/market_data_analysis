"""User-triggered file storage and rule management.

Parsing and market-data persistence stay in later features. This module
registers files. Import queues each matching ingestion for Celery.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import (
    SUPPORTED_FILE_FORMATS,
    DataType,
    Ingestion,
    IngestionRule,
    IngestionState,
    StorageBackend,
    _safe_storage_prefix,
)
from .services import (
    _record_transition,
    _schedule_dispatch,
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
    return LocalStorageAdapter(settings.INGESTION_SYSTEM_ROOT, "")


USER_FILE_SEGMENT = "files"


def user_files_location(owner: User) -> str:
    """Relative directory under INGESTION_SYSTEM_ROOT for one user's files."""

    return f"users/{owner.pk}/{USER_FILE_SEGMENT}"


def _user_location(owner: User, storage_prefix: str) -> str:
    _checked_prefix(storage_prefix)
    return user_files_location(owner)


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
    prefix = data.get("storage_prefix", rule.storage_prefix or USER_FILE_SEGMENT)
    if isinstance(prefix, str) and prefix:
        _checked_prefix(prefix)
    rule.storage_prefix = USER_FILE_SEGMENT
    rule.location_reference = user_files_location(owner)
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
