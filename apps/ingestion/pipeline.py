"""Conversion and Pandas validation pipeline for one ingestion (no persistence)."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction, AuditResourceType, AuditStatus
from apps.audit.services import record_audit_event, record_system_event
from apps.ingestion.converters.factory import convert_source
from apps.ingestion.converters.types import ConversionManifest, ConvertRequest
from apps.ingestion.models import (
    Ingestion,
    IngestionState,
    ValidationRule,
    ValidationRuleSet,
)
from apps.ingestion.parsers.dataframe_processor import ProcessedChunk, process_staged_chunk
from apps.ingestion.parsers.validation import ValidatorSpec, load_validators
from apps.ingestion.services import _record_transition, source_storage_key
from apps.ingestion.staging import (
    ensure_staging_dir,
    list_chunk_paths,
    read_manifest,
    write_manifest,
)

logger = logging.getLogger("apps.ingestion.pipeline")


@dataclass(frozen=True)
class PipelineResult:
    """Outcome of staging plus optional Pandas validation."""

    manifest: ConversionManifest
    chunks_processed: int
    accepted_rows: int
    rejected_rows: int
    duplicate_rows: int


def _parser_config(ingestion: Ingestion) -> dict[str, object]:
    raw = ingestion.rule.parser_config
    return dict(raw) if isinstance(raw, dict) else {}


def _schema_mapping(ingestion: Ingestion) -> dict[str, object]:
    raw = ingestion.rule.schema_mapping
    return dict(raw) if isinstance(raw, dict) else {}


def _resolve_source_path(ingestion: Ingestion) -> Path:
    from django.conf import settings

    root = Path(settings.INGESTION_LOCAL_ROOT).expanduser().resolve()
    key = source_storage_key(ingestion)
    path = (root / Path(key)).resolve()
    if not path.is_relative_to(root):
        raise FileNotFoundError("Storage key escaped the ingestion root.")
    if not path.is_file():
        raise FileNotFoundError(f"Source file was not found for key {key!r}.")
    return path


def _rule_rows(ingestion: Ingestion) -> list[dict[str, object]] | None:
    rule_set_id = ingestion.rule.validation_rule_set_id
    if rule_set_id is None:
        return None
    rule_set = ValidationRuleSet.objects.filter(pk=rule_set_id, is_active=True).first()
    if rule_set is None:
        return None
    rows = ValidationRule.objects.filter(rule_set=rule_set).order_by("sort_order", "id")
    return [
        {
            "field_name": rule.field_name,
            "validator": rule.validator,
            "parameters": rule.parameters if isinstance(rule.parameters, dict) else {},
            "is_enabled": rule.is_enabled,
        }
        for rule in rows
    ]


def _validators_for(ingestion: Ingestion) -> list[ValidatorSpec]:
    return load_validators(ingestion.data_type, _rule_rows(ingestion))


def stage_ingestion(ingestion: Ingestion) -> ConversionManifest:
    """Convert the source file into isolated CSV staging chunks."""

    staging_dir = ensure_staging_dir(ingestion)
    source_path = _resolve_source_path(ingestion)
    request = ConvertRequest(
        source_file=source_path,
        staging_dir=staging_dir,
        chunk_size=ingestion.chunk_size,
        parser_config=_parser_config(ingestion),
    )
    manifest = convert_source(request, ingestion_id=str(ingestion.pk))
    write_manifest(staging_dir, manifest)
    return manifest


def iter_processed_chunks(ingestion: Ingestion) -> Iterator[ProcessedChunk]:
    """Yield validated DataFrame splits for each staged CSV chunk."""

    staging_dir = ensure_staging_dir(ingestion)
    validators = _validators_for(ingestion)
    schema = _schema_mapping(ingestion)
    for chunk_path in list_chunk_paths(staging_dir):
        yield process_staged_chunk(
            chunk_path,
            data_type=ingestion.data_type,
            schema_mapping=schema,
            validators=validators,
            chunk_size=ingestion.chunk_size,
        )


def _audit_conversion(
    ingestion: Ingestion,
    *,
    status: str,
    metadata: Mapping[str, object],
) -> None:
    action = (
        AuditAction.INGESTION_COMPLETED
        if status == AuditStatus.SUCCESS
        else AuditAction.INGESTION_FAILED
    )
    owner = ingestion.owner
    if owner is None:
        record_system_event(
            action=action,
            resource_type=AuditResourceType.INGESTION,
            status=status,
            resource_id=str(ingestion.pk),
            metadata=dict(metadata),
        )
        return
    record_audit_event(
        actor=owner,
        action=action,
        resource_type=AuditResourceType.INGESTION,
        status=status,
        resource_id=str(ingestion.pk),
        metadata=dict(metadata),
    )


def run_conversion_pipeline(ingestion_id: str) -> PipelineResult:
    """Stage a file and validate staged chunks without market-data persistence."""

    ingestion = (
        Ingestion.objects.select_related("rule", "storage_file", "owner")
        .filter(pk=ingestion_id)
        .first()
    )
    if ingestion is None:
        raise Ingestion.DoesNotExist(ingestion_id)

    try:
        manifest = stage_ingestion(ingestion)
        accepted = 0
        rejected = len(manifest.rejections)
        duplicates = 0
        chunks_processed = 0
        for processed in iter_processed_chunks(ingestion):
            accepted += len(processed.valid_df)
            rejected += len(processed.rejected_df)
            duplicates += len(processed.duplicate_df)
            chunks_processed += 1
        result = PipelineResult(
            manifest=manifest,
            chunks_processed=chunks_processed,
            accepted_rows=accepted,
            rejected_rows=rejected,
            duplicate_rows=duplicates,
        )
        _persist_pipeline_counters(ingestion, result)
        _audit_conversion(
            ingestion,
            status=AuditStatus.SUCCESS,
            metadata={
                "stage": "conversion_validation",
                "total_chunks": manifest.total_chunks,
                "accepted_rows": accepted,
                "rejected_rows": rejected,
                "duplicate_rows": duplicates,
            },
        )
        return result
    except Exception as exc:
        _fail_conversion(ingestion, exc)
        raise


def _persist_pipeline_counters(ingestion: Ingestion, result: PipelineResult) -> None:
    Ingestion.objects.filter(pk=ingestion.pk).update(
        rows_read=result.manifest.total_rows,
        accepted_rows=result.accepted_rows,
        rejected_rows=result.rejected_rows,
        duplicate_rows=result.duplicate_rows,
        chunks_processed=result.chunks_processed,
        validation_rule_set_version=(
            ingestion.validation_rule_set_version
            or str(ingestion.rule.validation_rule_set_id or "default")
        ),
        updated_at=timezone.now(),
    )


def _fail_conversion(ingestion: Ingestion, exc: Exception) -> None:
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
            reason="conversion_failed",
            metadata={"error": str(exc)[:500]},
        )
    _audit_conversion(
        ingestion,
        status=AuditStatus.FAILURE,
        metadata={"stage": "conversion", "error": str(exc)[:500]},
    )


def load_existing_manifest(ingestion: Ingestion) -> dict[str, object]:
    return read_manifest(ensure_staging_dir(ingestion))
