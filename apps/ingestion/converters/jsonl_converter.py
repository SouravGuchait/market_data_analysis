"""Line-oriented JSONL converter with per-line rejection recovery."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from .chunk_writer import records_to_frame, write_frame_chunk
from .types import (
    SOURCE_ROW_COLUMN,
    ChunkMeta,
    ConversionError,
    ConversionRejection,
    ConvertRequest,
)


def _rejection(
    line_number: int,
    reason_code: str,
    message: str,
) -> ConversionRejection:
    return ConversionRejection(
        source_row=line_number,
        reason_code=reason_code,
        message=message,
    )


def _decoded_record(
    payload: object,
    line_number: int,
) -> tuple[dict[str, object] | None, ConversionRejection | None]:
    if isinstance(payload, dict):
        return {str(key): value for key, value in payload.items()}, None
    return None, _rejection(
        line_number,
        "INVALID_JSONL_RECORD",
        "JSONL records must be objects.",
    )


def _parse_line(
    line: str,
    line_number: int,
) -> tuple[dict[str, object] | None, ConversionRejection | None]:
    text = line.strip()
    if not text:
        return None, None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, _rejection(line_number, "MALFORMED_JSONL", str(exc))
    return _decoded_record(payload, line_number)


def _iter_lines(path: Path) -> Iterator[tuple[int, str]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            yield from enumerate(handle, start=1)
    except OSError as exc:
        raise ConversionError(f"Unable to read JSONL source: {exc}") from exc


def convert_jsonl(
    request: ConvertRequest,
) -> tuple[list[ChunkMeta], int, list[ConversionRejection]]:
    """Convert JSONL into CSV chunks; malformed lines become conversion rejections."""

    request.staging_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[ChunkMeta] = []
    rejections: list[ConversionRejection] = []
    batch: list[dict[str, object]] = []
    batch_rows: list[int] = []
    chunk_number = 1
    total_rows = 0

    def flush() -> None:
        nonlocal chunk_number, total_rows, batch, batch_rows
        if not batch:
            return
        frame = records_to_frame(batch, batch_rows[0])
        # Preserve exact source line numbers even when gaps exist from rejections.
        frame[SOURCE_ROW_COLUMN] = batch_rows
        meta = write_frame_chunk(frame, request.staging_dir, chunk_number)
        chunks.append(meta)
        total_rows += meta.row_count
        chunk_number += 1
        batch = []
        batch_rows = []

    for line_number, line in _iter_lines(request.source_file):
        record, rejection = _parse_line(line, line_number)
        if rejection is not None:
            rejections.append(rejection)
            continue
        if record is None:
            continue
        batch.append(record)
        batch_rows.append(line_number)
        if len(batch) >= request.chunk_size:
            flush()
    flush()
    return chunks, total_rows, rejections
