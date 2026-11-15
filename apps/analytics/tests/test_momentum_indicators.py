"""RSI and Stochastic formula tests and analytics API tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import isclose
from typing import TypedDict

import pytest
from django.urls import reverse
from rest_framework.response import Response
from rest_framework.test import APIClient

from apps.analytics.services.metrics_calculator import rsi_series, stochastic_series
from apps.audit.models import AuditEvent
from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from apps.market_data.models import Candle, CandleTimeframe, DailyOHLCV
from apps.portfolios.models import Portfolio, PortfolioInstrument

RSI_URL = reverse("analytics-rsi")
STOCH_URL = reverse("analytics-stochastic")


class _Point(TypedDict):
    timestamp: str
    value: object
    percent_k: object
    percent_d: object


def _close(response_value: object, expected: float) -> None:
    assert isinstance(response_value, int | float)
    assert not isinstance(response_value, bool)
    assert isclose(float(response_value), expected, rel_tol=1e-12)


def test_rsi_increasing_series_is_100() -> None:
    values = rsi_series([1, 2, 3, 4], window=2)
    assert values[0] is None
    assert values[1] is None
    _close(values[2], 100)
    _close(values[3], 100)


def test_rsi_decreasing_series_is_0() -> None:
    values = rsi_series([5, 4, 3, 2], window=2)
    assert values[0] is None
    assert values[1] is None
    _close(values[2], 0)
    _close(values[3], 0)


def test_rsi_constant_prices_are_neutral_50() -> None:
    values = rsi_series([5, 5, 5, 5], window=2)
    assert values[0] is None
    assert values[1] is None
    _close(values[2], 50)
    _close(values[3], 50)


def test_rsi_wilder_smoothing_known_values() -> None:
    values = rsi_series([10, 12, 11, 13], window=2)
    assert values[0] is None
    assert values[1] is None
    _close(values[2], 100.0 - (100.0 / (1.0 + 2.0)))
    _close(values[3], 100.0 - (100.0 / (1.0 + 6.0)))


def test_rsi_skips_invalid_closes_without_advancing() -> None:
    values = rsi_series([10, None, 12, 11, 13], window=2)
    assert values[0] is None
    assert values[1] is None
    assert values[2] is None
    _close(values[3], 100.0 - (100.0 / 3.0))
    _close(values[4], 100.0 - (100.0 / 7.0))


def test_rsi_no_lookahead() -> None:
    original = rsi_series([10, 12, 11, 13], window=2)
    changed = rsi_series([10, 12, 11, 99], window=2)
    assert original[2] == changed[2]


def test_rsi_rejects_unsafe_window() -> None:
    with pytest.raises(ValueError):
        rsi_series([1, 2, 3], window=1)


def test_stochastic_known_values() -> None:
    highs = [10.0, 12.0, 11.0]
    lows = [8.0, 9.0, 7.0]
    closes = [9.0, 11.0, 8.0]
    values = stochastic_series(highs, lows, closes, window=2, signal_period=2)
    assert values[0].percent_k is None
    _close(values[1].percent_k, 75)
    assert values[1].percent_d is None
    _close(values[2].percent_k, 20)
    _close(values[2].percent_d, 47.5)


def test_stochastic_zero_range_yields_null_k() -> None:
    values = stochastic_series(
        [5.0, 5.0, 5.0],
        [5.0, 5.0, 5.0],
        [5.0, 5.0, 5.0],
        window=2,
        signal_period=2,
    )
    assert values[1].percent_k is None
    assert values[1].percent_d is None
    assert values[2].percent_k is None
    assert values[2].percent_d is None


def test_stochastic_insufficient_history() -> None:
    values = stochastic_series([10.0], [8.0], [9.0], window=2, signal_period=2)
    assert values == [values[0]]
    assert values[0].percent_k is None
    assert values[0].percent_d is None


def test_stochastic_rejects_unsafe_periods() -> None:
    with pytest.raises(ValueError):
        stochastic_series([1.0], [1.0], [1.0], window=1, signal_period=2)


def _candle(
    *,
    instrument: Instrument,
    exchange: Exchange,
    timeframe: str,
    bucket_start: datetime,
    open_: str,
    high: str,
    low: str,
    close: str,
    is_complete: bool = True,
) -> Candle:
    return Candle.objects.create(
        instrument=instrument,
        exchange=exchange,
        timeframe=timeframe,
        bucket_start=bucket_start,
        bucket_end=bucket_start + timedelta(minutes=1),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(1),
        trade_count=1,
        is_complete=is_complete,
    )


def _flat(
    *,
    instrument: Instrument,
    exchange: Exchange,
    timeframe: str,
    bucket_start: datetime,
    close: str,
    is_complete: bool = True,
) -> Candle:
    return _candle(
        instrument=instrument,
        exchange=exchange,
        timeframe=timeframe,
        bucket_start=bucket_start,
        open_=close,
        high=close,
        low=close,
        close=close,
        is_complete=is_complete,
    )


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _point(value: object) -> _Point:
    item = _mapping(value)
    timestamp = item["timestamp"]
    assert isinstance(timestamp, str)
    return {
        "timestamp": timestamp,
        "value": item.get("value"),
        "percent_k": item.get("percent_k"),
        "percent_d": item.get("percent_d"),
    }


def _points(value: object) -> list[_Point]:
    assert isinstance(value, list)
    return [_point(item) for item in value]


def _results(response: Response) -> list[dict[str, object]]:
    assert response.status_code == 200, response.content
    body = _mapping(response.data)
    assert body["success"] is True
    data = _mapping(body["data"])
    results = data["results"]
    assert isinstance(results, list)
    return [_mapping(item) for item in results]


@pytest.mark.django_db
def test_rsi_api_known_values_and_defaults(auth_client, instrument, exchange) -> None:
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    for offset, close in enumerate(("10", "12", "11", "13")):
        _flat(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=start + timedelta(minutes=offset),
            close=close,
        )
    series = _results(
        auth_client.get(
            RSI_URL,
            {"instrument_symbol": "ACME", "exchange_code": "XNYS", "timeframe": "1m", "window": 2},
        )
    )
    assert len(series) == 1
    payload = series[0]
    assert payload["metric"] == "rsi"
    assert payload["source"] == "trade_derived_candle"
    assert payload["output_unit"] == "oscillator"
    points = _points(payload["results"])
    assert points[0]["value"] is None
    assert points[1]["value"] is None
    _close(points[2]["value"], 100.0 - (100.0 / 3.0))
    _close(points[3]["value"], 100.0 - (100.0 / 7.0))
    defaults = _results(
        auth_client.get(RSI_URL, {"instrument_id": instrument.pk, "timeframe": "1m"})
    )[0]["parameters"]
    assert defaults == {"window": 14, "include_incomplete": False}


@pytest.mark.django_db
def test_stochastic_api_known_values_and_defaults(auth_client, instrument, exchange) -> None:
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    bars = (("10", "8", "9"), ("12", "9", "11"), ("11", "7", "8"))
    for offset, (high, low, close) in enumerate(bars):
        _candle(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=start + timedelta(minutes=offset),
            open_=close,
            high=high,
            low=low,
            close=close,
        )
    series = _results(
        auth_client.get(
            STOCH_URL,
            {
                "instrument_id": instrument.pk,
                "timeframe": "1m",
                "window": 2,
                "signal_period": 2,
            },
        )
    )[0]
    points = _points(series["results"])
    assert points[0]["percent_k"] is None
    _close(points[1]["percent_k"], 75)
    assert points[1]["percent_d"] is None
    _close(points[2]["percent_k"], 20)
    _close(points[2]["percent_d"], 47.5)
    defaults = _results(
        auth_client.get(STOCH_URL, {"instrument_id": instrument.pk, "timeframe": "1m"})
    )[0]["parameters"]
    assert defaults == {
        "window": 14,
        "signal_period": 3,
        "include_incomplete": False,
    }


@pytest.mark.django_db
def test_rsi_start_uses_lookback_without_returning_it(auth_client, instrument, exchange) -> None:
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    for offset, close in enumerate(("10", "12", "11", "13")):
        _flat(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=start + timedelta(minutes=offset),
            close=close,
        )
    points = _points(
        _results(
            auth_client.get(
                RSI_URL,
                {
                    "instrument_id": instrument.pk,
                    "timeframe": "1m",
                    "window": 2,
                    "start": (start + timedelta(minutes=2)).isoformat(),
                },
            )
        )[0]["results"]
    )
    assert [point["timestamp"] for point in points] == [
        "2024-01-02T15:02:00Z",
        "2024-01-02T15:03:00Z",
    ]
    assert points[0]["value"] is not None


@pytest.mark.django_db
def test_stochastic_zero_range_via_api(auth_client, instrument, exchange) -> None:
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    for offset in range(3):
        _flat(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=start + timedelta(minutes=offset),
            close="5",
        )
    points = _points(
        _results(
            auth_client.get(
                STOCH_URL,
                {
                    "instrument_id": instrument.pk,
                    "timeframe": "1m",
                    "window": 2,
                    "signal_period": 2,
                },
            )
        )[0]["results"]
    )
    assert points[1]["percent_k"] is None
    assert points[2]["percent_k"] is None


@pytest.mark.django_db
def test_incomplete_candles_excluded_unless_requested(auth_client, instrument, exchange) -> None:
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    for offset, close in enumerate(("1", "2", "3")):
        _flat(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=start + timedelta(minutes=offset),
            close=close,
        )
    _flat(
        instrument=instrument,
        exchange=exchange,
        timeframe=CandleTimeframe.ONE_MINUTE,
        bucket_start=start + timedelta(minutes=3),
        close="4",
        is_complete=False,
    )
    complete = _points(
        _results(
            auth_client.get(
                RSI_URL, {"instrument_id": instrument.pk, "timeframe": "1m", "window": 2}
            )
        )[0]["results"]
    )
    assert len(complete) == 3
    included = _points(
        _results(
            auth_client.get(
                RSI_URL,
                {
                    "instrument_id": instrument.pk,
                    "timeframe": "1m",
                    "window": 2,
                    "include_incomplete": "true",
                },
            )
        )[0]["results"]
    )
    assert len(included) == 4


@pytest.mark.django_db
def test_corrected_candle_recalculated_on_next_read(auth_client, instrument, exchange) -> None:
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    candles = [
        _flat(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=start + timedelta(minutes=offset),
            close=close,
        )
        for offset, close in enumerate(("10", "12", "11", "13"))
    ]
    params = {"instrument_id": instrument.pk, "timeframe": "1m", "window": 2}
    before = _points(_results(auth_client.get(RSI_URL, params))[0]["results"])
    _close(before[2]["value"], 100.0 - (100.0 / 3.0))
    candles[3].close = Decimal(9)
    candles[3].high = Decimal(9)
    candles[3].low = Decimal(9)
    candles[3].open = Decimal(9)
    candles[3].save(update_fields=["close", "high", "low", "open"])
    from apps.common.cache_service import bump_market_version

    bump_market_version()
    after = _points(_results(auth_client.get(RSI_URL, params))[0]["results"])
    _close(after[2]["value"], 100.0 - (100.0 / 3.0))
    assert after[3]["value"] != before[3]["value"]


@pytest.mark.django_db
def test_foreign_portfolio_not_found(auth_client, other_user) -> None:
    portfolio = Portfolio.objects.create(owner=other_user, name="Private")
    response = auth_client.get(RSI_URL, {"portfolio_id": str(portfolio.pk), "timeframe": "1m"})
    assert response.status_code == 404


@pytest.mark.django_db
def test_portfolio_scope_keeps_instruments_separate(
    auth_client, user, instrument, exchange, other_instrument
) -> None:
    portfolio = Portfolio.objects.create(owner=user, name="Both")
    PortfolioInstrument.objects.create(
        portfolio=portfolio, instrument=instrument, exchange=exchange
    )
    PortfolioInstrument.objects.create(
        portfolio=portfolio,
        instrument=other_instrument,
        exchange=other_instrument.exchange,
    )
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    for offset, close in enumerate(("1", "2", "3")):
        stamp = start + timedelta(minutes=offset)
        _flat(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=stamp,
            close=close,
        )
        _flat(
            instrument=other_instrument,
            exchange=other_instrument.exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=stamp,
            close=str(int(close) * 10),
        )
    series = _results(
        auth_client.get(
            RSI_URL,
            {"portfolio_id": str(portfolio.pk), "timeframe": "1m", "window": 2},
        )
    )
    assert [_mapping(item["instrument"])["symbol"] for item in series] == ["ACME", "BETA"]


@pytest.mark.django_db
def test_daily_ohlcv_is_not_momentum_source(auth_client, instrument) -> None:
    DailyOHLCV.objects.create(
        instrument=instrument,
        trading_date=datetime(2024, 1, 2, tzinfo=UTC).date(),
        timestamp=datetime(2024, 1, 2, 21, 0, tzinfo=UTC),
        open=Decimal(10),
        high=Decimal(11),
        low=Decimal(9),
        close=Decimal(10),
        volume=100,
    )
    assert (
        _results(auth_client.get(RSI_URL, {"instrument_id": instrument.pk, "timeframe": "1d"}))
        == []
    )


@pytest.mark.django_db
def test_momentum_get_does_not_write_audit(auth_client, instrument, exchange) -> None:
    _flat(
        instrument=instrument,
        exchange=exchange,
        timeframe=CandleTimeframe.ONE_MINUTE,
        bucket_start=datetime(2024, 1, 2, 15, 0, tzinfo=UTC),
        close="10",
    )
    before = AuditEvent.objects.count()
    response = auth_client.get(RSI_URL, {"instrument_id": instrument.pk, "timeframe": "1m"})
    assert response.status_code == 200
    assert AuditEvent.objects.count() == before


@pytest.mark.django_db
def test_momentum_auth_and_validation(auth_client, instrument) -> None:
    denied = APIClient().get(RSI_URL, {"instrument_id": instrument.pk, "timeframe": "1m"})
    assert isinstance(denied, Response)
    assert denied.status_code == 401
    missing = auth_client.get(STOCH_URL, {"timeframe": "1m"})
    assert isinstance(missing, Response)
    assert missing.status_code == 400
    window = auth_client.get(
        RSI_URL, {"instrument_id": instrument.pk, "timeframe": "1m", "window": 1}
    )
    assert isinstance(window, Response)
    assert window.status_code == 400


@pytest.mark.django_db
def test_observation_limit(auth_client, instrument, exchange, monkeypatch) -> None:
    monkeypatch.setattr("apps.analytics.services.momentum.MAX_INDICATOR_CANDLES", 1)
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    for offset in range(2):
        _flat(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=start + timedelta(minutes=offset),
            close="1",
        )
    response = auth_client.get(
        RSI_URL, {"instrument_id": instrument.pk, "timeframe": "1m", "window": 2}
    )
    assert response.status_code == 400
