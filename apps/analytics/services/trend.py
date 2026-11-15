"""Assemble SMA, EMA, and MACD series from persisted trade-derived candles.

Indicators are not stored. Each request reads the current ``Candle`` rows, so a
late tick that updates a candle changes the next response. Calculations stay in
``metrics_calculator``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from apps.analytics.selectors import (
    IndicatorCandleQuery,
    indicator_lookback_candles,
    list_indicator_candles,
)
from apps.analytics.services.metrics_calculator import (
    EMA_INITIALIZATION,
    MACD_INITIALIZATION,
    MAX_INDICATOR_CANDLES,
    SMA_INITIALIZATION,
    DataAvailability,
    EventRange,
    ExchangeRef,
    IndicatorObservation,
    IndicatorSeries,
    InstrumentRef,
    MacdObservation,
    MacdSeries,
    MacdValue,
    ema_series,
    macd_lookback,
    macd_lookback_text,
    macd_series,
    sma_series,
    window_lookback,
    window_lookback_text,
)
from apps.instruments.models import Instrument
from apps.market_data.models import Candle

SOURCE = "trade_derived_candle"
INCOMPLETE_EXCLUDED = (
    "Incomplete candles are excluded. Set include_incomplete=true to include them."
)
INCOMPLETE_INCLUDED = "Incomplete candles are included and can change until the bucket closes."


@dataclass(frozen=True)
class WindowIndicatorQuery:
    candles: IndicatorCandleQuery
    window: int


@dataclass(frozen=True)
class MacdIndicatorQuery:
    candles: IndicatorCandleQuery
    fast_period: int
    slow_period: int
    signal_period: int


@dataclass(frozen=True)
class IndicatorLimitError:
    message: str
    errors: dict[str, list[str]]


@dataclass(frozen=True)
class _WindowMeta:
    metric: str
    initialization: str
    lookback_requirement: str


def build_sma_series(query: WindowIndicatorQuery) -> list[IndicatorSeries] | IndicatorLimitError:
    meta = _WindowMeta("sma", SMA_INITIALIZATION, window_lookback_text(query.window))
    return _build_window_series(query, meta)


def build_ema_series(query: WindowIndicatorQuery) -> list[IndicatorSeries] | IndicatorLimitError:
    meta = _WindowMeta("ema", EMA_INITIALIZATION, window_lookback_text(query.window))
    return _build_window_series(query, meta)


def _build_window_series(
    query: WindowIndicatorQuery,
    meta: _WindowMeta,
) -> list[IndicatorSeries] | IndicatorLimitError:
    loaded = _load_candles(query.candles)
    if isinstance(loaded, IndicatorLimitError):
        return loaded
    series = [_window_series(rows, query, meta) for rows in _group(loaded).values()]
    series.sort(key=lambda item: item["instrument"]["symbol"])
    return series


def build_macd_series(query: MacdIndicatorQuery) -> list[MacdSeries] | IndicatorLimitError:
    loaded = _load_candles(query.candles)
    if isinstance(loaded, IndicatorLimitError):
        return loaded
    series = [_macd_series(rows, query) for rows in _group(loaded).values()]
    series.sort(key=lambda item: item["instrument"]["symbol"])
    return series


def _load_candles(query: IndicatorCandleQuery) -> list[Candle] | IndicatorLimitError:
    if not query.instrument_ids:
        return []
    rows = list(list_indicator_candles(query)[: MAX_INDICATOR_CANDLES + 1])
    if len(rows) > MAX_INDICATOR_CANDLES:
        return IndicatorLimitError(
            "Indicator range exceeds the observation limit",
            {"detail": [f"Narrow the range to at most {MAX_INDICATOR_CANDLES} candles."]},
        )
    return rows


def _group(rows: Sequence[Candle]) -> dict[int, list[Candle]]:
    grouped: dict[int, list[Candle]] = {}
    for row in rows:
        grouped.setdefault(row.instrument.pk, []).append(row)
    return grouped


def _with_prior(
    rows: list[Candle], query: IndicatorCandleQuery, limit: int
) -> tuple[list[Candle], int]:
    if query.range_start is None or limit <= 0:
        return rows, 0
    prior = indicator_lookback_candles(query, rows[0].instrument.pk, limit)
    return [*prior, *rows], len(prior)


def _close_numbers(rows: Sequence[Candle]) -> list[float | None]:
    numbers: list[float | None] = []
    for row in rows:
        number = float(row.close)
        numbers.append(number if math.isfinite(number) and number > 0 else None)
    return numbers


def _window_values(
    closes: Sequence[float | None], query: WindowIndicatorQuery, metric: str
) -> list[float | None]:
    if metric == "sma":
        return sma_series(closes, query.window)
    return ema_series(closes, query.window)


def _window_series(
    rows: list[Candle], query: WindowIndicatorQuery, meta: _WindowMeta
) -> IndicatorSeries:
    combined, prior_count = _with_prior(rows, query.candles, window_lookback(query.window))
    values = _window_values(_close_numbers(combined), query, meta.metric)
    return _window_payload(rows[0], query, meta, _value_points(rows, values[prior_count:]))


def _value_points(
    rows: Sequence[Candle], values: Sequence[float | None]
) -> list[IndicatorObservation]:
    return [
        {
            "timestamp": _timestamp_text(row.bucket_start),
            "close": format(row.close, "f"),
            "value": value,
        }
        for row, value in zip(rows, values, strict=True)
    ]


def _window_payload(
    row: Candle,
    query: WindowIndicatorQuery,
    meta: _WindowMeta,
    points: list[IndicatorObservation],
) -> IndicatorSeries:
    instrument = row.instrument
    return {
        "metric": meta.metric,
        "timeframe": query.candles.timeframe,
        "source": SOURCE,
        "instrument": _instrument_ref(instrument),
        "exchange": _exchange_ref(instrument),
        "parameters": {
            "window": query.window,
            "include_incomplete": query.candles.include_incomplete,
        },
        "requested_range": _event_range(query.candles),
        "lookback_requirement": meta.lookback_requirement,
        "incomplete_policy": _incomplete_policy(query.candles.include_incomplete),
        "initialization": meta.initialization,
        "input_field": "close",
        "output_unit": "price",
        "data_availability": _availability(points),
        "results": points,
    }


def _macd_series(rows: list[Candle], query: MacdIndicatorQuery) -> MacdSeries:
    limit = macd_lookback(query.slow_period, query.signal_period)
    combined, prior_count = _with_prior(rows, query.candles, limit)
    values = macd_series(
        _close_numbers(combined),
        fast=query.fast_period,
        slow=query.slow_period,
        signal=query.signal_period,
    )
    points = _macd_points(rows, values[prior_count:])
    return _macd_payload(rows[0], query, points)


def _macd_points(rows: Sequence[Candle], values: Sequence[MacdValue]) -> list[MacdObservation]:
    return [
        {
            "timestamp": _timestamp_text(row.bucket_start),
            "close": format(row.close, "f"),
            "macd": value.macd,
            "signal": value.signal,
            "histogram": value.histogram,
        }
        for row, value in zip(rows, values, strict=True)
    ]


def _macd_payload(
    row: Candle, query: MacdIndicatorQuery, points: list[MacdObservation]
) -> MacdSeries:
    instrument = row.instrument
    return {
        "metric": "macd",
        "timeframe": query.candles.timeframe,
        "source": SOURCE,
        "instrument": _instrument_ref(instrument),
        "exchange": _exchange_ref(instrument),
        "parameters": {
            "fast_period": query.fast_period,
            "slow_period": query.slow_period,
            "signal_period": query.signal_period,
            "include_incomplete": query.candles.include_incomplete,
        },
        "requested_range": _event_range(query.candles),
        "lookback_requirement": macd_lookback_text(query.slow_period, query.signal_period),
        "incomplete_policy": _incomplete_policy(query.candles.include_incomplete),
        "initialization": MACD_INITIALIZATION,
        "input_field": "close",
        "output_unit": "price_difference",
        "data_availability": {
            "observation_count": len(points),
            "null_count": sum(1 for point in points if point["histogram"] is None),
        },
        "results": points,
    }


def _instrument_ref(instrument: Instrument) -> InstrumentRef:
    return {"id": instrument.pk, "symbol": instrument.symbol, "name": instrument.name}


def _exchange_ref(instrument: Instrument) -> ExchangeRef:
    exchange = instrument.exchange
    return {"id": exchange.pk, "code": exchange.code, "name": exchange.name}


def _event_range(query: IndicatorCandleQuery) -> EventRange:
    return {
        "start": _optional_timestamp(query.range_start),
        "end": _optional_timestamp(query.range_end),
    }


def _optional_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _timestamp_text(value)


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _incomplete_policy(include_incomplete: bool) -> str:
    if include_incomplete:
        return INCOMPLETE_INCLUDED
    return INCOMPLETE_EXCLUDED


def _availability(points: Sequence[IndicatorObservation]) -> DataAvailability:
    return {
        "observation_count": len(points),
        "null_count": sum(1 for point in points if point["value"] is None),
    }
