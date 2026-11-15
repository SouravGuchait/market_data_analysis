"""Canonical analytical metric helpers for Feature 008 and later metrics.

Trend indicators (Feature 009) use trade-derived candle closes. They are computed
on read from the closes passed in, so a corrected candle is reflected on the next
request. Invalid closes (non-finite or not positive) are omitted from the window
and do not advance EMA or MACD state. There is no look-ahead: index ``t`` uses
only closes at or before ``t``.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TypedDict

from apps.instruments.models import Instrument
from apps.market_data.models import DailyOHLCV


@dataclass(frozen=True)
class ReturnPoint:
    """One dated return observation."""

    trading_date: date
    close: Decimal
    simple_return: float | None
    log_return: float | None


class ReturnObservation(TypedDict):
    """One dated metric observation in an API series."""

    trading_date: str
    close: str
    value: float | None


class InstrumentRef(TypedDict):
    id: int
    symbol: str
    name: str


class ExchangeRef(TypedDict):
    id: int
    code: str
    name: str


class RequestedRange(TypedDict):
    start_date: str | None
    end_date: str | None


class DataAvailability(TypedDict):
    observation_count: int
    null_count: int


class MetricParameters(TypedDict):
    lookback_observations: int


class ReturnSeriesPayload(TypedDict):
    """One instrument daily-return series with metadata."""

    metric: str
    return_type: str
    timeframe: str
    source: str
    instrument: InstrumentRef
    exchange: ExchangeRef
    parameters: MetricParameters
    requested_range: RequestedRange
    lookback_requirement: str
    data_availability: DataAvailability
    results: list[ReturnObservation]


def close_value(row: DailyOHLCV) -> Decimal | None:
    """Prefer adjusted close when present and positive; otherwise use close."""

    if row.adjusted_close is not None and row.adjusted_close > 0:
        return row.adjusted_close
    if row.close > 0:
        return row.close
    return None


def pair_returns(previous: Decimal, current: Decimal) -> tuple[float, float]:
    """Return simple and log returns between two positive closes."""

    ratio = current / previous
    return float(ratio - 1), math.log(float(ratio))


def compute_daily_returns(rows: Sequence[DailyOHLCV]) -> list[ReturnPoint]:
    """Compute simple and log returns for an ascending DailyOHLCV series."""

    points: list[ReturnPoint] = []
    previous_close: Decimal | None = None
    for row in rows:
        close = close_value(row)
        if close is None:
            points.append(
                ReturnPoint(
                    trading_date=row.trading_date,
                    close=row.close,
                    simple_return=None,
                    log_return=None,
                )
            )
            continue
        simple: float | None = None
        log_return: float | None = None
        if previous_close is not None and previous_close > 0:
            simple, log_return = pair_returns(previous_close, close)
        points.append(
            ReturnPoint(
                trading_date=row.trading_date,
                close=close,
                simple_return=simple,
                log_return=log_return,
            )
        )
        previous_close = close
    return points


def previous_close_before(instrument_id: int, start_date: date) -> DailyOHLCV | None:
    """Fetch the nearest earlier close used as return lookback context."""

    return (
        DailyOHLCV.objects.filter(instrument_id=instrument_id, trading_date__lt=start_date)
        .order_by("-trading_date")
        .first()
    )


def _in_range(point: ReturnPoint, start_date: date | None) -> bool:
    return start_date is None or point.trading_date >= start_date


def point_payload(point: ReturnPoint, return_type: str) -> ReturnObservation:
    value = point.simple_return if return_type == "simple" else point.log_return
    return {
        "trading_date": point.trading_date.isoformat(),
        "close": str(point.close),
        "value": value,
    }


def series_for_instrument(
    rows: Sequence[DailyOHLCV],
    *,
    start_date: date | None,
    return_type: str,
) -> list[ReturnObservation]:
    """Build dated return points for one instrument, including lookback context."""

    history = list(rows)
    if start_date is not None and history:
        prior = previous_close_before(history[0].instrument.pk, start_date)
        if prior is not None and prior.trading_date < history[0].trading_date:
            history = [prior, *history]
    points = compute_daily_returns(history)
    return [point_payload(point, return_type) for point in points if _in_range(point, start_date)]


def returns_payload(
    instrument: Instrument,
    points: list[ReturnObservation],
    *,
    return_type: str,
    start_date: date | None,
    end_date: date | None,
) -> ReturnSeriesPayload:
    """Wrap one instrument return series with metric metadata."""

    return {
        "metric": "daily_return",
        "return_type": return_type,
        "timeframe": "1d",
        "source": "daily_ohlcv",
        "instrument": {
            "id": instrument.pk,
            "symbol": instrument.symbol,
            "name": instrument.name,
        },
        "exchange": {
            "id": instrument.exchange.pk,
            "code": instrument.exchange.code,
            "name": instrument.exchange.name,
        },
        "parameters": {"lookback_observations": 1},
        "requested_range": {
            "start_date": None if start_date is None else start_date.isoformat(),
            "end_date": None if end_date is None else end_date.isoformat(),
        },
        "lookback_requirement": "One earlier valid close is required for the first return.",
        "data_availability": {
            "observation_count": len(points),
            "null_count": sum(1 for point in points if point["value"] is None),
        },
        "results": points,
    }


def build_returns_series(
    rows: Sequence[DailyOHLCV],
    *,
    start_date: date | None,
    end_date: date | None,
    return_type: str,
) -> list[ReturnSeriesPayload]:
    """Group DailyOHLCV rows by instrument and compute return series payloads."""

    by_instrument: dict[int, list[DailyOHLCV]] = {}
    for row in rows:
        by_instrument.setdefault(row.instrument.pk, []).append(row)
    instruments = {
        item.pk: item
        for item in Instrument.objects.filter(pk__in=by_instrument).select_related("exchange")
    }
    series: list[ReturnSeriesPayload] = []
    for instrument_id, instrument_rows in by_instrument.items():
        points = series_for_instrument(
            instrument_rows, start_date=start_date, return_type=return_type
        )
        series.append(
            returns_payload(
                instruments[instrument_id],
                points,
                return_type=return_type,
                start_date=start_date,
                end_date=end_date,
            )
        )
    return series


MIN_INDICATOR_PERIOD = 2
MAX_INDICATOR_PERIOD = 500
DEFAULT_WINDOW = 20
MACD_DEFAULT_FAST = 12
MACD_DEFAULT_SLOW = 26
MACD_DEFAULT_SIGNAL = 9
MAX_INDICATOR_CANDLES = 10_000
MAX_INDICATOR_RANGE_DAYS = 3_660

SMA_INITIALIZATION = (
    "SMA is the arithmetic mean of the last full window of valid closes. "
    "The value is null until that many valid closes exist."
)
EMA_INITIALIZATION = (
    "EMA is seeded with the SMA of the first full window of valid closes, "
    "then EMA_t = alpha*close_t + (1-alpha)*EMA_(t-1) with alpha = 2/(n+1)."
)
MACD_INITIALIZATION = (
    "MACD is the fast EMA minus the slow EMA. The signal line is an EMA of the "
    "MACD line, seeded with the SMA of the first signal-period MACD values. "
    "Histogram is MACD minus signal and is null until the signal exists."
)


class EventRange(TypedDict):
    start: str | None
    end: str | None


class WindowParameters(TypedDict):
    window: int
    include_incomplete: bool


class MacdParameters(TypedDict):
    fast_period: int
    slow_period: int
    signal_period: int
    include_incomplete: bool


class IndicatorObservation(TypedDict):
    timestamp: str
    close: str
    value: float | None


class MacdObservation(TypedDict):
    timestamp: str
    close: str
    macd: float | None
    signal: float | None
    histogram: float | None


class IndicatorSeries(TypedDict):
    metric: str
    timeframe: str
    source: str
    instrument: InstrumentRef
    exchange: ExchangeRef
    parameters: WindowParameters
    requested_range: EventRange
    lookback_requirement: str
    incomplete_policy: str
    initialization: str
    input_field: str
    output_unit: str
    data_availability: DataAvailability
    results: list[IndicatorObservation]


class MacdSeries(TypedDict):
    metric: str
    timeframe: str
    source: str
    instrument: InstrumentRef
    exchange: ExchangeRef
    parameters: MacdParameters
    requested_range: EventRange
    lookback_requirement: str
    incomplete_policy: str
    initialization: str
    input_field: str
    output_unit: str
    data_availability: DataAvailability
    results: list[MacdObservation]


@dataclass(frozen=True)
class MacdValue:
    """One MACD observation in price units."""

    macd: float | None
    signal: float | None
    histogram: float | None


@dataclass
class _EmaState:
    window: int
    alpha: float
    require_positive: bool
    seed: list[float]
    previous: float | None


@dataclass(frozen=True)
class _EmaStep:
    value: float | None
    previous: float | None
    clear_seed: bool


def _require_period(window: int, name: str) -> None:
    if window < MIN_INDICATOR_PERIOD or window > MAX_INDICATOR_PERIOD:
        raise ValueError(
            f"{name} period must be between {MIN_INDICATOR_PERIOD} and {MAX_INDICATOR_PERIOD}."
        )


def _accepted_value(value: float | None, *, require_positive: bool) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    if require_positive and value <= 0:
        return None
    return value


def sma_series(closes: Sequence[float | None], window: int) -> list[float | None]:
    """Full-window simple moving average of valid closes.

    Input is close, already ordered by event time. Output is in price units.
    A null is returned until ``window`` valid closes have been seen. Invalid
    closes do not enter the window. No intermediate rounding is applied.
    """

    _require_period(window, "SMA")
    values: list[float | None] = []
    bucket: deque[float] = deque()
    total = 0.0
    for close in closes:
        accepted = _accepted_value(close, require_positive=True)
        if accepted is None:
            values.append(None)
            continue
        bucket.append(accepted)
        total += accepted
        if len(bucket) > window:
            total -= bucket.popleft()
        values.append(None if len(bucket) < window else total / window)
    return values


def _seed_ema(accepted: float, state: _EmaState) -> _EmaStep:
    state.seed.append(accepted)
    if len(state.seed) < state.window:
        return _EmaStep(None, None, False)
    initial = sum(state.seed) / state.window
    return _EmaStep(initial, initial, True)


def _ema_step(value: float | None, state: _EmaState) -> _EmaStep:
    accepted = _accepted_value(value, require_positive=state.require_positive)
    if accepted is None:
        return _EmaStep(None, state.previous, False)
    if state.previous is not None:
        updated = state.alpha * accepted + (1.0 - state.alpha) * state.previous
        return _EmaStep(updated, updated, False)
    return _seed_ema(accepted, state)


def ema_series(
    closes: Sequence[float | None],
    window: int,
    *,
    require_positive: bool = True,
) -> list[float | None]:
    """Exponential moving average seeded with the first full-window SMA.

    ``require_positive`` is for price series. The MACD signal passes
    ``require_positive=False`` so negative MACD values still update the EMA.
    Invalid observations do not advance state. Output uses the input unit.
    """

    _require_period(window, "EMA")
    state = _EmaState(
        window=window,
        alpha=2.0 / (window + 1),
        require_positive=require_positive,
        seed=[],
        previous=None,
    )
    values: list[float | None] = []
    for close in closes:
        step = _ema_step(close, state)
        values.append(step.value)
        state.previous = step.previous
        if step.clear_seed:
            state.seed.clear()
    return values


def _macd_value(macd: float | None, signal: float | None) -> MacdValue:
    if macd is None or signal is None:
        return MacdValue(macd, signal, None)
    return MacdValue(macd, signal, macd - signal)


def _macd_line(
    fast_ema: Sequence[float | None], slow_ema: Sequence[float | None]
) -> list[float | None]:
    line: list[float | None] = []
    for fast_value, slow_value in zip(fast_ema, slow_ema, strict=True):
        if fast_value is None or slow_value is None:
            line.append(None)
            continue
        line.append(fast_value - slow_value)
    return line


def macd_series(
    closes: Sequence[float | None],
    *,
    fast: int,
    slow: int,
    signal: int,
) -> list[MacdValue]:
    """MACD line, signal, and histogram from close prices.

    Fast, slow, and signal periods follow the documented EMA rules. The fast
    period must be shorter than the slow period. Histogram is null until the
    signal EMA is initialized. Values are price differences, not returns.
    """

    _require_period(fast, "MACD fast")
    _require_period(slow, "MACD slow")
    _require_period(signal, "MACD signal")
    if fast >= slow:
        raise ValueError("MACD fast period must be shorter than the slow period.")
    fast_ema = ema_series(closes, fast)
    slow_ema = ema_series(closes, slow)
    line = _macd_line(fast_ema, slow_ema)
    signal_line = ema_series(line, signal, require_positive=False)
    return [
        _macd_value(macd, signal_value)
        for macd, signal_value in zip(line, signal_line, strict=True)
    ]


def window_lookback(window: int) -> int:
    """Prior closes needed before the first in-range SMA or EMA can be non-null."""

    return window - 1


def macd_lookback(slow: int, signal: int) -> int:
    """Prior closes needed before the first in-range histogram can be non-null."""

    return slow + signal - 2


def window_lookback_text(window: int) -> str:
    prior = window_lookback(window)
    return (
        f"{window} valid closes are required. When start is set, up to {prior} earlier "
        "candles are calculation context and are omitted from results."
    )


def macd_lookback_text(slow: int, signal: int) -> str:
    prior = macd_lookback(slow, signal)
    return (
        f"Slow EMA {slow} and signal EMA {signal} must initialize. When start is set, "
        f"up to {prior} earlier candles are calculation context and are omitted from results."
    )
