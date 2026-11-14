"""Queue an uploaded file at once.

Files that appear in the store without an upload still go through the two-scan
stability check in ``scan_ingestion_rule``.
"""

from __future__ import annotations

from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.utils import timezone

from .models import Ingestion, IngestionObjectState, IngestionRule, StorageFile
from .services import _create_ingestion
from .storage import LocalStorageAdapter, StorageObject
from .user_services import user_files_location

_STABLE_SCAN_COUNT = 2


def queue_uploaded_file(owner: User, storage_file: StorageFile) -> list[Ingestion]:
    """Match enabled rules and queue one ingestion per match."""

    queued: list[Ingestion] = []
    for rule in _matching_rules(owner, storage_file.original_filename):
        ingestion = _queue_for_rule(rule, storage_file)
        if ingestion is not None:
            queued.append(ingestion)
    return queued


def upload_is_registered(owner: User, storage_file: StorageFile) -> bool:
    """Return whether this stored file already has an ingestion."""

    return Ingestion.objects.filter(owner=owner, storage_file=storage_file).exists()


def _matching_rules(owner: User, filename: str) -> list[IngestionRule]:
    extension = Path(filename).suffix.lower().lstrip(".")
    location = user_files_location(owner)
    rules = IngestionRule.objects.filter(owner=owner, is_enabled=True, location_reference=location)
    return [
        rule
        for rule in rules
        if fnmatch(filename, rule.file_pattern) and _format_matches(rule, extension)
    ]


def _format_matches(rule: IngestionRule, extension: str) -> bool:
    formats = rule.supported_formats
    if not isinstance(formats, list):
        return False
    allowed = {str(item).lower().lstrip(".") for item in formats}
    return extension in allowed


def _queue_for_rule(rule: IngestionRule, storage_file: StorageFile) -> Ingestion | None:
    object_key = Path(storage_file.storage_key).name
    adapter = LocalStorageAdapter(settings.INGESTION_SYSTEM_ROOT, rule.location_reference)
    obj = adapter.stat(object_key)
    content_hash = storage_file.content_hash or adapter.content_hash(object_key)
    if _ingestion_exists(rule, object_key, content_hash):
        return None
    try:
        ingestion = _create_ingestion(rule, obj, content_hash, dispatch=True)
    except IntegrityError:
        return None
    _remember_uploaded(rule, obj, content_hash)
    return ingestion


def _remember_uploaded(rule: IngestionRule, obj: StorageObject, content_hash: str) -> None:
    now = timezone.now()
    state, _created = IngestionObjectState.objects.get_or_create(
        rule=rule,
        object_key=obj.name,
        defaults={"object_name": obj.name, "first_seen_at": now, "last_seen_at": now},
    )
    _save_processed_state(state, obj, content_hash, now)


def _save_processed_state(
    state: IngestionObjectState,
    obj: StorageObject,
    content_hash: str,
    now: datetime,
) -> None:
    state.object_name = obj.name
    state.observed_size_bytes = obj.size_bytes
    state.observed_modified_at = obj.modified_at
    state.stable_scan_count = _STABLE_SCAN_COUNT
    state.is_historical = False
    state.last_seen_at = now
    state.last_content_hash = content_hash
    state.last_processed_content_hash = content_hash
    state.last_processed_modified_at = obj.modified_at
    state.last_ingested_at = now
    state.save()


def _ingestion_exists(rule: IngestionRule, object_key: str, content_hash: str) -> bool:
    return Ingestion.objects.filter(
        rule=rule,
        object_key=object_key,
        content_hash=content_hash,
    ).exists()
