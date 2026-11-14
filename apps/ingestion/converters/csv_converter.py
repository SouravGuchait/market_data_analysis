"""CSV source to bounded staging CSV chunks."""

from __future__ import annotations

from collections.abc import Iterator, Mapping

import pandas as pd

from .chunk_writer import attach_source_rows, write_frame_chunk
from .types import ChunkMeta, ConversionError, ConvertRequest


def _config_str(config: Mapping[str, object], key: str, default: str) -> str:
    raw = config.get(key, default)
    return default if raw is None else str(raw)


def _csv_reader(request: ConvertRequest) -> Iterator[pd.DataFrame]:
    encoding = _config_str(request.parser_config, "encoding", "utf-8")
    delimiter = _config_str(request.parser_config, "delimiter", ",")
    try:
        reader = pd.read_csv(
            request.source_file,
            chunksize=request.chunk_size,
            dtype=str,
            encoding=encoding,
            sep=delimiter,
            keep_default_na=False,
        )
    except Exception as exc:  # noqa: BLE001 - surface as conversion failure
        raise ConversionError(f"Unable to read CSV source: {exc}") from exc
    return reader


def convert_csv(request: ConvertRequest) -> tuple[list[ChunkMeta], int]:
    """Split a CSV source into staging chunks. Returns chunk metas and row count."""

    request.staging_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[ChunkMeta] = []
    next_source_row = 2  # header is row 1; first data row is 2
    total_rows = 0
    for index, frame in enumerate(_csv_reader(request), start=1):
        if frame.empty:
            continue
        numbered = attach_source_rows(frame, next_source_row)
        chunks.append(write_frame_chunk(numbered, request.staging_dir, index))
        total_rows += len(numbered)
        next_source_row += len(numbered)
    return chunks, total_rows
