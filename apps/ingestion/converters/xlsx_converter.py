"""Read-only openpyxl converter into staging CSV chunks."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from .chunk_writer import write_frame_chunk
from .types import SOURCE_ROW_COLUMN, ChunkMeta, ConversionError, ConvertRequest


def _config_int(config: Mapping[str, object], key: str, default: int) -> int:
    raw = config.get(key, default)
    if raw is None:
        return default
    if isinstance(raw, bool):
        raise ConversionError(f"Parser config {key!r} must be an integer.")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError as exc:
            raise ConversionError(f"Parser config {key!r} must be an integer.") from exc
    raise ConversionError(f"Parser config {key!r} must be an integer.")


def _config_str(config: Mapping[str, object], key: str, default: str) -> str:
    raw = config.get(key, default)
    return default if raw is None else str(raw)


def _header_names(values: Sequence[object]) -> list[str]:
    headers: list[str] = []
    for index, value in enumerate(values):
        if value is None or str(value).strip() == "":
            headers.append(f"column_{index + 1}")
            continue
        headers.append(str(value).strip())
    if not headers:
        raise ConversionError("XLSX header row is empty.")
    return headers


def _row_mapping(
    headers: Sequence[str],
    values: Sequence[object],
) -> dict[str, object]:
    width = min(len(headers), len(values))
    return {headers[index]: values[index] for index in range(width)}


def _flush_xlsx_batch(
    batch: list[dict[str, object]],
    batch_rows: list[int],
    staging_dir: Path,
    chunk_number: int,
) -> ChunkMeta:
    frame = pd.DataFrame.from_records(batch)
    frame[SOURCE_ROW_COLUMN] = batch_rows
    return write_frame_chunk(frame, staging_dir, chunk_number)


def _iter_sheet_rows(
    source_file: Path,
    worksheet: str,
    header_row: int,
) -> Iterator[tuple[int, dict[str, object]]]:
    try:
        workbook = load_workbook(source_file, read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ConversionError(f"Unable to open XLSX source: {exc}") from exc
    try:
        sheet_name = worksheet or workbook.sheetnames[0]
        if sheet_name not in workbook.sheetnames:
            raise ConversionError(f"Worksheet {worksheet!r} was not found.")
        sheet = workbook[sheet_name]
        rows = sheet.iter_rows(min_row=header_row, values_only=True)
        header_values = next(rows, None)
        if header_values is None:
            raise ConversionError("XLSX header row is empty.")
        headers = _header_names(list(header_values))
        for row_number, values in enumerate(rows, start=header_row + 1):
            if values is None or all(value is None for value in values):
                continue
            yield row_number, _row_mapping(headers, list(values))
    finally:
        workbook.close()


def convert_xlsx(request: ConvertRequest) -> tuple[list[ChunkMeta], int]:
    """Stream worksheet rows into staging CSV chunks."""

    request.staging_dir.mkdir(parents=True, exist_ok=True)
    worksheet = _config_str(request.parser_config, "worksheet", "")
    header_row = _config_int(request.parser_config, "header_row", 1)
    chunks: list[ChunkMeta] = []
    batch: list[dict[str, object]] = []
    batch_rows: list[int] = []
    chunk_number = 1
    total_rows = 0
    for row_number, record in _iter_sheet_rows(request.source_file, worksheet, header_row):
        batch.append(record)
        batch_rows.append(row_number)
        if len(batch) < request.chunk_size:
            continue
        meta = _flush_xlsx_batch(batch, batch_rows, request.staging_dir, chunk_number)
        chunks.append(meta)
        total_rows += meta.row_count
        chunk_number += 1
        batch.clear()
        batch_rows.clear()
    if batch:
        meta = _flush_xlsx_batch(batch, batch_rows, request.staging_dir, chunk_number)
        chunks.append(meta)
        total_rows += meta.row_count
    return chunks, total_rows
