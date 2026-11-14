"""Batch persistence for daily OHLCV DataFrames."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

import pandas as pd
from django.utils.dateparse import parse_date, parse_datetime

from apps.ingestion.converters.types import SOURCE_ROW_COLUMN
from apps.ingestion.instrument_lookup import load_instrument_map
from apps.instruments.models import Instrument
from apps.market_data.models import DailyOHLCV

OhlcvValues = tuple[Decimal, Decimal, Decimal, Decimal, Decimal | None, int]


@dataclass(frozen=True)
class OhlcvPersistResult:
    """Counts and conflict rows from one OHLCV batch write."""

    inserted: int
    duplicates: int
    conflicts: pd.DataFrame


@dataclass(frozen=True)
class _ParsedOhlcv:
    source_row: int
    instrument: Instrument
    trading_date: date
    timestamp: datetime
    values: OhlcvValues


def _text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _decimal(value: object) -> Decimal | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None or (isinstance(value, float) and pd.isna(value)):
        parsed = None
    else:
        parsed = parse_date(str(value).strip()[:10])
    return parsed


def _as_timestamp(value: object) -> datetime | None:
    stamp = value if isinstance(value, datetime) else None
    if stamp is None and value is not None and not (isinstance(value, float) and pd.isna(value)):
        stamp = parse_datetime(str(value).strip())
    if stamp is None:
        return None
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def _volume(value: object) -> int | None:
    number = _decimal(value)
    if number is None or number < 0 or number != number.to_integral_value():
        return None
    return int(number)


def _ohlc_tuple(row: pd.Series) -> OhlcvValues | None:
    open_price = _decimal(row.get("open"))
    high = _decimal(row.get("high"))
    low = _decimal(row.get("low"))
    close = _decimal(row.get("close"))
    volume = _volume(row.get("volume"))
    if open_price is None or high is None or low is None or close is None or volume is None:
        return None
    return open_price, high, low, close, _decimal(row.get("adjusted_close")), volume


def _conflict_frame(rows: Sequence[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        empty: dict[str, list[object]] = {
            SOURCE_ROW_COLUMN: [],
            "reason_code": [],
            "field_name": [],
            "message": [],
            "stage": [],
        }
        return pd.DataFrame(empty)
    return pd.DataFrame(list(rows))


def _conflict_row(source_row: int, code: str, message: str, field: str = "") -> dict[str, object]:
    return {
        SOURCE_ROW_COLUMN: source_row,
        "reason_code": code,
        "field_name": field,
        "message": message,
        "stage": "PERSISTENCE",
    }


def _parse_rows(frame: pd.DataFrame) -> tuple[list[_ParsedOhlcv], list[dict[str, object]]]:
    pairs = [
        (_text(row.get("exchange_identifier")), _text(row.get("instrument_identifier")))
        for _, row in frame.iterrows()
    ]
    instruments = load_instrument_map(pairs)
    parsed: list[_ParsedOhlcv] = []
    conflicts: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        source_row = int(row[SOURCE_ROW_COLUMN])
        key = (
            _text(row.get("exchange_identifier")).lower(),
            _text(row.get("instrument_identifier")).lower(),
        )
        instrument = instruments.get(key)
        trading_date = _as_date(row.get("trading_date"))
        timestamp = _as_timestamp(row.get("timestamp"))
        values = _ohlc_tuple(row)
        if instrument is None or trading_date is None or timestamp is None or values is None:
            conflicts.append(
                _conflict_row(source_row, "PERSIST_MAPPING", "Unable to map OHLCV row.")
            )
            continue
        parsed.append(_ParsedOhlcv(source_row, instrument, trading_date, timestamp, values))
    return parsed, conflicts


def _existing_map(parsed: Sequence[_ParsedOhlcv]) -> dict[tuple[int, date], DailyOHLCV]:
    if not parsed:
        return {}
    rows = DailyOHLCV.objects.filter(
        instrument_id__in=[item.instrument.pk for item in parsed],
        trading_date__in=[item.trading_date for item in parsed],
    ).select_related("instrument")
    return {(row.instrument.pk, row.trading_date): row for row in rows}


def _classify(
    parsed: Sequence[_ParsedOhlcv],
    existing: dict[tuple[int, date], DailyOHLCV],
) -> tuple[list[DailyOHLCV], int, list[dict[str, object]]]:
    to_create: list[DailyOHLCV] = []
    duplicates = 0
    conflicts: list[dict[str, object]] = []
    for item in parsed:
        current = existing.get((item.instrument.pk, item.trading_date))
        if current is None:
            open_p, high, low, close, adjusted, volume = item.values
            candle = DailyOHLCV(
                instrument=item.instrument,
                trading_date=item.trading_date,
                timestamp=item.timestamp,
                open=open_p,
                high=high,
                low=low,
                close=close,
                adjusted_close=adjusted,
                volume=volume,
            )
            to_create.append(candle)
            existing[(item.instrument.pk, item.trading_date)] = candle
            continue
        current_values = (
            current.open,
            current.high,
            current.low,
            current.close,
            current.adjusted_close,
            current.volume,
        )
        if current_values == item.values:
            duplicates += 1
            continue
        conflicts.append(
            _conflict_row(
                item.source_row,
                "OHLCV_CONFLICT",
                "Existing daily candle values conflict with the source row.",
                "trading_date",
            )
        )
    return to_create, duplicates, conflicts


def persist_ohlcv_dataframe(frame: pd.DataFrame) -> OhlcvPersistResult:
    """Insert new daily candles; identical rows duplicate; conflicts rejected."""

    if frame.empty:
        return OhlcvPersistResult(0, 0, _conflict_frame([]))
    parsed, mapping_conflicts = _parse_rows(frame)
    to_create, duplicates, value_conflicts = _classify(parsed, _existing_map(parsed))
    if to_create:
        DailyOHLCV.objects.bulk_create(to_create, batch_size=500)
    return OhlcvPersistResult(
        len(to_create),
        duplicates,
        _conflict_frame([*mapping_conflicts, *value_conflicts]),
    )
