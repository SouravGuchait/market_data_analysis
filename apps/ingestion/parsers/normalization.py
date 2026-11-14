"""Empty-value and type normalization for staged DataFrames."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

_EMPTY_TOKENS = {"", "nan", "none", "null", "na", "n/a"}


def normalize_empty_values(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in result.columns:
        series = result[column]
        if series.dtype == object:
            cleaned = series.map(_clean_cell)
            result[column] = cleaned
    return result


def _clean_cell(value: object) -> object:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return pd.NA
    text = str(value).strip()
    if text.lower() in _EMPTY_TOKENS:
        return pd.NA
    return text


def to_numeric_columns(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        if column not in result.columns:
            continue
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result


def to_datetime_columns(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        if column not in result.columns:
            continue
        result[column] = pd.to_datetime(result[column], errors="coerce", utc=True)
    return result


def to_date_columns(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        if column not in result.columns:
            continue
        parsed = pd.to_datetime(result[column], errors="coerce", utc=True)
        result[column] = parsed.dt.date
    return result
