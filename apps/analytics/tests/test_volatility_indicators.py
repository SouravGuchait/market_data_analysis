"""Bollinger Bands and ATR formula tests and analytics API tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import isclose, sqrt
from typing import TypedDict

import pytest
from django.http import HttpResponse
from django.urls import reverse
from rest_framework.test import APIClient

from apps.analytics.services.metrics_calculator import (
    atr_series,
    band_width,
    bollinger_series,
    true_range_series,
)
from apps.audit.models import AuditEvent
from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from apps.market_data.models import Candle, CandleTimeframe, DailyOHLCV
from apps.portfolios.models import Portfolio, PortfolioInstrument

BOLLINGER_URL = reverse("analytics-bollinger")
ATR_URL = reverse("analytics-atr")


class _BandPoint(TypedDict):
    timestamp: str
    middle: object
    upper: object
    lower: object
    width: object
    value: object


def _close(response_value: object, expected: float) -> None:
    assert isinstance(response_value, int | float)
    assert not isinstance(response_value, bool)
    assert isclose(float(response_value), expected, rel_tol=1e-12)


def test_bollinger_known_window_and_warmup() -> None:
    values = bollinger_series([1, 2, 3, 4, 5], 3, std_multiplier=2)
    assert values[0].middle is None
    assert values[1].middle is None
    mean = 2.0
    std = sqrt(((1 - mean) ** 2 + (2 - mean) ** 2 + (3 - mean) ** 2) / 3)
    _close(values[2].middle, mean)
    _close(values[2].upper, mean + 2 * std)
    _close(values[2].lower, mean - 2 * std)
    _close(values[2].width, (4 * std) / mean)


def test_bollinger_width_null_when_middle_is_zero() -> None:
    assert band_width(0.0, 2.0, -2.0) is None
    _close(band_width(2.0, 4.0, 0.0), 2.0)


def test_bollinger_constant_prices_have_zero_width() -> None:
    values = bollinger_series([2, 2, 2], 3, std_multiplier=2)
    _close(values[2].middle, 2)
    _close(values[2].upper, 2)
    _close(values[2].lower, 2)
    _close(values[2].width, 0)


def test_bollinger_skips_invalid_closes() -> None:
    values = bollinger_series([1, None, 2, 0, -1, 3], 2, std_multiplier=1)
    assert values[0].middle is None
    assert values[1].middle is None
    _close(values[2].middle, 1.5)
    assert values[3].middle is None
    assert values[4].middle is None
    _close(values[5].middle, 2.5)


def test_bollinger_rejects_bad_multiplier_and_window() -> None:
    with pytest.raises(ValueError):
        bollinger_series([1, 2, 3], 3, std_multiplier=0)
    with pytest.raises(ValueError):
        bollinger_series([1, 2, 3], 3, std_multiplier=5.1)
    with pytest.raises(ValueError):
        bollinger_series([1, 2, 3], 1, std_multiplier=2)


def test_bollinger_no_lookahead() -> None:
    original = bollinger_series([1, 2, 3, 4], 3, std_multiplier=2)
    changed = bollinger_series([1, 2, 3, 99], 3, std_multiplier=2)
    assert original[2].middle == changed[2].middle
    assert original[2].upper == changed[2].upper


def test_true_range_first_bar_uses_high_minus_low() -> None:
    values = true_range_series([10, 12], [8, 9], [9, 11])
    _close(values[0], 2)
    _close(values[1], max(3, abs(12 - 9), abs(9 - 9)))


def test_atr_wilder_initialization_and_smoothing() -> None:
    highs = [10.0, 12.0, 11.0, 13.0]
    lows = [8.0, 9.0, 7.0, 10.0]
    closes = [9.0, 11.0, 8.0, 12.0]
    values = atr_series(highs, lows, closes, window=3)
    tr = true_range_series(highs, lows, closes)
    assert values[0] is None
    assert values[1] is None
    assert tr[0] is not None and tr[1] is not None and tr[2] is not None
    seed = (tr[0] + tr[1] + tr[2]) / 3
    _close(values[2], seed)
    assert tr[3] is not None
    _close(values[3], (2 * seed + tr[3]) / 3)


def test_atr_skips_invalid_bars_without_advancing_state() -> None:
    highs = [10.0, None, 12.0, 13.0]
    lows = [8.0, 9.0, 9.0, 10.0]
    closes = [9.0, 10.0, 11.0, 12.0]
    values = atr_series(highs, lows, closes, window=2)
    assert values[0] is None
    assert values[1] is None
    seeded = values[2]
    assert seeded is not None
    _close(seeded, (2 + 3) / 2)
    next_value = values[3]
    assert next_value is not None
    _close(next_value, (1 * seeded + 3) / 2)


def test_atr_rejects_unsafe_window() -> None:
    with pytest.raises(ValueError):
        atr_series([1], [1], [1], window=1)


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


def _flat_candle(
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


def _band(value: object) -> _BandPoint:
    item = _mapping(value)
    timestamp = item["timestamp"]
    assert isinstance(timestamp, str)
    return {
        "timestamp": timestamp,
        "middle": item.get("middle"),
        "upper": item.get("upper"),
        "lower": item.get("lower"),
        "width": item.get("width"),
        "value": item.get("value"),
    }


def _bands(value: object) -> list[_BandPoint]:
    assert isinstance(value, list)
    return [_band(item) for item in value]


def _results(response: HttpResponse) -> list[dict[str, object]]:
    assert response.status_code == 200, response.content
    body = _mapping(json.loads(response.content.decode()))
    assert body["success"] is True
    data = _mapping(body["data"])
    results = data["results"]
    assert isinstance(results, list)
    return [_mapping(item) for item in results]


@pytest.mark.django_db
def test_bollinger_api_matches_formula(auth_client, instrument, exchange) -> None:
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    for offset, close in enumerate(("1", "2", "3", "4", "5")):
        _flat_candle(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=start + timedelta(minutes=offset),
            close=close,
        )
    series = _results(
        auth_client.get(
            BOLLINGER_URL,
            {
                "instrument_symbol": "ACME",
                "exchange_code": "XNYS",
                "timeframe": "1m",
                "window": 3,
                "std_multiplier": 2,
            },
        )
    )
    assert len(series) == 1
    payload = series[0]
    assert payload["metric"] == "bollinger"
    assert payload["source"] == "trade_derived_candle"
    assert payload["output_unit"] == "price"
    points = _bands(payload["results"])
    assert points[0]["middle"] is None
    assert points[1]["middle"] is None
    mean = 2.0
    std = sqrt(((1 - mean) ** 2 + (2 - mean) ** 2 + (3 - mean) ** 2) / 3)
    _close(points[2]["middle"], mean)
    _close(points[2]["upper"], mean + 2 * std)
    _close(points[2]["lower"], mean - 2 * std)
    _close(points[2]["width"], (4 * std) / mean)


@pytest.mark.django_db
def test_bollinger_defaults_and_multiplier_validation(auth_client, instrument, exchange) -> None:
    _flat_candle(
        instrument=instrument,
        exchange=exchange,
        timeframe=CandleTimeframe.ONE_MINUTE,
        bucket_start=datetime(2024, 1, 2, 15, 0, tzinfo=UTC),
        close="10",
    )
    defaults = _results(
        auth_client.get(BOLLINGER_URL, {"instrument_id": instrument.pk, "timeframe": "1m"})
    )[0]["parameters"]
    assert defaults == {
        "window": 20,
        "std_multiplier": 2.0,
        "include_incomplete": False,
    }
    low = auth_client.get(
        BOLLINGER_URL,
        {"instrument_id": instrument.pk, "timeframe": "1m", "std_multiplier": 0.05},
    )
    assert low.status_code == 400
    high = auth_client.get(
        BOLLINGER_URL,
        {"instrument_id": instrument.pk, "timeframe": "1m", "std_multiplier": 5.5},
    )
    assert high.status_code == 400


@pytest.mark.django_db
def test_atr_api_known_values_and_default_window(auth_client, instrument, exchange) -> None:
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    bars = (
        ("10", "8", "9"),
        ("12", "9", "11"),
        ("11", "7", "8"),
        ("13", "10", "12"),
    )
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
            ATR_URL,
            {"instrument_id": instrument.pk, "timeframe": "1m", "window": 3},
        )
    )[0]
    points = _bands(series["results"])
    assert points[0]["value"] is None
    assert points[1]["value"] is None
    tr = true_range_series([10, 12, 11, 13], [8, 9, 7, 10], [9, 11, 8, 12])
    assert tr[0] is not None and tr[1] is not None and tr[2] is not None and tr[3] is not None
    seed = (tr[0] + tr[1] + tr[2]) / 3
    _close(points[2]["value"], seed)
    _close(points[3]["value"], (2 * seed + tr[3]) / 3)
    defaults = _results(
        auth_client.get(ATR_URL, {"instrument_id": instrument.pk, "timeframe": "1m"})
    )[0]["parameters"]
    assert defaults == {"window": 14, "include_incomplete": False}


@pytest.mark.django_db
def test_atr_start_uses_lookback_without_returning_it(auth_client, instrument, exchange) -> None:
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    bars = (("10", "8", "9"), ("12", "9", "11"), ("11", "7", "8"), ("13", "10", "12"))
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
    points = _bands(
        _results(
            auth_client.get(
                ATR_URL,
                {
                    "instrument_id": instrument.pk,
                    "timeframe": "1m",
                    "window": 3,
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
    assert points[1]["value"] is not None


@pytest.mark.django_db
def test_incomplete_candles_excluded_unless_requested(auth_client, instrument, exchange) -> None:
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    for offset, close in enumerate(("1", "2", "3")):
        _flat_candle(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=start + timedelta(minutes=offset),
            close=close,
        )
    _flat_candle(
        instrument=instrument,
        exchange=exchange,
        timeframe=CandleTimeframe.ONE_MINUTE,
        bucket_start=start + timedelta(minutes=3),
        close="9",
        is_complete=False,
    )
    complete = _bands(
        _results(
            auth_client.get(
                BOLLINGER_URL,
                {"instrument_id": instrument.pk, "timeframe": "1m", "window": 3},
            )
        )[0]["results"]
    )
    assert len(complete) == 3
    included = _bands(
        _results(
            auth_client.get(
                BOLLINGER_URL,
                {
                    "instrument_id": instrument.pk,
                    "timeframe": "1m",
                    "window": 3,
                    "include_incomplete": "true",
                },
            )
        )[0]["results"]
    )
    assert len(included) == 4
    assert included[3]["middle"] is not None


@pytest.mark.django_db
def test_corrected_candle_recalculated_on_next_read(auth_client, instrument, exchange) -> None:
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    candles = [
        _flat_candle(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=start + timedelta(minutes=offset),
            close=close,
        )
        for offset, close in enumerate(("1", "2", "3", "4"))
    ]
    params = {"instrument_id": instrument.pk, "timeframe": "1m", "window": 3}
    before = _bands(_results(auth_client.get(BOLLINGER_URL, params))[0]["results"])
    _close(before[2]["middle"], 2)
    candles[3].close = Decimal(10)
    candles[3].high = Decimal(10)
    candles[3].low = Decimal(10)
    candles[3].open = Decimal(10)
    candles[3].save(update_fields=["close", "high", "low", "open"])
    from apps.common.cache_service import bump_market_version

    bump_market_version()
    after = _bands(_results(auth_client.get(BOLLINGER_URL, params))[0]["results"])
    _close(after[2]["middle"], 2)
    _close(after[3]["middle"], 5)


@pytest.mark.django_db
def test_foreign_portfolio_not_found(auth_client, other_user) -> None:
    portfolio = Portfolio.objects.create(owner=other_user, name="Private")
    response = auth_client.get(ATR_URL, {"portfolio_id": str(portfolio.pk), "timeframe": "1m"})
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
        _flat_candle(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=stamp,
            close=close,
        )
        _flat_candle(
            instrument=other_instrument,
            exchange=other_instrument.exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=stamp,
            close=str(int(close) * 10),
        )
    series = _results(
        auth_client.get(
            BOLLINGER_URL,
            {"portfolio_id": str(portfolio.pk), "timeframe": "1m", "window": 3},
        )
    )
    assert [_mapping(item["instrument"])["symbol"] for item in series] == ["ACME", "BETA"]


@pytest.mark.django_db
def test_daily_ohlcv_is_not_volatility_source(auth_client, instrument) -> None:
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
        _results(auth_client.get(ATR_URL, {"instrument_id": instrument.pk, "timeframe": "1d"}))
        == []
    )


@pytest.mark.django_db
def test_volatility_get_does_not_write_audit(auth_client, instrument, exchange) -> None:
    _flat_candle(
        instrument=instrument,
        exchange=exchange,
        timeframe=CandleTimeframe.ONE_MINUTE,
        bucket_start=datetime(2024, 1, 2, 15, 0, tzinfo=UTC),
        close="10",
    )
    before = AuditEvent.objects.count()
    response = auth_client.get(BOLLINGER_URL, {"instrument_id": instrument.pk, "timeframe": "1m"})
    assert response.status_code == 200
    assert AuditEvent.objects.count() == before


@pytest.mark.django_db
def test_volatility_auth_and_validation(auth_client, instrument) -> None:
    denied = APIClient().get(ATR_URL, {"instrument_id": instrument.pk, "timeframe": "1m"})
    assert isinstance(denied, HttpResponse)
    assert denied.status_code == 401
    missing = auth_client.get(BOLLINGER_URL, {"timeframe": "1m"})
    assert isinstance(missing, HttpResponse)
    assert missing.status_code == 400
    window = auth_client.get(
        ATR_URL, {"instrument_id": instrument.pk, "timeframe": "1m", "window": 1}
    )
    assert isinstance(window, HttpResponse)
    assert window.status_code == 400
    span = auth_client.get(
        ATR_URL,
        {
            "instrument_id": instrument.pk,
            "timeframe": "1m",
            "start": "2020-01-01T00:00:00Z",
            "end": "2030-02-01T00:00:00Z",
        },
    )
    assert isinstance(span, HttpResponse)
    assert span.status_code == 400


@pytest.mark.django_db
def test_observation_limit(auth_client, instrument, exchange, monkeypatch) -> None:
    monkeypatch.setattr("apps.analytics.services.volatility.MAX_INDICATOR_CANDLES", 1)
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    for offset in range(2):
        _flat_candle(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=start + timedelta(minutes=offset),
            close="1",
        )
    response = auth_client.get(
        ATR_URL, {"instrument_id": instrument.pk, "timeframe": "1m", "window": 2}
    )
    assert response.status_code == 400
