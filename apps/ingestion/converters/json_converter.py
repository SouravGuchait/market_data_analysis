"""Streaming JSON array converter using ijson."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import BinaryIO

import ijson

from .chunk_writer import records_to_frame, write_frame_chunk
from .types import ChunkMeta, ConversionError, ConvertRequest


def _record_path(config: Mapping[str, object]) -> str:
    raw = config.get("json_records_path", "item")
    path = "item" if raw is None else str(raw).strip()
    return path or "item"


def _iter_records(handle: BinaryIO, path: str) -> Iterator[dict[str, object]]:
    try:
        for item in ijson.items(handle, path):
            if isinstance(item, dict):
                yield {str(key): value for key, value in item.items()}
                continue
            raise ConversionError("JSON records must be objects.")
    except ijson.JSONError as exc:
        raise ConversionError(f"Unable to parse JSON source: {exc}") from exc


def _flush_batch(
    batch: list[dict[str, object]],
    start_row: int,
    staging_dir: Path,
    chunk_number: int,
) -> ChunkMeta:
    frame = records_to_frame(batch, start_row)
    return write_frame_chunk(frame, staging_dir, chunk_number)


def convert_json(request: ConvertRequest) -> tuple[list[ChunkMeta], int]:
    """Stream a JSON array into staging CSV chunks."""

    request.staging_dir.mkdir(parents=True, exist_ok=True)
    path = _record_path(request.parser_config)
    chunks: list[ChunkMeta] = []
    batch: list[dict[str, object]] = []
    chunk_number = 1
    next_source_row = 1
    total_rows = 0
    with request.source_file.open("rb") as handle:
        for record in _iter_records(handle, path):
            batch.append(record)
            if len(batch) < request.chunk_size:
                continue
            meta = _flush_batch(batch, next_source_row, request.staging_dir, chunk_number)
            chunks.append(meta)
            total_rows += meta.row_count
            next_source_row += meta.row_count
            chunk_number += 1
            batch.clear()
    if batch:
        meta = _flush_batch(batch, next_source_row, request.staging_dir, chunk_number)
        chunks.append(meta)
        total_rows += meta.row_count
    return chunks, total_rows
