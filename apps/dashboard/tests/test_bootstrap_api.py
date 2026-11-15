"""API tests for dashboard bootstrap."""

from __future__ import annotations

BOOTSTRAP_URL = "/api/v1/dashboard/bootstrap/"


def test_bootstrap_requires_authentication(api_client) -> None:
    assert api_client.get(BOOTSTRAP_URL).status_code == 401


def test_bootstrap_returns_user_portfolios_and_defaults(
    auth_client, user, portfolio, exchange, instrument
) -> None:
    response = auth_client.get(BOOTSTRAP_URL)
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    data = payload["data"]
    assert data["user"]["username"] == user.username
    assert data["defaults"]["timeframe"] == "1d"
    assert any(item["id"] == str(portfolio.pk) for item in data["portfolios"])
    assert any(item["code"] == exchange.code for item in data["exchanges"])
    assert any(item["symbol"] == instrument.symbol for item in data["instruments"])


def test_bootstrap_scopes_instruments_to_owned_portfolio(
    auth_client, portfolio, other_portfolio
) -> None:
    ok = auth_client.get(BOOTSTRAP_URL, {"portfolio_id": str(portfolio.pk)})
    assert ok.status_code == 200
    assert len(ok.json()["data"]["instruments"]) == 1

    missing = auth_client.get(BOOTSTRAP_URL, {"portfolio_id": str(other_portfolio.pk)})
    assert missing.status_code == 404
    assert missing.json()["success"] is False
