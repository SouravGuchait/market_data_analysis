"""Canonical analytical metric helpers for Feature 008 and later metrics."""

from __future__ import annotations

import math
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
