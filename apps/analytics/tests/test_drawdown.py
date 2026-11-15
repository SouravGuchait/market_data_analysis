"""Running drawdown and maximum drawdown formula and API tests."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from math import isclose

import pytest
from django.http import HttpResponse
from django.urls import reverse
from rest_framework.test import APIClient

from apps.analytics.services.metrics_calculator import (
    drawdown_series,
    maximum_drawdown,
)
from apps.audit.models import AuditEvent
from apps.instruments.models import Instrument
from apps.market_data.models import Candle, CandleTimeframe, DailyOHLCV
from apps.portfolios.models import Portfolio, PortfolioInstrument

DRAWDOWN_URL = reverse("analytics-drawdown")


def _close(response_value: object, expected: float) -> None:
    assert isinstance(response_value, int | float)
    assert not isinstance(response_value, bool)
    assert isclose(float(response_value), expected, rel_tol=1e-12)


def test_drawdown_peak_to_trough_example() -> None:
    values = drawdown_series([100.0, 120.0, 90.0, 108.0])
    assert values[0] == (0.0, 100.0)
    assert values[1] == (0.0, 120.0)
    _close(values[2][0], 90 / 120 - 1)
    _close(values[3][0], 108 / 120 - 1)
    _close(maximum_drawdown([item[0] for item in values]), 90 / 120 - 1)


def test_drawdown_recovery_after_trough() -> None:
    values = drawdown_series([100.0, 80.0, 95.0])
    _close(values[1][0], -0.2)
    _close(values[2][0], -0.05)
    _close(maximum_drawdown([item[0] for item in values]), -0.2)


def test_drawdown_new_running_peak_resets_to_zero() -> None:
    values = drawdown_series([100.0, 90.0, 110.0])
    _close(values[1][0], -0.1)
    assert values[2] == (0.0, 110.0)


def test_drawdown_flat_prices_have_zero_drawdown() -> None:
    values = drawdown_series([50.0, 50.0, 50.0])
    assert [item[0] for item in values] == [0.0, 0.0, 0.0]
    assert maximum_drawdown([item[0] for item in values]) == 0.0


def test_drawdown_skips_invalid_closes() -> None:
    values = drawdown_series([100.0, None, 0.0, -1.0, 80.0])
    assert values[0] == (0.0, 100.0)
    assert values[1][0] is None
    assert values[2][0] is None
    assert values[3][0] is None
    _close(values[4][0], -0.2)
    assert values[4][1] == 100.0


def test_drawdown_empty_and_insufficient() -> None:
    assert drawdown_series([]) == []
    assert maximum_drawdown([]) is None
    assert maximum_drawdown([None, None]) is None
    single = drawdown_series([42.0])
    assert single == [(0.0, 42.0)]
    assert maximum_drawdown([item[0] for item in single]) == 0.0


def test_drawdown_no_lookahead() -> None:
    original = drawdown_series([100.0, 90.0, 95.0])
    changed = drawdown_series([100.0, 90.0, 50.0])
    assert original[1] == changed[1]


def _ohlcv(
    instrument: Instrument,
    trading_date: date,
    close: str,
    *,
    adjusted_close: str | None = None,
) -> DailyOHLCV:
    return DailyOHLCV.objects.create(
        instrument=instrument,
        trading_date=trading_date,
        timestamp=datetime(trading_date.year, trading_date.month, trading_date.day, 21, tzinfo=UTC),
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        adjusted_close=None if adjusted_close is None else Decimal(adjusted_close),
        volume=100,
    )


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _results(response: HttpResponse) -> list[dict[str, object]]:
    assert response.status_code == 200, response.content
    body = _mapping(json.loads(response.content.decode()))
    assert body["success"] is True
    data = _mapping(body["data"])
    results = data["results"]
    assert isinstance(results, list)
    return [_mapping(item) for item in results]


@pytest.mark.django_db
def test_drawdown_api_peak_to_trough_and_period_mdd(auth_client, instrument) -> None:
    _ohlcv(instrument, date(2024, 1, 2), "100")
    _ohlcv(instrument, date(2024, 1, 3), "120")
    _ohlcv(instrument, date(2024, 1, 4), "90")
    _ohlcv(instrument, date(2024, 1, 5), "108")
    series = _results(
        auth_client.get(
            DRAWDOWN_URL,
            {
                "instrument_symbol": "ACME",
                "exchange_code": "XNYS",
                "start_date": "2024-01-02",
                "end_date": "2024-01-05",
            },
        )
    )
    assert len(series) == 1
    payload = series[0]
    assert payload["metric"] == "drawdown"
    assert payload["source"] == "daily_ohlcv"
    assert payload["output_unit"] == "fractional_return"
    _close(payload["maximum_drawdown"], 90 / 120 - 1)
    points = payload["results"]
    assert isinstance(points, list)
    assert points[0]["value"] == 0.0
    assert points[1]["value"] == 0.0
    _close(points[2]["value"], 90 / 120 - 1)
    _close(points[3]["value"], 108 / 120 - 1)


@pytest.mark.django_db
def test_drawdown_prefers_adjusted_close(auth_client, instrument) -> None:
    _ohlcv(instrument, date(2024, 1, 2), "10", adjusted_close="100")
    _ohlcv(instrument, date(2024, 1, 3), "11", adjusted_close="80")
    series = _results(auth_client.get(DRAWDOWN_URL, {"instrument_id": instrument.pk}))[0]
    _close(series["maximum_drawdown"], -0.2)
    points = series["results"]
    assert isinstance(points, list)
    assert Decimal(str(points[0]["close"])) == Decimal(100)
    assert Decimal(str(points[1]["close"])) == Decimal(80)


@pytest.mark.django_db
def test_drawdown_period_reset_ignores_earlier_history(auth_client, instrument) -> None:
    _ohlcv(instrument, date(2024, 1, 2), "200")
    _ohlcv(instrument, date(2024, 1, 3), "100")
    _ohlcv(instrument, date(2024, 1, 4), "90")
    series = _results(
        auth_client.get(
            DRAWDOWN_URL,
            {
                "instrument_id": instrument.pk,
                "start_date": "2024-01-03",
                "end_date": "2024-01-04",
            },
        )
    )[0]
    points = series["results"]
    assert isinstance(points, list)
    assert len(points) == 2
    assert points[0]["value"] == 0.0
    _close(points[1]["value"], -0.1)
    _close(series["maximum_drawdown"], -0.1)


@pytest.mark.django_db
def test_drawdown_multiple_symbols_stay_separate(
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
    _ohlcv(instrument, date(2024, 1, 2), "100")
    _ohlcv(instrument, date(2024, 1, 3), "80")
    _ohlcv(other_instrument, date(2024, 1, 2), "50")
    _ohlcv(other_instrument, date(2024, 1, 3), "40")
    series = _results(auth_client.get(DRAWDOWN_URL, {"portfolio_id": str(portfolio.pk)}))
    assert [_mapping(item["instrument"])["symbol"] for item in series] == ["ACME", "BETA"]
    _close(series[0]["maximum_drawdown"], -0.2)
    _close(series[1]["maximum_drawdown"], -0.2)


@pytest.mark.django_db
def test_drawdown_does_not_use_trade_derived_candles(auth_client, instrument, exchange) -> None:
    Candle.objects.create(
        instrument=instrument,
        exchange=exchange,
        timeframe=CandleTimeframe.ONE_DAY,
        bucket_start=datetime(2024, 1, 2, tzinfo=UTC),
        bucket_end=datetime(2024, 1, 3, tzinfo=UTC),
        open=Decimal(100),
        high=Decimal(100),
        low=Decimal(100),
        close=Decimal(100),
        volume=Decimal(1),
        trade_count=1,
        is_complete=True,
    )
    assert _results(auth_client.get(DRAWDOWN_URL, {"instrument_id": instrument.pk})) == []


@pytest.mark.django_db
def test_drawdown_get_does_not_write_audit(auth_client, instrument) -> None:
    _ohlcv(instrument, date(2024, 1, 2), "100")
    before = AuditEvent.objects.count()
    response = auth_client.get(DRAWDOWN_URL, {"instrument_id": instrument.pk})
    assert response.status_code == 200
    assert AuditEvent.objects.count() == before


@pytest.mark.django_db
def test_drawdown_auth_and_validation(auth_client, instrument, other_user) -> None:
    denied = APIClient().get(DRAWDOWN_URL, {"instrument_id": instrument.pk})
    assert isinstance(denied, HttpResponse)
    assert denied.status_code == 401
    missing = auth_client.get(DRAWDOWN_URL)
    assert isinstance(missing, HttpResponse)
    assert missing.status_code == 400
    ordered = auth_client.get(
        DRAWDOWN_URL,
        {
            "instrument_id": instrument.pk,
            "start_date": "2024-01-10",
            "end_date": "2024-01-01",
        },
    )
    assert isinstance(ordered, HttpResponse)
    assert ordered.status_code == 400
    portfolio = Portfolio.objects.create(owner=other_user, name="Private")
    foreign = auth_client.get(DRAWDOWN_URL, {"portfolio_id": str(portfolio.pk)})
    assert isinstance(foreign, HttpResponse)
    assert foreign.status_code == 404
