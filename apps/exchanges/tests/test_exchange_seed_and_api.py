"""Exchange seeding and the shared exchange list API."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from rest_framework import status
from rest_framework.test import APIClient

from apps.exchanges.models import Exchange
from scripts.generate_market_data import EXCHANGES

EXCHANGES_URL = "/api/v1/exchanges/"


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def auth_client(db) -> APIClient:
    user = User.objects.create_user(username="alice", password="Exchange!Test-Password_9274")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_seed_exchanges_updates_metadata_and_preserves_unrelated_records():
    call_command("seed_exchanges")
    seeded = Exchange.objects.get(code="XNYS")
    seeded.name = "Temporary name"
    seeded.timezone = "UTC"
    seeded.is_active = False
    seeded.save(update_fields=["name", "timezone", "is_active", "updated_at"])
    custom = Exchange.objects.create(
        code="CUST", name="Custom Venue", timezone="UTC", is_active=True
    )

    call_command("seed_exchanges")

    seeded.refresh_from_db()
    custom.refresh_from_db()
    assert seeded.name == "New York Stock Exchange"
    assert seeded.timezone == "America/New_York"
    assert seeded.is_active is False
    assert custom.name == "Custom Venue"
    assert Exchange.objects.filter(code="XNYS").count() == 1
    assert Exchange.objects.count() == len(EXCHANGES) + 1


@pytest.mark.django_db
def test_exchange_list_requires_authentication(db, api_client):
    call_command("seed_exchanges")
    assert api_client.get(EXCHANGES_URL).status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_exchange_list_returns_seeded_exchanges(auth_client):
    call_command("seed_exchanges")

    response = auth_client.get(EXCHANGES_URL, {"code": "xnas"})

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["success"] is True
    results = payload["data"]["results"]
    assert len(results) == 1
    assert results[0]["code"] == "XNAS"
    assert results[0]["name"] == "Nasdaq"
    assert results[0]["timezone"] == "America/New_York"
