"""API tests for combined analytics overview."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from rest_framework.response import Response
from rest_framework.test import APIClient

from apps.market_data.models import Candle, CandleTimeframe, DailyOHLCV

OVERVIEW_URL = "/api/v1/analytics/overview/"


@pytest.mark.django_db
def test_overview_requires_authentication() -> None:
    denied = APIClient().get(OVERVIEW_URL, {})
    assert isinstance(denied, Response)
    assert denied.status_code == 401


@pytest.mark.django_db
def test_overview_returns_requested_metrics(auth_client, instrument, exchange) -> None:
    for index, close in enumerate((100, 101, 102, 103, 104), start=1):
        trading = date(2024, 1, index)
        DailyOHLCV.objects.create(
            instrument=instrument,
            trading_date=trading,
            timestamp=datetime(2024, 1, index, 21, 0, tzinfo=UTC),
            open=Decimal(close),
            high=Decimal(close + 1),
            low=Decimal(close - 1),
            close=Decimal(close),
            volume=1000,
        )
        Candle.objects.create(
            instrument=instrument,
            exchange=exchange,
            timeframe=CandleTimeframe.ONE_DAY,
            bucket_start=datetime(2024, 1, index, tzinfo=UTC),
            bucket_end=datetime(2024, 1, index + 1, tzinfo=UTC),
            open=Decimal(close),
            high=Decimal(close + 1),
            low=Decimal(close - 1),
            close=Decimal(close),
            volume=Decimal(1000),
            trade_count=10,
            is_complete=True,
        )

    response = auth_client.get(
        OVERVIEW_URL,
        {
            "instrument_symbol": instrument.symbol,
            "exchange_code": exchange.code,
            "metrics": "sma,returns,drawdown",
            "timeframe": "1d",
            "window": 2,
            "start_date": "2024-01-01",
            "end_date": "2024-01-05",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    names = [block["metric"] for block in payload["data"]["results"]]
    assert names == ["sma", "returns", "drawdown"]
    for block in payload["data"]["results"]:
        assert "observation_count" in block
        assert "source" in block


@pytest.mark.django_db
def test_overview_rejects_unknown_metrics(auth_client, instrument) -> None:
    response = auth_client.get(
        OVERVIEW_URL,
        {
            "instrument_id": instrument.pk,
            "metrics": "obv",
            "timeframe": "1d",
        },
    )
    assert response.status_code == 400
    assert response.json()["success"] is False
