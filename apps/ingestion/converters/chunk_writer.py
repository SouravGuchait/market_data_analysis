"""Helpers for writing bounded staging CSV chunks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pandas as pd

from .types import SOURCE_ROW_COLUMN, ChunkMeta


def chunk_filename(chunk_number: int) -> str:
    return f"chunk_{chunk_number:06d}.csv"


def chunk_path(staging_dir: Path, chunk_number: int) -> Path:
    return staging_dir / chunk_filename(chunk_number)


def attach_source_rows(frame: pd.DataFrame, start_row: int) -> pd.DataFrame:
    """Attach 1-based source row numbers for the contiguous batch."""

    if frame.empty:
        result = frame.copy()
        result[SOURCE_ROW_COLUMN] = pd.Series(dtype="int64")
        return result
    rows = list(range(start_row, start_row + len(frame)))
    result = frame.copy()
    result[SOURCE_ROW_COLUMN] = rows
    return result


def records_to_frame(
    records: Sequence[Mapping[str, object]],
    start_row: int,
) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(list(records))
    return attach_source_rows(frame, start_row)


def write_frame_chunk(
    frame: pd.DataFrame,
    staging_dir: Path,
    chunk_number: int,
) -> ChunkMeta:
    """Write one DataFrame as a staging CSV and return its metadata."""

    path = chunk_path(staging_dir, chunk_number)
    frame.to_csv(path, index=False)
    return ChunkMeta(
        chunk_number=chunk_number,
        filename=path.name,
        row_count=int(len(frame)),
    )
