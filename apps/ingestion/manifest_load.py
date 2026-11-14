"""Load an existing staging manifest into a ConversionManifest."""

from __future__ import annotations

from collections.abc import Mapping

from apps.ingestion.converters.types import (
    ChunkMeta,
    ConversionManifest,
    ConversionRejection,
)
from apps.ingestion.models import Ingestion
from apps.ingestion.staging import ensure_staging_dir, list_chunk_paths, read_manifest


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        parsed = int(value)
    elif isinstance(value, str) and value.strip():
        parsed = int(value.strip())
    else:
        parsed = default
    return parsed


def _rejection(item: Mapping[str, object]) -> ConversionRejection:
    return ConversionRejection(
        source_row=_as_int(item.get("source_row")),
        reason_code=str(item.get("reason_code", "")),
        message=str(item.get("message", "")),
        field_name=str(item.get("field_name", "")),
        stage=str(item.get("stage", "CONVERSION")),
    )


def _chunk(item: Mapping[str, object]) -> ChunkMeta:
    return ChunkMeta(
        chunk_number=_as_int(item.get("chunk_number")),
        filename=str(item.get("filename", "")),
        row_count=_as_int(item.get("row_count")),
    )


def _usable_payload(ingestion: Ingestion) -> dict[str, object] | None:
    staging_dir = ensure_staging_dir(ingestion)
    if not (staging_dir / "manifest.json").is_file():
        return None
    payload = read_manifest(staging_dir)
    total_chunks = _as_int(payload.get("total_chunks"))
    chunks_missing = total_chunks > 0 and not list_chunk_paths(staging_dir)
    if payload.get("conversion_status") != "COMPLETED" or chunks_missing:
        return None
    return payload


def load_completed_manifest(ingestion: Ingestion) -> ConversionManifest | None:
    """Return a completed staging manifest when reusable chunks exist."""

    payload = _usable_payload(ingestion)
    if payload is None:
        return None
    raw_rejections = payload.get("rejections", [])
    raw_chunks = payload.get("chunks", [])
    rejection_items = raw_rejections if isinstance(raw_rejections, list) else []
    chunk_items = raw_chunks if isinstance(raw_chunks, list) else []
    rejections = [_rejection(item) for item in rejection_items if isinstance(item, Mapping)]
    chunks = [_chunk(item) for item in chunk_items if isinstance(item, Mapping)]
    return ConversionManifest(
        ingestion_id=str(payload.get("ingestion_id", ingestion.pk)),
        source_filename=str(payload.get("source_filename", "")),
        source_format=str(payload.get("source_format", "")),
        staging_format=str(payload.get("staging_format", "csv")),
        chunk_size=_as_int(payload.get("chunk_size"), ingestion.chunk_size),
        total_chunks=_as_int(payload.get("total_chunks")),
        total_rows=_as_int(payload.get("total_rows")),
        chunks=chunks,
        conversion_status="COMPLETED",
        rejections=rejections,
    )
