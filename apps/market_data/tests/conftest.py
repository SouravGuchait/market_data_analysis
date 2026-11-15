"""Fixtures for market-data tick API tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.core.cache import caches
from rest_framework.test import APIClient

from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from apps.market_data.models import Trade
from apps.portfolios.models import Portfolio

TEST_PASSWORD = "Tick!Test-Password_016A"


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    caches["default"].clear()


@pytest.fixture
def user(db) -> User:
    return User.objects.create_user(
        username="tick_alice",
        email="tick_alice@example.com",
        password=TEST_PASSWORD,
    )


@pytest.fixture
def other_user(db) -> User:
    return User.objects.create_user(
        username="tick_bob",
        email="tick_bob@example.com",
        password=TEST_PASSWORD,
    )


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def auth_client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def exchange(db) -> Exchange:
    return Exchange.objects.create(
        code="XNYS",
        name="New York Stock Exchange",
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
def trade(instrument, exchange) -> Trade:
    return Trade.objects.create(
        instrument=instrument,
        exchange=exchange,
        event_timestamp=datetime(2024, 1, 2, 15, 0, tzinfo=UTC),
        price=Decimal("10.500000"),
        quantity=Decimal("100.00000000"),
        exchange_trade_id="ACME-2024-01-02-0001",
        sequence_number=1,
        ingestion_timestamp=datetime(2024, 1, 2, 16, 0, tzinfo=UTC),
        deduplication_key="dedupe-1",
    )


@pytest.fixture
def portfolio(user) -> Portfolio:
    return Portfolio.objects.create(owner=user, name="Ticks")


@pytest.fixture
def other_portfolio(other_user) -> Portfolio:
    return Portfolio.objects.create(owner=other_user, name="Private")
