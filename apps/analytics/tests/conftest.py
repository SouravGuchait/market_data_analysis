"""Fixtures for analytics aggregation and returns tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.core.cache import caches
from rest_framework.test import APIClient

from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from apps.market_data.models import Trade

TEST_PASSWORD = "Analytics!Test-Password_008"


@pytest.fixture(autouse=True)
def clear_throttle_cache() -> None:
    caches["default"].clear()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="analyst",
        email="analyst@example.com",
        password=TEST_PASSWORD,
    )


@pytest.fixture
def other_user(db):
    return User.objects.create_user(
        username="other",
        email="other@example.com",
        password=TEST_PASSWORD,
    )


@pytest.fixture
def auth_client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def other_auth_client(other_user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=other_user)
    return client


@pytest.fixture
def exchange(db) -> Exchange:
    return Exchange.objects.create(
        code="XNYS",
        name="NYSE",
        timezone="America/New_York",
    )


@pytest.fixture
def other_exchange(db) -> Exchange:
    return Exchange.objects.create(
        code="XNAS",
        name="Nasdaq",
        timezone="America/New_York",
    )


@pytest.fixture
def instrument(exchange) -> Instrument:
    return Instrument.objects.create(
        exchange=exchange,
        symbol="ACME",
        name="Acme Industrial",
        asset_class=Instrument.AssetClass.EQUITY,
        currency=Instrument.Currency.USD,
    )


@pytest.fixture
def other_instrument(other_exchange) -> Instrument:
    return Instrument.objects.create(
        exchange=other_exchange,
        symbol="BETA",
        name="Beta Corp",
        asset_class=Instrument.AssetClass.EQUITY,
        currency=Instrument.Currency.USD,
    )


def make_trade(
    *,
    instrument: Instrument,
    exchange: Exchange,
    event_timestamp: datetime,
    price: str,
    quantity: str = "1.5",
    sequence_number: int | None = None,
    exchange_trade_id: str = "",
) -> Trade:
    return Trade.objects.create(
        instrument=instrument,
        exchange=exchange,
        event_timestamp=event_timestamp,
        price=Decimal(price),
        quantity=Decimal(quantity),
        sequence_number=sequence_number,
        exchange_trade_id=exchange_trade_id,
        ingestion_timestamp=datetime(2024, 1, 2, 12, 0, tzinfo=UTC),
        deduplication_key=uuid4().hex,
    )
