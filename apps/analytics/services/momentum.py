"""Assemble RSI and Stochastic series from persisted trade-derived candles.

Indicators are not stored. Each request reads current ``Candle`` rows so a late
tick that updates a candle changes the next response. Formulas stay in
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
    MAX_INDICATOR_CANDLES,
    RSI_INITIALIZATION,
    STOCHASTIC_INITIALIZATION,
    DataAvailability,
    EventRange,
    ExchangeRef,
    IndicatorObservation,
    InstrumentRef,
    RsiSeries,
    StochasticObservation,
    StochasticSeries,
    StochasticValue,
    rsi_lookback,
    rsi_lookback_text,
    rsi_series,
    stochastic_lookback,
    stochastic_lookback_text,
    stochastic_series,
)
from apps.analytics.services.trend import IndicatorLimitError
from apps.instruments.models import Instrument
from apps.market_data.models import Candle

SOURCE = "trade_derived_candle"
INCOMPLETE_EXCLUDED = (
    "Incomplete candles are excluded. Set include_incomplete=true to include them."
)
INCOMPLETE_INCLUDED = "Incomplete candles are included and can change until the bucket closes."


@dataclass(frozen=True)
class RsiIndicatorQuery:
    candles: IndicatorCandleQuery
    window: int


@dataclass(frozen=True)
class StochasticIndicatorQuery:
    candles: IndicatorCandleQuery
    window: int
    signal_period: int


def build_rsi_series(query: RsiIndicatorQuery) -> list[RsiSeries] | IndicatorLimitError:
    loaded = _load_candles(query.candles)
    if isinstance(loaded, IndicatorLimitError):
        return loaded
    series = [_rsi_series(rows, query) for rows in _group(loaded).values()]
    series.sort(key=lambda item: item["instrument"]["symbol"])
    return series


def build_stochastic_series(
    query: StochasticIndicatorQuery,
) -> list[StochasticSeries] | IndicatorLimitError:
    loaded = _load_candles(query.candles)
    if isinstance(loaded, IndicatorLimitError):
        return loaded
    series = [_stochastic_series(rows, query) for rows in _group(loaded).values()]
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


def _ohlc_numbers(
    rows: Sequence[Candle],
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    highs: list[float | None] = []
    lows: list[float | None] = []
    closes: list[float | None] = []
    for row in rows:
        high = float(row.high)
        low = float(row.low)
        close = float(row.close)
        highs.append(high if math.isfinite(high) else None)
        lows.append(low if math.isfinite(low) else None)
        closes.append(close if math.isfinite(close) and close > 0 else None)
    return highs, lows, closes


def _rsi_series(rows: list[Candle], query: RsiIndicatorQuery) -> RsiSeries:
    combined, prior_count = _with_prior(rows, query.candles, rsi_lookback(query.window))
    values = rsi_series(_close_numbers(combined), query.window)
    return _rsi_payload(rows[0], query, _value_points(rows, values[prior_count:]))


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


def _rsi_payload(
    row: Candle, query: RsiIndicatorQuery, points: list[IndicatorObservation]
) -> RsiSeries:
    instrument = row.instrument
    return {
        "metric": "rsi",
        "timeframe": query.candles.timeframe,
        "source": SOURCE,
        "instrument": _instrument_ref(instrument),
        "exchange": _exchange_ref(instrument),
        "parameters": {
            "window": query.window,
            "include_incomplete": query.candles.include_incomplete,
        },
        "requested_range": _event_range(query.candles),
        "lookback_requirement": rsi_lookback_text(query.window),
        "incomplete_policy": _incomplete_policy(query.candles.include_incomplete),
        "initialization": RSI_INITIALIZATION,
        "input_field": "close",
        "output_unit": "oscillator",
        "data_availability": _availability(points),
        "results": points,
    }


def _stochastic_series(rows: list[Candle], query: StochasticIndicatorQuery) -> StochasticSeries:
    limit = stochastic_lookback(query.window, query.signal_period)
    combined, prior_count = _with_prior(rows, query.candles, limit)
    highs, lows, closes = _ohlc_numbers(combined)
    values = stochastic_series(
        highs,
        lows,
        closes,
        query.window,
        signal_period=query.signal_period,
    )
    return _stochastic_payload(rows[0], query, _stoch_points(rows, values[prior_count:]))


def _stoch_points(
    rows: Sequence[Candle], values: Sequence[StochasticValue]
) -> list[StochasticObservation]:
    return [
        {
            "timestamp": _timestamp_text(row.bucket_start),
            "close": format(row.close, "f"),
            "percent_k": value.percent_k,
            "percent_d": value.percent_d,
        }
        for row, value in zip(rows, values, strict=True)
    ]


def _stochastic_payload(
    row: Candle,
    query: StochasticIndicatorQuery,
    points: list[StochasticObservation],
) -> StochasticSeries:
    instrument = row.instrument
    return {
        "metric": "stochastic",
        "timeframe": query.candles.timeframe,
        "source": SOURCE,
        "instrument": _instrument_ref(instrument),
        "exchange": _exchange_ref(instrument),
        "parameters": {
            "window": query.window,
            "signal_period": query.signal_period,
            "include_incomplete": query.candles.include_incomplete,
        },
        "requested_range": _event_range(query.candles),
        "lookback_requirement": stochastic_lookback_text(query.window, query.signal_period),
        "incomplete_policy": _incomplete_policy(query.candles.include_incomplete),
        "initialization": STOCHASTIC_INITIALIZATION,
        "input_field": "high,low,close",
        "output_unit": "oscillator",
        "data_availability": {
            "observation_count": len(points),
            "null_count": sum(1 for point in points if point["percent_d"] is None),
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
