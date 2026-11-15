"""API tests for daily OHLCV listing."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from apps.market_data.models import DailyOHLCV

DAILY_URL = "/api/v1/market-data/daily-ohlcv/"


@pytest.fixture
def daily_bar(instrument) -> DailyOHLCV:
    return DailyOHLCV.objects.create(
        instrument=instrument,
        trading_date=date(2024, 1, 2),
        timestamp=datetime(2024, 1, 2, 21, 0, tzinfo=UTC),
        open=Decimal("10.00"),
        high=Decimal("12.00"),
        low=Decimal("9.50"),
        close=Decimal("11.25"),
        volume=Decimal(1000),
    )


def test_daily_ohlcv_require_authentication(api_client) -> None:
    assert api_client.get(DAILY_URL).status_code == 401


def test_daily_ohlcv_return_nested_relations(auth_client, daily_bar) -> None:
    response = auth_client.get(
        DAILY_URL,
        {
            "instrument_symbol": "ACME",
            "exchange_code": "XNYS",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["count"] == 1
    row = payload["data"]["results"][0]
    assert row["trading_date"] == "2024-01-02"
    assert row["close"] == "11.250000"
    assert row["instrument"]["symbol"] == "ACME"
    assert row["exchange"]["code"] == "XNYS"
    assert "volume" in row


def test_daily_ohlcv_reject_oversized_range(auth_client, instrument) -> None:
    start = date(2024, 1, 1)
    end = start + timedelta(days=400)
    response = auth_client.get(
        DAILY_URL,
        {
            "instrument_id": instrument.pk,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
    )
    assert response.status_code == 400
    assert response.json()["success"] is False


def test_daily_ohlcv_foreign_portfolio_returns_404(auth_client, other_portfolio) -> None:
    response = auth_client.get(DAILY_URL, {"portfolio_id": str(other_portfolio.pk)})
    assert response.status_code == 404
