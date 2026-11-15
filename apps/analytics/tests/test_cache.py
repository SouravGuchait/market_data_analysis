"""Feature 015: analytics response cache after scope resolution."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.market_data.models import Candle, CandleTimeframe, DailyOHLCV
from apps.portfolios.models import Portfolio, PortfolioInstrument

SMA_URL = "/api/v1/analytics/sma/"
RETURNS_URL = "/api/v1/analytics/returns/"
DRAWDOWN_URL = "/api/v1/analytics/drawdown/"


def _candle(instrument, exchange, day: int) -> Candle:
    start = datetime(2024, 1, day, 15, 0, tzinfo=UTC)
    return Candle.objects.create(
        instrument=instrument,
        exchange=exchange,
        timeframe=CandleTimeframe.ONE_DAY,
        bucket_start=start,
        bucket_end=start + timedelta(days=1),
        open=Decimal(10 + day),
        high=Decimal(11 + day),
        low=Decimal(9 + day),
        close=Decimal(10 + day),
        volume=Decimal(100),
        trade_count=1,
        is_complete=True,
    )


@pytest.mark.django_db
def test_sma_cache_hit_skips_candle_query(auth_client, instrument, exchange) -> None:
    for day in range(1, 25):
        _candle(instrument, exchange, day)
    url = (
        f"{SMA_URL}?instrument_id={instrument.pk}&exchange_code={exchange.code}"
        f"&timeframe=1d&window=5&start=2024-01-01T00:00:00Z&end=2024-01-31T00:00:00Z"
    )
    first = auth_client.get(url)
    assert first.status_code == 200
    with CaptureQueriesContext(connection) as ctx:
        second = auth_client.get(url)
    assert second.status_code == 200
    assert second.json() == first.json()
    candle_queries = [q for q in ctx.captured_queries if "mda_candles" in q["sql"].lower()]
    assert candle_queries == []


@pytest.mark.django_db
def test_portfolio_scoped_analytics_404_before_cache(
    auth_client, other_auth_client, instrument, exchange, user
) -> None:
    portfolio = Portfolio.objects.create(owner=user, name="Alice only")
    PortfolioInstrument.objects.create(
        portfolio=portfolio, instrument=instrument, exchange=exchange
    )
    for day in range(1, 10):
        _candle(instrument, exchange, day)
    url = (
        f"{SMA_URL}?portfolio_id={portfolio.pk}&timeframe=1d&window=3"
        f"&start=2024-01-01T00:00:00Z&end=2024-01-31T00:00:00Z"
    )
    assert auth_client.get(url).status_code == 200
    assert other_auth_client.get(url).status_code == 404


@pytest.mark.django_db
def test_returns_include_user_scope_when_portfolio_filter(
    auth_client, other_auth_client, instrument, exchange, user
) -> None:
    portfolio = Portfolio.objects.create(owner=user, name="Returns book")
    PortfolioInstrument.objects.create(
        portfolio=portfolio, instrument=instrument, exchange=exchange
    )
    for day in (2, 3, 4):
        close = Decimal(10 + day)
        DailyOHLCV.objects.create(
            instrument=instrument,
            trading_date=date(2024, 1, day),
            timestamp=datetime(2024, 1, day, 21, 0, tzinfo=UTC),
            open=close,
            high=close + Decimal(1),
            low=close - Decimal(1),
            close=close,
            volume=100,
        )
    url = (
        f"{RETURNS_URL}?portfolio_id={portfolio.pk}"
        f"&start_date=2024-01-01&end_date=2024-01-31&return_type=simple"
    )
    assert auth_client.get(url).status_code == 200
    assert other_auth_client.get(url).status_code == 404


@pytest.mark.django_db
def test_drawdown_market_bump_invalidates_cache(auth_client, instrument, exchange) -> None:
    from apps.common.cache_service import bump_market_version

    for day in (2, 3, 4, 5):
        close = Decimal(20 - day)
        DailyOHLCV.objects.create(
            instrument=instrument,
            trading_date=date(2024, 1, day),
            timestamp=datetime(2024, 1, day, 21, 0, tzinfo=UTC),
            open=close,
            high=close + Decimal(1),
            low=close - Decimal(1),
            close=close,
            volume=100,
        )
    url = f"{DRAWDOWN_URL}?instrument_id={instrument.pk}&start_date=2024-01-01&end_date=2024-01-31"
    first = auth_client.get(url)
    assert first.status_code == 200
    first_points = first.json()["data"]["results"][0]["results"]
    bump_market_version()
    DailyOHLCV.objects.create(
        instrument=instrument,
        trading_date=date(2024, 1, 6),
        timestamp=datetime(2024, 1, 6, 21, 0, tzinfo=UTC),
        open=Decimal(1),
        high=Decimal(2),
        low=Decimal("0.5"),
        close=Decimal(1),
        volume=100,
    )
    second = auth_client.get(url)
    assert second.status_code == 200
    second_points = second.json()["data"]["results"][0]["results"]
    assert len(second_points) == len(first_points) + 1
