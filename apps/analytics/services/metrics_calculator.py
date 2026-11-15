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


DEFAULT_ATR_WINDOW = 14
DEFAULT_BOLLINGER_MULTIPLIER = 2.0
MIN_BOLLINGER_MULTIPLIER = 0.1
MAX_BOLLINGER_MULTIPLIER = 5.0

BOLLINGER_INITIALIZATION = (
    "Middle is the SMA of the last full window of valid closes. Upper and lower "
    "bands use population standard deviation (ddof=0) times std_multiplier. "
    "Width is (upper-lower)/middle when middle is non-zero; otherwise null."
)
ATR_INITIALIZATION = (
    "True range is max(high-low, |high-prev_close|, |low-prev_close|). The first "
    "bar without a previous close uses high-low only. ATR seeds as the mean of the "
    "first window true ranges, then Wilder-smooths: "
    "((window-1)*ATR_prev + TR) / window."
)


@dataclass(frozen=True)
class BollingerValue:
    """One Bollinger Band observation in price units."""

    middle: float | None
    upper: float | None
    lower: float | None
    width: float | None


class BollingerParameters(TypedDict):
    window: int
    std_multiplier: float
    include_incomplete: bool


class BollingerObservation(TypedDict):
    timestamp: str
    close: str
    middle: float | None
    upper: float | None
    lower: float | None
    width: float | None


class BollingerSeries(TypedDict):
    metric: str
    timeframe: str
    source: str
    instrument: InstrumentRef
    exchange: ExchangeRef
    parameters: BollingerParameters
    requested_range: EventRange
    lookback_requirement: str
    incomplete_policy: str
    initialization: str
    input_field: str
    output_unit: str
    data_availability: DataAvailability
    results: list[BollingerObservation]


class AtrSeries(TypedDict):
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


def _require_multiplier(std_multiplier: float) -> None:
    if not math.isfinite(std_multiplier):
        raise ValueError("std_multiplier must be a finite number.")
    if std_multiplier < MIN_BOLLINGER_MULTIPLIER or std_multiplier > MAX_BOLLINGER_MULTIPLIER:
        raise ValueError(
            "std_multiplier must be between "
            f"{MIN_BOLLINGER_MULTIPLIER} and {MAX_BOLLINGER_MULTIPLIER}."
        )


def _population_std(values: Sequence[float]) -> float:
    mean = sum(values) / len(values)
    return math.sqrt(sum((item - mean) ** 2 for item in values) / len(values))


def band_width(middle: float, upper: float, lower: float) -> float | None:
    """Return (upper-lower)/middle, or null when middle is zero."""

    if middle == 0:
        return None
    return (upper - lower) / middle


def _bollinger_point(bucket: Sequence[float], std_multiplier: float) -> BollingerValue:
    middle = sum(bucket) / len(bucket)
    spread = std_multiplier * _population_std(bucket)
    upper = middle + spread
    lower = middle - spread
    return BollingerValue(middle, upper, lower, band_width(middle, upper, lower))


def bollinger_series(
    closes: Sequence[float | None],
    window: int,
    *,
    std_multiplier: float,
) -> list[BollingerValue]:
    """Bollinger Bands from closes using population standard deviation.

    Invalid or non-positive closes do not enter the window. Warm-up values are
    null until ``window`` valid closes exist. Output is in price units; width is
    dimensionless when middle is non-zero.
    """

    _require_period(window, "Bollinger")
    _require_multiplier(std_multiplier)
    values: list[BollingerValue] = []
    bucket: deque[float] = deque()
    empty = BollingerValue(None, None, None, None)
    for close in closes:
        accepted = _accepted_value(close, require_positive=True)
        if accepted is None:
            values.append(empty)
            continue
        bucket.append(accepted)
        if len(bucket) > window:
            bucket.popleft()
        if len(bucket) < window:
            values.append(empty)
            continue
        values.append(_bollinger_point(bucket, std_multiplier))
    return values


def _ohlc_triple(
    high: float | None, low: float | None, close: float | None
) -> tuple[float, float, float] | None:
    if high is None or low is None or close is None:
        return None
    values = (high, low, close)
    invalid = not all(math.isfinite(item) for item in values) or high < low or close <= 0
    if invalid:
        return None
    return high, low, close


def true_range_at(
    high: float,
    low: float,
    close: float,
    previous_close: float | None,
) -> float:
    """True range for one bar. Without a previous close, use high-low only."""

    span = high - low
    if previous_close is None:
        return span
    return max(span, abs(high - previous_close), abs(low - previous_close))


def true_range_series(
    highs: Sequence[float | None],
    lows: Sequence[float | None],
    closes: Sequence[float | None],
) -> list[float | None]:
    """Event-time true ranges. Invalid OHLC bars yield null and do not update state."""

    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows, and closes must have the same length.")
    values: list[float | None] = []
    previous_close: float | None = None
    for high, low, close in zip(highs, lows, closes, strict=True):
        triple = _ohlc_triple(high, low, close)
        if triple is None:
            values.append(None)
            continue
        high_v, low_v, close_v = triple
        values.append(true_range_at(high_v, low_v, close_v, previous_close))
        previous_close = close_v
    return values


@dataclass
class _AtrState:
    window: int
    seed: list[float]
    previous: float | None


def _atr_seed_step(true_range: float, state: _AtrState) -> tuple[float | None, float | None]:
    state.seed.append(true_range)
    if len(state.seed) < state.window:
        return None, None
    initial = sum(state.seed) / state.window
    state.seed.clear()
    return initial, initial


def _atr_step(true_range: float | None, state: _AtrState) -> tuple[float | None, float | None]:
    if true_range is None or not math.isfinite(true_range) or true_range < 0:
        return None, state.previous
    if state.previous is not None:
        updated = ((state.window - 1) * state.previous + true_range) / state.window
        return updated, updated
    return _atr_seed_step(true_range, state)


def atr_series(
    highs: Sequence[float | None],
    lows: Sequence[float | None],
    closes: Sequence[float | None],
    window: int,
) -> list[float | None]:
    """Average True Range in price units with Wilder smoothing.

    Invalid bars do not advance ATR state. The first ATR is the mean of the first
    ``window`` valid true ranges.
    """

    _require_period(window, "ATR")
    state = _AtrState(window=window, seed=[], previous=None)
    values: list[float | None] = []
    for true_range in true_range_series(highs, lows, closes):
        value, state.previous = _atr_step(true_range, state)
        values.append(value)
    return values


def atr_lookback_text(window: int) -> str:
    prior = window_lookback(window)
    return (
        f"{window} valid true-range observations are required. When start is set, up to "
        f"{prior} earlier candles are calculation context and are omitted from results."
    )


DEFAULT_RSI_WINDOW = 14
DEFAULT_STOCHASTIC_WINDOW = 14
DEFAULT_STOCHASTIC_SIGNAL = 3

RSI_INITIALIZATION = (
    "RSI uses close-to-close changes. Average gain and loss seed as the mean of the "
    "first window changes, then Wilder-smooth. RSI is 100 when average loss is zero "
    "and average gain is positive, 0 when average gain is zero and average loss is "
    "positive, and 50 when both are zero."
)
STOCHASTIC_INITIALIZATION = (
    "%K is 100 * (close - lowest low) / (highest high - lowest low) over the lookback "
    "window. %D is the SMA of %K over signal_period. A zero high-low range yields null."
)


@dataclass(frozen=True)
class StochasticValue:
    """One Stochastic observation. Values are oscillator units (0-100)."""

    percent_k: float | None
    percent_d: float | None


class StochasticParameters(TypedDict):
    window: int
    signal_period: int
    include_incomplete: bool


class StochasticObservation(TypedDict):
    timestamp: str
    close: str
    percent_k: float | None
    percent_d: float | None


class StochasticSeries(TypedDict):
    metric: str
    timeframe: str
    source: str
    instrument: InstrumentRef
    exchange: ExchangeRef
    parameters: StochasticParameters
    requested_range: EventRange
    lookback_requirement: str
    incomplete_policy: str
    initialization: str
    input_field: str
    output_unit: str
    data_availability: DataAvailability
    results: list[StochasticObservation]


class RsiSeries(TypedDict):
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


@dataclass
class _RsiState:
    window: int
    gains: list[float]
    losses: list[float]
    avg_gain: float | None
    avg_loss: float | None
    previous_close: float | None


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    if avg_gain == 0:
        return 0.0
    return 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))


def _rsi_seed(state: _RsiState, gain: float, loss: float) -> float | None:
    state.gains.append(gain)
    state.losses.append(loss)
    if len(state.gains) < state.window:
        return None
    avg_gain = sum(state.gains) / state.window
    avg_loss = sum(state.losses) / state.window
    state.avg_gain = avg_gain
    state.avg_loss = avg_loss
    state.gains.clear()
    state.losses.clear()
    return _rsi_from_averages(avg_gain, avg_loss)


def _rsi_wilder(state: _RsiState, gain: float, loss: float) -> float:
    avg_gain = state.avg_gain
    avg_loss = state.avg_loss
    if avg_gain is None or avg_loss is None:
        raise ValueError("RSI Wilder step requires seeded averages.")
    state.avg_gain = ((state.window - 1) * avg_gain + gain) / state.window
    state.avg_loss = ((state.window - 1) * avg_loss + loss) / state.window
    return _rsi_from_averages(state.avg_gain, state.avg_loss)


def _rsi_step(close: float | None, state: _RsiState) -> float | None:
    accepted = _accepted_value(close, require_positive=True)
    if accepted is None or state.previous_close is None:
        if accepted is not None:
            state.previous_close = accepted
        return None
    change = accepted - state.previous_close
    state.previous_close = accepted
    gain = max(change, 0.0)
    loss = max(-change, 0.0)
    if state.avg_gain is None or state.avg_loss is None:
        return _rsi_seed(state, gain, loss)
    return _rsi_wilder(state, gain, loss)


def rsi_series(closes: Sequence[float | None], window: int) -> list[float | None]:
    """Relative Strength Index from closes with Wilder-smoothed averages.

    Invalid closes do not advance state. The first RSI requires ``window`` valid
    close-to-close changes (``window + 1`` valid closes). Output is oscillator
    units from 0 to 100.
    """

    _require_period(window, "RSI")
    state = _RsiState(
        window=window,
        gains=[],
        losses=[],
        avg_gain=None,
        avg_loss=None,
        previous_close=None,
    )
    return [_rsi_step(close, state) for close in closes]


def rsi_lookback(window: int) -> int:
    """Prior closes needed before the first in-range RSI can be non-null."""

    return window


def rsi_lookback_text(window: int) -> str:
    prior = rsi_lookback(window)
    return (
        f"{window} valid close-to-close changes are required ({window + 1} closes). "
        f"When start is set, up to {prior} earlier candles are calculation context "
        "and are omitted from results."
    )


@dataclass
class _StochasticBar:
    high: float
    low: float
    close: float


def _stochastic_k(bucket: Sequence[_StochasticBar]) -> float | None:
    highest = max(item.high for item in bucket)
    lowest = min(item.low for item in bucket)
    span = highest - lowest
    if span == 0:
        return None
    return 100.0 * (bucket[-1].close - lowest) / span


def _stoch_bar(high: float | None, low: float | None, close: float | None) -> _StochasticBar | None:
    triple = _ohlc_triple(high, low, close)
    if triple is None:
        return None
    high_v, low_v, close_v = triple
    return _StochasticBar(high_v, low_v, close_v)


@dataclass
class _StochasticState:
    window: int
    signal_period: int
    bucket: deque[_StochasticBar]
    k_window: deque[float]


def _percent_d(state: _StochasticState, percent_k: float | None) -> float | None:
    if percent_k is None:
        return None
    state.k_window.append(percent_k)
    if len(state.k_window) > state.signal_period:
        state.k_window.popleft()
    if len(state.k_window) < state.signal_period:
        return None
    return sum(state.k_window) / state.signal_period


def _stochastic_step(bar: _StochasticBar | None, state: _StochasticState) -> StochasticValue:
    if bar is None:
        return StochasticValue(None, None)
    state.bucket.append(bar)
    if len(state.bucket) > state.window:
        state.bucket.popleft()
    if len(state.bucket) < state.window:
        return StochasticValue(None, None)
    percent_k = _stochastic_k(state.bucket)
    return StochasticValue(percent_k, _percent_d(state, percent_k))


def stochastic_series(
    highs: Sequence[float | None],
    lows: Sequence[float | None],
    closes: Sequence[float | None],
    window: int,
    *,
    signal_period: int,
) -> list[StochasticValue]:
    """Stochastic %K and %D from OHLC bars.

    Invalid bars do not enter the lookback window. %D is the SMA of valid %K
    values. A flat high-low window yields null %K and does not feed %D.
    """

    _require_period(window, "Stochastic lookback")
    _require_period(signal_period, "Stochastic signal")
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows, and closes must have the same length.")
    state = _StochasticState(
        window=window,
        signal_period=signal_period,
        bucket=deque(),
        k_window=deque(),
    )
    return [
        _stochastic_step(_stoch_bar(high, low, close), state)
        for high, low, close in zip(highs, lows, closes, strict=True)
    ]


def stochastic_lookback(window: int, signal_period: int) -> int:
    """Prior bars needed before the first in-range %D can be non-null."""

    return window + signal_period - 2


def stochastic_lookback_text(window: int, signal_period: int) -> str:
    prior = stochastic_lookback(window, signal_period)
    return (
        f"%K needs {window} valid bars and %D needs {signal_period} valid %K values. "
        f"When start is set, up to {prior} earlier candles are calculation context "
        "and are omitted from results."
    )


DRAWDOWN_INITIALIZATION = (
    "Running peak starts at the first valid close in the requested period. "
    "Drawdown_t = close_t / peak_t - 1. Period maximum drawdown is the minimum "
    "running drawdown. Dividends and splits are excluded unless adjusted closes "
    "are present and preferred."
)


@dataclass(frozen=True)
class DrawdownPoint:
    """One dated running-drawdown observation."""

    trading_date: date
    close: Decimal
    drawdown: float | None
    peak: Decimal | None


class DrawdownObservation(TypedDict):
    trading_date: str
    close: str
    value: float | None
    peak: str | None


class DrawdownSeriesPayload(TypedDict):
    metric: str
    timeframe: str
    source: str
    instrument: InstrumentRef
    exchange: ExchangeRef
    parameters: MetricParameters
    requested_range: RequestedRange
    lookback_requirement: str
    initialization: str
    input_field: str
    output_unit: str
    maximum_drawdown: float | None
    data_availability: DataAvailability
    results: list[DrawdownObservation]


def drawdown_series(closes: Sequence[float | None]) -> list[tuple[float | None, float | None]]:
    """Running drawdown and peak from an ordered close series.

    Peak resets at the start of the provided series. The first valid close has
    drawdown 0. Invalid closes yield null and do not update the peak. Values are
    fractional (``-0.25`` means -25%).
    """

    values: list[tuple[float | None, float | None]] = []
    peak: float | None = None
    for close in closes:
        accepted = _accepted_value(close, require_positive=True)
        if accepted is None:
            values.append((None, peak))
            continue
        peak = accepted if peak is None else max(peak, accepted)
        values.append((accepted / peak - 1.0, peak))
    return values


def maximum_drawdown(drawdowns: Sequence[float | None]) -> float | None:
    """Period-level MDD as the minimum running drawdown, or null if none exist."""

    observed = [value for value in drawdowns if value is not None]
    if not observed:
        return None
    return min(observed)


def compute_daily_drawdowns(rows: Sequence[DailyOHLCV]) -> list[DrawdownPoint]:
    """Compute running drawdown points for an ascending DailyOHLCV series."""

    points: list[DrawdownPoint] = []
    peak: Decimal | None = None
    for row in rows:
        close = close_value(row)
        if close is None:
            points.append(
                DrawdownPoint(
                    trading_date=row.trading_date,
                    close=row.close,
                    drawdown=None,
                    peak=peak,
                )
            )
            continue
        peak = close if peak is None else max(peak, close)
        points.append(
            DrawdownPoint(
                trading_date=row.trading_date,
                close=close,
                drawdown=float(close / peak - 1),
                peak=peak,
            )
        )
    return points


def drawdown_point_payload(point: DrawdownPoint) -> DrawdownObservation:
    return {
        "trading_date": point.trading_date.isoformat(),
        "close": str(point.close),
        "value": point.drawdown,
        "peak": None if point.peak is None else str(point.peak),
    }


def drawdown_payload(
    instrument: Instrument,
    points: list[DrawdownObservation],
    *,
    start_date: date | None,
    end_date: date | None,
) -> DrawdownSeriesPayload:
    """Wrap one instrument drawdown series with metric metadata."""

    return {
        "metric": "drawdown",
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
        "parameters": {"lookback_observations": 0},
        "requested_range": {
            "start_date": None if start_date is None else start_date.isoformat(),
            "end_date": None if end_date is None else end_date.isoformat(),
        },
        "lookback_requirement": (
            "Peak resets at the first valid close in the requested period. "
            "Earlier history outside the range is not used."
        ),
        "initialization": DRAWDOWN_INITIALIZATION,
        "input_field": "adjusted_close_or_close",
        "output_unit": "fractional_return",
        "maximum_drawdown": maximum_drawdown([point["value"] for point in points]),
        "data_availability": {
            "observation_count": len(points),
            "null_count": sum(1 for point in points if point["value"] is None),
        },
        "results": points,
    }


def build_drawdown_series(
    rows: Sequence[DailyOHLCV],
    *,
    start_date: date | None,
    end_date: date | None,
) -> list[DrawdownSeriesPayload]:
    """Group DailyOHLCV rows by instrument and compute drawdown payloads."""

    by_instrument: dict[int, list[DailyOHLCV]] = {}
    for row in rows:
        by_instrument.setdefault(row.instrument.pk, []).append(row)
    instruments = {
        item.pk: item
        for item in Instrument.objects.filter(pk__in=by_instrument).select_related("exchange")
    }
    series: list[DrawdownSeriesPayload] = []
    for instrument_id, instrument_rows in by_instrument.items():
        points = [
            drawdown_point_payload(point) for point in compute_daily_drawdowns(instrument_rows)
        ]
        series.append(
            drawdown_payload(
                instruments[instrument_id],
                points,
                start_date=start_date,
                end_date=end_date,
            )
        )
    series.sort(key=lambda item: item["instrument"]["symbol"])
    return series
