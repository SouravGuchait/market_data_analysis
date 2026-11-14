"""Read staged CSV chunks and produce validated DataFrame splits."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from apps.ingestion.models import DataType

from .column_mapping import apply_column_map, normalize_columns, resolve_column_map
from .normalization import (
    normalize_empty_values,
    to_date_columns,
    to_datetime_columns,
    to_numeric_columns,
)
from .validation import ValidatorSpec, apply_validators, load_validators


@dataclass(frozen=True)
class ProcessedChunk:
    """One staged chunk after normalization and validation."""

    chunk_path: Path
    valid_df: pd.DataFrame
    rejected_df: pd.DataFrame
    duplicate_df: pd.DataFrame


def _numeric_fields(data_type: str) -> tuple[str, ...]:
    if data_type == DataType.TICK_DATA:
        return ("price", "quantity", "sequence_number")
    return ("open", "high", "low", "close", "adjusted_close", "volume")


def _datetime_fields(data_type: str) -> tuple[str, ...]:
    if data_type == DataType.TICK_DATA:
        return ("event_timestamp",)
    return ("timestamp",)


def _date_fields(data_type: str) -> tuple[str, ...]:
    if data_type == DataType.DAILY_OHLCV:
        return ("trading_date",)
    return ()


def normalize_dataframe(
    frame: pd.DataFrame,
    data_type: str,
    schema_mapping: Mapping[str, object] | None,
) -> pd.DataFrame:
    named = normalize_columns(frame)
    column_map = resolve_column_map(data_type, schema_mapping)
    mapped = apply_column_map(named, data_type, column_map)
    cleaned = normalize_empty_values(mapped)
    numbered = to_numeric_columns(cleaned, _numeric_fields(data_type))
    stamped = to_datetime_columns(numbered, _datetime_fields(data_type))
    return to_date_columns(stamped, _date_fields(data_type))


def process_staged_chunk(
    chunk_path: Path,
    *,
    data_type: str,
    schema_mapping: Mapping[str, object] | None,
    validators: Sequence[ValidatorSpec] | None = None,
    chunk_size: int = 5000,
) -> ProcessedChunk:
    """Read one staged CSV and split into valid, rejected and duplicate frames."""

    reader = pd.read_csv(chunk_path, chunksize=chunk_size, dtype=str, keep_default_na=False)
    frames = list(reader)
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    normalized = normalize_dataframe(frame, data_type, schema_mapping)
    specs = list(validators) if validators is not None else load_validators(data_type, None)
    valid_df, rejected_df, duplicate_df = apply_validators(normalized, data_type, specs)
    return ProcessedChunk(
        chunk_path=chunk_path,
        valid_df=valid_df,
        rejected_df=rejected_df,
        duplicate_df=duplicate_df,
    )
