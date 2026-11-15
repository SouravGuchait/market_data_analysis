"""Combine already-implemented analytics into one overview payload."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from apps.analytics.selectors import IndicatorCandleQuery, list_daily_ohlcv
from apps.analytics.services.metrics_calculator import (
    DEFAULT_ATR_WINDOW,
    DEFAULT_BOLLINGER_MULTIPLIER,
    DEFAULT_RSI_WINDOW,
    DEFAULT_STOCHASTIC_SIGNAL,
    DEFAULT_STOCHASTIC_WINDOW,
    DEFAULT_WINDOW,
    MACD_DEFAULT_FAST,
    MACD_DEFAULT_SIGNAL,
    MACD_DEFAULT_SLOW,
    build_drawdown_series,
    build_returns_series,
)
from apps.analytics.services.momentum import (
    RsiIndicatorQuery,
    StochasticIndicatorQuery,
    build_rsi_series,
    build_stochastic_series,
)
from apps.analytics.services.trend import (
    IndicatorLimitError,
    MacdIndicatorQuery,
    WindowIndicatorQuery,
    build_ema_series,
    build_macd_series,
    build_sma_series,
)
from apps.analytics.services.volatility import (
    AtrIndicatorQuery,
    BollingerIndicatorQuery,
    build_atr_series,
    build_bollinger_series,
)
from apps.market_data.models import DailyOHLCV

CANDLE_METRICS = frozenset({"sma", "ema", "macd", "bollinger", "atr", "rsi", "stochastic"})
DAILY_METRICS = frozenset({"returns", "drawdown"})
SUPPORTED_METRICS = CANDLE_METRICS | DAILY_METRICS


@dataclass(frozen=True)
class OverviewRequest:
    instrument_ids: list[int]
    exchange_ids: list[int] | None
    metrics: tuple[str, ...]
    timeframe: str | None
    start: datetime | None
    end: datetime | None
    start_date: date | None
    end_date: date | None
    include_incomplete: bool
    return_type: str
    window: int
    std_multiplier: float
    fast_period: int
    slow_period: int
    signal_period: int
    stochastic_signal: int


def parse_metrics(raw: str | None) -> tuple[str, ...] | None:
    if raw is None or not raw.strip():
        return ("sma", "returns", "drawdown")
    parts = [part.strip().lower() for part in raw.split(",") if part.strip()]
    invalid = not parts or any(part not in SUPPORTED_METRICS for part in parts)
    if invalid:
        return None
    return tuple(dict.fromkeys(parts))


def _candle_query(request: OverviewRequest) -> IndicatorCandleQuery | None:
    if request.timeframe is None:
        return None
    return IndicatorCandleQuery(
        instrument_ids=request.instrument_ids,
        exchange_ids=request.exchange_ids,
        timeframe=request.timeframe,
        range_start=request.start,
        range_end=request.end,
        include_incomplete=request.include_incomplete,
    )


def _series_or_error(
    outcome: IndicatorLimitError | Sequence[object],
) -> list[object] | IndicatorLimitError:
    if isinstance(outcome, IndicatorLimitError):
        return outcome
    return list(outcome)


Builder = Callable[[OverviewRequest], list[object] | IndicatorLimitError]


def _build_sma(request: OverviewRequest) -> list[object] | IndicatorLimitError:
    candles = _candle_query(request)
    if candles is None:
        return IndicatorLimitError("timeframe required", {"timeframe": ["Required."]})
    return _series_or_error(
        build_sma_series(WindowIndicatorQuery(candles=candles, window=request.window))
    )


def _build_ema(request: OverviewRequest) -> list[object] | IndicatorLimitError:
    candles = _candle_query(request)
    if candles is None:
        return IndicatorLimitError("timeframe required", {"timeframe": ["Required."]})
    return _series_or_error(
        build_ema_series(WindowIndicatorQuery(candles=candles, window=request.window))
    )


def _build_macd(request: OverviewRequest) -> list[object] | IndicatorLimitError:
    candles = _candle_query(request)
    if candles is None:
        return IndicatorLimitError("timeframe required", {"timeframe": ["Required."]})
    query = MacdIndicatorQuery(
        candles=candles,
        fast_period=request.fast_period,
        slow_period=request.slow_period,
        signal_period=request.signal_period,
    )
    return _series_or_error(build_macd_series(query))


def _build_bollinger(request: OverviewRequest) -> list[object] | IndicatorLimitError:
    candles = _candle_query(request)
    if candles is None:
        return IndicatorLimitError("timeframe required", {"timeframe": ["Required."]})
    query = BollingerIndicatorQuery(
        candles=candles,
        window=request.window,
        std_multiplier=request.std_multiplier,
    )
    return _series_or_error(build_bollinger_series(query))


def _build_atr(request: OverviewRequest) -> list[object] | IndicatorLimitError:
    candles = _candle_query(request)
    if candles is None:
        return IndicatorLimitError("timeframe required", {"timeframe": ["Required."]})
    return _series_or_error(
        build_atr_series(AtrIndicatorQuery(candles=candles, window=request.window))
    )


def _build_rsi(request: OverviewRequest) -> list[object] | IndicatorLimitError:
    candles = _candle_query(request)
    if candles is None:
        return IndicatorLimitError("timeframe required", {"timeframe": ["Required."]})
    return _series_or_error(
        build_rsi_series(RsiIndicatorQuery(candles=candles, window=request.window))
    )


def _build_stochastic(request: OverviewRequest) -> list[object] | IndicatorLimitError:
    candles = _candle_query(request)
    if candles is None:
        return IndicatorLimitError("timeframe required", {"timeframe": ["Required."]})
    query = StochasticIndicatorQuery(
        candles=candles,
        window=request.window,
        signal_period=request.stochastic_signal,
    )
    return _series_or_error(build_stochastic_series(query))


def _daily_rows(request: OverviewRequest) -> list[DailyOHLCV]:
    return list(
        list_daily_ohlcv(
            instrument_ids=request.instrument_ids,
            start_date=request.start_date,
            end_date=request.end_date,
        )
    )


def _build_returns(request: OverviewRequest) -> list[object] | IndicatorLimitError:
    return list(
        build_returns_series(
            _daily_rows(request),
            start_date=request.start_date,
            end_date=request.end_date,
            return_type=request.return_type,
        )
    )


def _build_drawdown(request: OverviewRequest) -> list[object] | IndicatorLimitError:
    return list(
        build_drawdown_series(
            _daily_rows(request),
            start_date=request.start_date,
            end_date=request.end_date,
        )
    )


BUILDERS: Mapping[str, Builder] = {
    "sma": _build_sma,
    "ema": _build_ema,
    "macd": _build_macd,
    "bollinger": _build_bollinger,
    "atr": _build_atr,
    "rsi": _build_rsi,
    "stochastic": _build_stochastic,
    "returns": _build_returns,
    "drawdown": _build_drawdown,
}


def _observation_count(series_list: Sequence[object]) -> int:
    total = 0
    for series in series_list:
        if not isinstance(series, Mapping):
            continue
        results = series.get("results")
        if isinstance(results, Sequence):
            total += len(results)
    return total


def _metric_block(name: str, series_list: Sequence[object]) -> dict[str, object]:
    source = "daily_ohlcv" if name in DAILY_METRICS else "trade_derived_candle"
    sample = series_list[0] if series_list and isinstance(series_list[0], Mapping) else {}
    return {
        "metric": name,
        "source": sample.get("source", source) if isinstance(sample, Mapping) else source,
        "timeframe": sample.get("timeframe") if isinstance(sample, Mapping) else None,
        "parameters": sample.get("parameters", {}) if isinstance(sample, Mapping) else {},
        "incomplete_policy": (
            sample.get("incomplete_policy") if isinstance(sample, Mapping) else None
        ),
        "observation_count": _observation_count(series_list),
        "series": list(series_list),
    }


def _append_metric(
    blocks: list[dict[str, object]],
    name: str,
    outcome: list[object] | IndicatorLimitError,
) -> IndicatorLimitError | None:
    if isinstance(outcome, IndicatorLimitError):
        return outcome
    blocks.append(_metric_block(name, outcome))
    return None


def build_overview(request: OverviewRequest) -> dict[str, object] | IndicatorLimitError:
    blocks: list[dict[str, object]] = []
    for name in request.metrics:
        err = _append_metric(blocks, name, BUILDERS[name](request))
        if err is not None:
            return err
    return {
        "metrics": list(request.metrics),
        "timeframe": request.timeframe,
        "include_incomplete": request.include_incomplete,
        "results": blocks,
    }


def default_overview_params() -> dict[str, object]:
    return {
        "window": DEFAULT_WINDOW,
        "std_multiplier": DEFAULT_BOLLINGER_MULTIPLIER,
        "fast_period": MACD_DEFAULT_FAST,
        "slow_period": MACD_DEFAULT_SLOW,
        "signal_period": MACD_DEFAULT_SIGNAL,
        "rsi_window": DEFAULT_RSI_WINDOW,
        "atr_window": DEFAULT_ATR_WINDOW,
        "stochastic_window": DEFAULT_STOCHASTIC_WINDOW,
        "stochastic_signal": DEFAULT_STOCHASTIC_SIGNAL,
    }
