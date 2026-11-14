"""Persist rejected DataFrame rows and conversion rejections."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd
from django.conf import settings

from apps.ingestion.converters.types import SOURCE_ROW_COLUMN, ConversionRejection
from apps.ingestion.models import Ingestion, IngestionBatch, RejectedRow


def _raw_limit() -> int:
    return max(64, int(settings.INGESTION_REJECTED_RAW_MAX_BYTES))


def _truncate_mapping(payload: Mapping[str, object]) -> dict[str, object]:
    encoded = json.dumps(dict(payload), default=str, separators=(",", ":"))
    limit = _raw_limit()
    if len(encoded.encode("utf-8")) <= limit:
        return {str(key): value for key, value in payload.items()}
    clipped = encoded.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
    return {"_truncated": clipped}


def _cell_value(value: object) -> object:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) else value
    return str(value)


def _row_mapping(frame: pd.DataFrame, index: Any) -> dict[str, object]:
    payload: dict[str, object] = {}
    for column in frame.columns:
        if column == SOURCE_ROW_COLUMN:
            continue
        payload[str(column)] = _cell_value(frame.at[index, column])
    return _truncate_mapping(payload)


def rejected_rows_from_frame(
    frame: pd.DataFrame,
    *,
    ingestion: Ingestion,
    batch: IngestionBatch | None,
) -> list[RejectedRow]:
    if frame.empty:
        return []
    rows: list[RejectedRow] = []
    for index in frame.index:
        source_row = int(frame.at[index, SOURCE_ROW_COLUMN])
        reason = (
            str(frame.at[index, "reason_code"]) if "reason_code" in frame.columns else "REJECTED"
        )
        field_name = str(frame.at[index, "field_name"]) if "field_name" in frame.columns else ""
        message = str(frame.at[index, "message"]) if "message" in frame.columns else reason
        rows.append(
            RejectedRow(
                ingestion=ingestion,
                batch=batch,
                source_row_number=source_row,
                reason_code=reason[:64],
                field_name=field_name[:64],
                error_message=message[:512],
                raw_record=_row_mapping(frame, index),
            )
        )
    return rows


def rejected_rows_from_conversion(
    items: Sequence[ConversionRejection],
    *,
    ingestion: Ingestion,
    batch: IngestionBatch | None,
) -> list[RejectedRow]:
    return [
        RejectedRow(
            ingestion=ingestion,
            batch=batch,
            source_row_number=item.source_row,
            reason_code=item.reason_code[:64],
            field_name=item.field_name[:64],
            error_message=item.message[:512],
            raw_record={"stage": item.stage},
        )
        for item in items
    ]


def bulk_insert_rejections(rows: Sequence[RejectedRow]) -> int:
    if not rows:
        return 0
    RejectedRow.objects.bulk_create(list(rows), batch_size=500)
    return len(rows)
