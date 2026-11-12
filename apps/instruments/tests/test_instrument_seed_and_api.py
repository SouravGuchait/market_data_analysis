"""Instrument seeding and the shared instrument API."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from rest_framework import status
from rest_framework.test import APIClient

from apps.audit.models import AuditAction, AuditEvent
from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from scripts.generate_market_data import canonical_instruments

INSTRUMENTS_URL = "/api/v1/instruments/"


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def auth_client(db) -> APIClient:
    user = User.objects.create_user(username="alice", password="Instrument!Test-Password_9274")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_seed_instruments_matches_generator_catalog_and_is_idempotent():
    call_command("seed_market_data")
    catalog = canonical_instruments()
    assert Instrument.objects.count() == len(catalog)
    for row in catalog:
        instrument = Instrument.objects.get(exchange__code=row["exchange"], symbol=row["symbol"])
        assert instrument.name == row["name"]
        assert instrument.asset_class == row["asset_class"]
        assert instrument.currency == row["currency"]
    exchanges = {row["exchange"] for row in catalog}
    assert len(exchanges) > 1

    listed = Instrument.objects.get(symbol="ACME")
    listed.name = "Renamed"
    listed.is_active = False
    listed.save(update_fields=["name", "is_active", "updated_at"])
    custom = Instrument.objects.create(
        exchange=Exchange.objects.get(code="XNYS"),
        symbol="CUSTOM",
        name="Custom Instrument",
        asset_class=Instrument.AssetClass.EQUITY,
        currency=Instrument.Currency.USD,
    )

    call_command("seed_instruments")

    listed.refresh_from_db()
    custom.refresh_from_db()
    assert Instrument.objects.filter(symbol="ACME").count() == 1
    assert listed.name == "Acme Industrial"
    assert listed.is_active is False
    assert custom.name == "Custom Instrument"
    assert Instrument.objects.count() == len(catalog) + 1


@pytest.mark.django_db
def test_seed_instruments_requires_exchanges():
    with pytest.raises(CommandError, match="not seeded"):
        call_command("seed_instruments")
    assert Instrument.objects.count() == 0


@pytest.mark.django_db
def test_instrument_api_creates_lists_and_rejects_invalid_values(auth_client):
    call_command("seed_exchanges")

    created = auth_client.post(
        INSTRUMENTS_URL,
        {
            "exchange_code": "xnas",
            "symbol": "ACME",
            "name": "Acme Industrial",
            "asset_class": "equity",
            "currency": "USD",
        },
        format="json",
    )
    assert created.status_code == status.HTTP_201_CREATED
    body = created.json()["data"]
    assert body["symbol"] == "ACME"
    assert body["exchange"]["code"] == "XNAS"
    assert "id" not in body["exchange"]
    assert AuditEvent.objects.filter(action=AuditAction.INSTRUMENT_CREATED).count() == 1

    duplicate = auth_client.post(
        INSTRUMENTS_URL,
        {
            "exchange_code": "XNAS",
            "symbol": "acme",
            "name": "Acme Industrial",
            "asset_class": "equity",
            "currency": "USD",
        },
        format="json",
    )
    assert duplicate.status_code == status.HTTP_400_BAD_REQUEST
    assert "symbol" in duplicate.json()["errors"]

    unknown = auth_client.post(
        INSTRUMENTS_URL,
        {
            "exchange_code": "ZZZZ",
            "symbol": "BETA",
            "name": "Beta Materials",
            "asset_class": "equity",
            "currency": "USD",
        },
        format="json",
    )
    assert unknown.status_code == status.HTTP_400_BAD_REQUEST
    assert "exchange_code" in unknown.json()["errors"]
    assert Exchange.objects.filter(code="ZZZZ").count() == 0

    invalid = auth_client.post(
        INSTRUMENTS_URL,
        {
            "exchange_code": "XNAS",
            "symbol": "BETA",
            "name": "Beta Materials",
            "asset_class": "bond",
            "currency": "US",
        },
        format="json",
    )
    assert invalid.status_code == status.HTTP_400_BAD_REQUEST
    assert Instrument.objects.filter(symbol="BETA").count() == 0

    listed = auth_client.get(
        INSTRUMENTS_URL, {"exchange_code": "XNAS", "symbol": "acme", "search": "industrial"}
    )
    assert listed.status_code == status.HTTP_200_OK
    results = listed.json()["data"]["results"]
    assert len(results) == 1
    assert results[0]["symbol"] == "ACME"


@pytest.mark.django_db
def test_instrument_list_requires_authentication(api_client):
    assert api_client.get(INSTRUMENTS_URL).status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_instrument_list_avoids_per_row_exchange_queries(
    auth_client, django_assert_max_num_queries
):
    call_command("seed_market_data")
    with django_assert_max_num_queries(4):
        response = auth_client.get(INSTRUMENTS_URL)
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["data"]["count"] == len(canonical_instruments())
