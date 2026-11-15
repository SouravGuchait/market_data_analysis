"""Fixtures for dashboard API tests."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.cache import caches
from rest_framework.test import APIClient

from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from apps.portfolios.models import Portfolio, PortfolioInstrument

TEST_PASSWORD = "Dashboard!Test-Password_016A"


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    caches["default"].clear()


@pytest.fixture
def user(db) -> User:
    return User.objects.create_user(
        username="dash_alice",
        email="dash_alice@example.com",
        password=TEST_PASSWORD,
    )


@pytest.fixture
def other_user(db) -> User:
    return User.objects.create_user(
        username="dash_bob",
        email="dash_bob@example.com",
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
def portfolio(user, instrument, exchange) -> Portfolio:
    portfolio = Portfolio.objects.create(owner=user, name="Core", description="Main")
    PortfolioInstrument.objects.create(
        portfolio=portfolio,
        instrument=instrument,
        exchange=exchange,
        is_active=True,
    )
    return portfolio


@pytest.fixture
def other_portfolio(other_user) -> Portfolio:
    return Portfolio.objects.create(owner=other_user, name="Foreign")
