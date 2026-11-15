"""API tests for bounded tick listing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

TICKS_URL = "/api/v1/market-data/ticks/"


def test_ticks_require_authentication(api_client) -> None:
    assert api_client.get(TICKS_URL).status_code == 401


def test_ticks_return_nested_instrument_and_exchange(auth_client, trade) -> None:
    response = auth_client.get(
        TICKS_URL,
        {
            "instrument_symbol": "ACME",
            "exchange_code": "XNYS",
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-01-03T00:00:00Z",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["count"] == 1
    row = payload["data"]["results"][0]
    assert row["exchange_trade_id"] == trade.exchange_trade_id
    assert row["instrument"]["symbol"] == "ACME"
    assert row["exchange"]["code"] == "XNYS"
    assert "price" in row


def test_ticks_reject_oversized_date_range(auth_client, instrument) -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=40)
    response = auth_client.get(
        TICKS_URL,
        {
            "instrument_id": instrument.pk,
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
        },
    )
    assert response.status_code == 400
    assert response.json()["success"] is False


def test_ticks_foreign_portfolio_returns_404(auth_client, other_portfolio) -> None:
    response = auth_client.get(TICKS_URL, {"portfolio_id": str(other_portfolio.pk)})
    assert response.status_code == 404
