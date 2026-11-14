"""Normalize column names and apply source-to-canonical mapping."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from apps.ingestion.converters.types import SOURCE_ROW_COLUMN

from .profiles import canonical_columns, default_column_map


def normalize_column_name(name: object) -> str:
    text = str(name).strip().lower().replace(" ", "_")
    return text.replace("-", "_")


def normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    renamed = {column: normalize_column_name(column) for column in frame.columns}
    return frame.rename(columns=renamed)


def resolve_column_map(
    data_type: str,
    schema_mapping: Mapping[str, object] | None,
) -> dict[str, str]:
    mapping = default_column_map(data_type)
    if not schema_mapping:
        return mapping
    for source, target in schema_mapping.items():
        mapping[normalize_column_name(source)] = str(target).strip()
    return mapping


def apply_column_map(
    frame: pd.DataFrame,
    data_type: str,
    column_map: Mapping[str, str],
) -> pd.DataFrame:
    rename = {
        column: column_map[column]
        for column in frame.columns
        if column in column_map and column != SOURCE_ROW_COLUMN
    }
    mapped = frame.rename(columns=rename)
    keep = [SOURCE_ROW_COLUMN, *canonical_columns(data_type)]
    present = [column for column in keep if column in mapped.columns]
    return mapped.loc[:, present].copy()
