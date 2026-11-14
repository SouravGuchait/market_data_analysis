"""Celery worker orchestration for one ingestion UUID."""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import OperationalError
from django.db.utils import InterfaceError

from apps.ingestion.claim import claim_ingestion
from apps.ingestion.converters.types import (
    ConversionError,
    ConversionManifest,
    FormatMismatchError,
    UnsupportedFileFormatError,
)
from apps.ingestion.finalize import (
    audit_processing_started,
    finalize_ingestion,
    handle_ingestion_failure,
    handle_retries_exhausted,
    mark_retrying,
)
from apps.ingestion.manifest_load import load_completed_manifest
from apps.ingestion.models import Ingestion
from apps.ingestion.pipeline import iter_processed_chunks, stage_ingestion
from apps.ingestion.processing import (
    persist_conversion_rejections,
    persist_empty_file_batch,
    persist_processed_chunk,
)

logger = logging.getLogger("apps.ingestion.worker")

_TRANSIENT = (OperationalError, InterfaceError, ConnectionError, TimeoutError, OSError)
_PERMANENT = (
    ConversionError,
    FormatMismatchError,
    UnsupportedFileFormatError,
    FileNotFoundError,
    ValueError,
)


def _reuse_or_stage(ingestion: Ingestion) -> ConversionManifest:
    existing = load_completed_manifest(ingestion)
    if existing is not None:
        return existing
    return stage_ingestion(ingestion)


def _process_chunks(ingestion: Ingestion) -> None:
    batch_number = 1
    for chunk in iter_processed_chunks(ingestion):
        persist_processed_chunk(ingestion, batch_number, chunk)
        batch_number += 1


def _current_state(ingestion: Ingestion) -> str:
    return str(Ingestion.objects.values_list("state", flat=True).get(pk=ingestion.pk))


def _run_claimed(ingestion: Ingestion) -> str:
    audit_processing_started(ingestion)
    try:
        manifest = _reuse_or_stage(ingestion)
        persist_conversion_rejections(ingestion, list(manifest.rejections))
        if manifest.total_chunks == 0:
            persist_empty_file_batch(ingestion, manifest)
        else:
            _process_chunks(ingestion)
        return finalize_ingestion(ingestion).state
    except _PERMANENT as exc:
        handle_ingestion_failure(ingestion, exc)
        return _current_state(ingestion)
    except _TRANSIENT as exc:
        mark_retrying(ingestion, exc)
        raise
    except Exception as exc:
        handle_ingestion_failure(ingestion, exc)
        return _current_state(ingestion)


def run_ingestion_worker(ingestion_id: str, *, task_id: str = "") -> str | None:
    """Claim, convert, persist chunks, and finalize. Return final state."""

    ingestion = claim_ingestion(ingestion_id, task_id=task_id)
    if ingestion is None:
        return None
    return _run_claimed(ingestion)


def handle_worker_retry_exhausted(ingestion_id: str, exc: BaseException) -> None:
    ingestion = Ingestion.objects.filter(pk=ingestion_id).first()
    if ingestion is None:
        return
    handle_retries_exhausted(ingestion, exc)


def max_task_retries() -> int:
    return max(0, int(settings.INGESTION_TASK_MAX_RETRIES))
