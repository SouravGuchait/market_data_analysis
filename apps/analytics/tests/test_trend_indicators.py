"""SMA, EMA, and MACD formula tests and analytics API tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import isclose
from typing import TypedDict

import pytest
from django.urls import reverse
from rest_framework.response import Response
from rest_framework.test import APIClient

from apps.analytics.services.metrics_calculator import ema_series, macd_series, sma_series
from apps.audit.models import AuditEvent
from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from apps.market_data.models import Candle, CandleTimeframe, DailyOHLCV
from apps.portfolios.models import Portfolio, PortfolioInstrument

SMA_URL = reverse("analytics-sma")
EMA_URL = reverse("analytics-ema")
MACD_URL = reverse("analytics-macd")


class _Point(TypedDict):
    timestamp: str
    value: object
    macd: object
    signal: object
    histogram: object


def _close(response_value: object, expected: float) -> None:
    assert isinstance(response_value, int | float)
    assert not isinstance(response_value, bool)
    assert isclose(float(response_value), expected, rel_tol=1e-12)


def test_sma_full_window_and_warmup() -> None:
    values = sma_series([1, 2, 3, 4, 5], 3)
    assert values[:2] == [None, None]
    assert values[2:] == [2, 3, 4]


def test_sma_skips_invalid_closes_without_filling_zero() -> None:
    values = sma_series([1, None, 2, 0, -1, 3], 2)
    assert values[0] is None
    assert values[1] is None
    _close(values[2], 1.5)
    assert values[3] is None
    assert values[4] is None
    _close(values[5], 2.5)


def test_sma_does_not_use_future_closes() -> None:
    original = sma_series([1, 2, 3, 4], 3)
    changed = sma_series([1, 2, 3, 99], 3)
    assert original[2] == changed[2] == 2
    _close(changed[3], (2 + 3 + 99) / 3)


def test_ema_seeded_with_first_sma() -> None:
    values = ema_series([1, 2, 3, 4, 5], 3)
    assert values[0] is None
    assert values[1] is None
    _close(values[2], 2)
    _close(values[3], 3)
    _close(values[4], 4)


def test_macd_signal_accepts_negative_line() -> None:
    values = macd_series([5, 4, 3, 2, 1], fast=2, slow=3, signal=2)
    assert values[2].macd is not None
    assert values[2].signal is None
    assert values[2].histogram is None
    _close(values[2].macd, -0.5)
    _close(values[3].histogram, 0)
    _close(values[4].histogram, 0)


def test_macd_known_linear_series() -> None:
    values = macd_series([1, 2, 3, 4, 5, 6, 7, 8], fast=2, slow=4, signal=2)
    assert values[3].macd is not None
    assert values[3].histogram is None
    _close(values[3].macd, 1)
    _close(values[4].histogram, 0)
    _close(values[7].macd, 1)


def test_indicator_periods_reject_unsafe_windows() -> None:
    with pytest.raises(ValueError):
        sma_series([1, 2, 3], 1)
    with pytest.raises(ValueError):
        macd_series([1, 2, 3, 4], fast=4, slow=4, signal=2)


def _candle(
    *,
    instrument: Instrument,
    exchange: Exchange,
    timeframe: str,
    bucket_start: datetime,
    close: str,
    is_complete: bool = True,
) -> Candle:
    price = Decimal(close)
    return Candle.objects.create(
        instrument=instrument,
        exchange=exchange,
        timeframe=timeframe,
        bucket_start=bucket_start,
        bucket_end=bucket_start + timedelta(minutes=1),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal(1),
        trade_count=1,
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
        "macd": item.get("macd"),
        "signal": item.get("signal"),
        "histogram": item.get("histogram"),
    }


def _points(value: object) -> list[_Point]:
    assert isinstance(value, list)
    return [_point(item) for item in value]


def _results(response: Response) -> list[dict[str, object]]:
    assert response.status_code == 200, response.content
    body = _mapping(response.data)
    assert body["success"] is True
    assert body["errors"] is None
    data = _mapping(body["data"])
    results = data["results"]
    assert isinstance(results, list)
    assert data["count"] == len(results)
    return [_mapping(item) for item in results]


@pytest.mark.django_db
def test_sma_api_matches_formula_and_sorts_by_time(auth_client, instrument, exchange) -> None:
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    _candle(
        instrument=instrument,
        exchange=exchange,
        timeframe=CandleTimeframe.ONE_MINUTE,
        bucket_start=start + timedelta(minutes=2),
        close="3",
    )
    _candle(
        instrument=instrument,
        exchange=exchange,
        timeframe=CandleTimeframe.ONE_MINUTE,
        bucket_start=start,
        close="1",
    )
    _candle(
        instrument=instrument,
        exchange=exchange,
        timeframe=CandleTimeframe.ONE_MINUTE,
        bucket_start=start + timedelta(minutes=1),
        close="2",
    )
    _candle(
        instrument=instrument,
        exchange=exchange,
        timeframe=CandleTimeframe.FIVE_MINUTES,
        bucket_start=start,
        close="100",
    )
    series = _results(
        auth_client.get(
            SMA_URL,
            {"instrument_symbol": "ACME", "exchange_code": "XNYS", "timeframe": "1m", "window": 3},
        )
    )
    assert len(series) == 1
    payload = series[0]
    assert payload["metric"] == "sma"
    assert payload["source"] == "trade_derived_candle"
    assert payload["output_unit"] == "price"
    assert payload["input_field"] == "close"
    instrument_ref = payload["instrument"]
    exchange_ref = payload["exchange"]
    assert isinstance(instrument_ref, dict)
    assert isinstance(exchange_ref, dict)
    assert instrument_ref["symbol"] == "ACME"
    assert exchange_ref["code"] == "XNYS"
    points = payload["results"]
    assert isinstance(points, list)
    assert [point["value"] for point in points] == [None, None, 2]
    assert points[0]["timestamp"] == "2024-01-02T15:00:00Z"


@pytest.mark.django_db
def test_ema_api_known_values(auth_client, instrument, exchange) -> None:
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    for offset, close in enumerate(("1", "2", "3", "4", "5")):
        _candle(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=start + timedelta(minutes=offset),
            close=close,
        )
    points = _results(
        auth_client.get(
            EMA_URL,
            {"instrument_id": instrument.pk, "timeframe": "1m", "window": 3},
        )
    )[0]["results"]
    assert isinstance(points, list)
    assert points[0]["value"] is None
    assert points[1]["value"] is None
    _close(points[2]["value"], 2)
    _close(points[3]["value"], 3)
    _close(points[4]["value"], 4)


@pytest.mark.django_db
def test_macd_api_known_values_and_defaults(auth_client, instrument, exchange) -> None:
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    for offset, close in enumerate(("1", "2", "3", "4", "5", "6", "7", "8")):
        _candle(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_HOUR,
            bucket_start=start + timedelta(hours=offset),
            close=close,
        )
    series = _results(
        auth_client.get(
            MACD_URL,
            {
                "instrument_name": "Acme Industrial",
                "exchange_code": "XNYS",
                "timeframe": "1h",
                "fast_period": 2,
                "slow_period": 4,
                "signal_period": 2,
            },
        )
    )[0]
    points = series["results"]
    assert isinstance(points, list)
    _close(points[3]["macd"], 1)
    assert points[3]["histogram"] is None
    _close(points[4]["histogram"], 0)
    defaults = _results(
        auth_client.get(MACD_URL, {"instrument_id": instrument.pk, "timeframe": "1h"})
    )[0]["parameters"]
    assert defaults == {
        "fast_period": 12,
        "slow_period": 26,
        "signal_period": 9,
        "include_incomplete": False,
    }


@pytest.mark.django_db
def test_start_uses_lookback_without_returning_it(auth_client, instrument, exchange) -> None:
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    stamps = [
        start,
        start + timedelta(minutes=1),
        start + timedelta(minutes=2),
        start + timedelta(minutes=3),
    ]
    for stamp, close in zip(stamps, ("2", "4", "6", "8"), strict=True):
        _candle(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=stamp,
            close=close,
        )
    points = _results(
        auth_client.get(
            SMA_URL,
            {
                "instrument_id": instrument.pk,
                "timeframe": "1m",
                "window": 3,
                "start": stamps[2].isoformat(),
            },
        )
    )[0]["results"]
    assert isinstance(points, list)
    assert [point["timestamp"] for point in points] == [
        "2024-01-02T15:02:00Z",
        "2024-01-02T15:03:00Z",
    ]
    _close(points[0]["value"], 4)
    _close(points[1]["value"], 6)


@pytest.mark.django_db
def test_incomplete_candles_are_excluded_unless_requested(
    auth_client, instrument, exchange
) -> None:
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    for offset, close in enumerate(("1", "2", "3")):
        _candle(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=start + timedelta(minutes=offset),
            close=close,
        )
    _candle(
        instrument=instrument,
        exchange=exchange,
        timeframe=CandleTimeframe.ONE_MINUTE,
        bucket_start=start + timedelta(minutes=3),
        close="9",
        is_complete=False,
    )
    complete = _results(
        auth_client.get(SMA_URL, {"instrument_id": instrument.pk, "timeframe": "1m", "window": 3})
    )[0]
    assert _mapping(complete["parameters"])["include_incomplete"] is False
    assert [point["value"] for point in _points(complete["results"])] == [None, None, 2]
    included = _points(
        _results(
            auth_client.get(
                SMA_URL,
                {
                    "instrument_id": instrument.pk,
                    "timeframe": "1m",
                    "window": 3,
                    "include_incomplete": "true",
                },
            )
        )[0]["results"]
    )
    _close(included[3]["value"], (2 + 3 + 9) / 3)


@pytest.mark.django_db
def test_corrected_candle_is_recalculated_on_next_read(auth_client, instrument, exchange) -> None:
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    candles = [
        _candle(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=start + timedelta(minutes=offset),
            close=close,
        )
        for offset, close in enumerate(("1", "2", "3", "4"))
    ]
    params = {"instrument_id": instrument.pk, "timeframe": "1m", "window": 3}
    before = _points(_results(auth_client.get(SMA_URL, params))[0]["results"])
    assert before[2]["value"] == 2
    candles[3].close = Decimal(10)
    candles[3].high = Decimal(10)
    candles[3].save(update_fields=["close", "high"])
    after = _points(_results(auth_client.get(SMA_URL, params))[0]["results"])
    assert after[2]["value"] == 2
    _close(after[3]["value"], 5)


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
        _candle(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=stamp,
            close=close,
        )
        _candle(
            instrument=other_instrument,
            exchange=other_instrument.exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=stamp,
            close=str(int(close) * 10),
        )
    series = _results(
        auth_client.get(
            SMA_URL,
            {"portfolio_id": str(portfolio.pk), "timeframe": "1m", "window": 3},
        )
    )
    assert [_mapping(item["instrument"])["symbol"] for item in series] == ["ACME", "BETA"]
    assert _points(series[0]["results"])[2]["value"] == 2
    assert _points(series[1]["results"])[2]["value"] == 20


@pytest.mark.django_db
def test_foreign_portfolio_is_not_found(auth_client, other_user) -> None:
    portfolio = Portfolio.objects.create(owner=other_user, name="Private")
    response = auth_client.get(SMA_URL, {"portfolio_id": str(portfolio.pk), "timeframe": "1m"})
    assert response.status_code == 404
    assert response.json()["success"] is False


@pytest.mark.django_db
def test_shared_candles_are_readable_by_another_authenticated_user(
    other_auth_client, instrument, exchange
) -> None:
    _candle(
        instrument=instrument,
        exchange=exchange,
        timeframe=CandleTimeframe.ONE_DAY,
        bucket_start=datetime(2024, 1, 2, tzinfo=UTC),
        close="10",
    )
    series = _results(
        other_auth_client.get(
            SMA_URL, {"instrument_symbol": "ACME", "exchange_code": "XNYS", "timeframe": "1d"}
        )
    )
    assert _mapping(series[0]["parameters"])["window"] == 20
    assert _points(series[0]["results"])[0]["value"] is None


@pytest.mark.django_db
def test_indicator_get_does_not_write_audit(auth_client, instrument, exchange) -> None:
    _candle(
        instrument=instrument,
        exchange=exchange,
        timeframe=CandleTimeframe.ONE_MINUTE,
        bucket_start=datetime(2024, 1, 2, 15, 0, tzinfo=UTC),
        close="10",
    )
    before = AuditEvent.objects.count()
    response = auth_client.get(SMA_URL, {"instrument_id": instrument.pk, "timeframe": "1m"})
    assert response.status_code == 200
    assert AuditEvent.objects.count() == before


@pytest.mark.django_db
def test_daily_ohlcv_is_not_an_indicator_source(auth_client, instrument) -> None:
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
    series = _results(auth_client.get(SMA_URL, {"instrument_id": instrument.pk, "timeframe": "1d"}))
    assert series == []


@pytest.mark.django_db
def test_indicator_validation_and_authentication(auth_client, instrument) -> None:
    denied = APIClient().get(SMA_URL, {"instrument_id": instrument.pk, "timeframe": "1m"})
    assert isinstance(denied, Response)
    assert denied.status_code == 401
    missing = auth_client.get(EMA_URL, {"timeframe": "1m"})
    assert missing.status_code == 400
    window = auth_client.get(
        SMA_URL, {"instrument_id": instrument.pk, "timeframe": "1m", "window": 1}
    )
    assert window.status_code == 400
    periods = auth_client.get(
        MACD_URL,
        {"instrument_id": instrument.pk, "timeframe": "1m", "fast_period": 20, "slow_period": 10},
    )
    assert periods.status_code == 400
    span = auth_client.get(
        SMA_URL,
        {
            "instrument_id": instrument.pk,
            "timeframe": "1m",
            "start": "2020-01-01T00:00:00Z",
            "end": "2030-02-01T00:00:00Z",
        },
    )
    assert span.status_code == 400
    ordered = auth_client.get(
        EMA_URL,
        {
            "instrument_id": instrument.pk,
            "timeframe": "1m",
            "start": "2024-01-03T00:00:00Z",
            "end": "2024-01-01T00:00:00Z",
        },
    )
    assert ordered.status_code == 400


@pytest.mark.django_db
def test_observation_limit(auth_client, instrument, exchange, monkeypatch) -> None:
    monkeypatch.setattr("apps.analytics.services.trend.MAX_INDICATOR_CANDLES", 1)
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    for offset in range(2):
        _candle(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=start + timedelta(minutes=offset),
            close="1",
        )
    response = auth_client.get(
        SMA_URL, {"instrument_id": instrument.pk, "timeframe": "1m", "window": 2}
    )
    assert response.status_code == 400
    assert response.json()["success"] is False


@pytest.mark.django_db
def test_sma_queries_stay_bounded(
    auth_client, user, instrument, exchange, django_assert_max_num_queries
) -> None:
    portfolio = Portfolio.objects.create(owner=user, name="Core")
    PortfolioInstrument.objects.create(
        portfolio=portfolio, instrument=instrument, exchange=exchange
    )
    start = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    for offset in range(3):
        _candle(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_MINUTE,
            bucket_start=start + timedelta(minutes=offset),
            close=str(offset + 1),
        )
    with django_assert_max_num_queries(10):
        response = auth_client.get(
            SMA_URL, {"portfolio_id": str(portfolio.pk), "timeframe": "1m", "window": 3}
        )
    assert response.status_code == 200
