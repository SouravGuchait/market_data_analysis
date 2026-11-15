"""Dashboard bootstrap API coverage for reference choices."""

from __future__ import annotations

BOOTSTRAP_URL = "/api/v1/dashboard/bootstrap/"


def test_bootstrap_requires_authentication(api_client) -> None:
    assert api_client.get(BOOTSTRAP_URL).status_code == 401


def test_bootstrap_includes_instrument_choices_from_db(auth_client, instrument) -> None:
    response = auth_client.get(BOOTSTRAP_URL)
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    choices = payload["data"]["instrument_choices"]
    assert instrument.asset_class in choices["asset_classes"]
    assert instrument.currency in choices["currencies"]
