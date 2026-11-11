"""Shared fixtures for portfolio feature tests."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from apps.portfolios.models import Portfolio

TEST_PASSWORD = "Portfolio!Test-Password_9274"


@pytest.fixture
def user(db) -> User:
    return User.objects.create_user(
        username="alice",
        email="alice@example.com",
        password=TEST_PASSWORD,
    )


@pytest.fixture
def other_user(db) -> User:
    return User.objects.create_user(
        username="bob",
        email="bob@example.com",
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
def other_auth_client(other_user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=other_user)
    return client


@pytest.fixture
def exchange(db) -> Exchange:
    return Exchange.objects.create(
        code="XNYS",
        name="New York Stock Exchange",
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
        name="Beta Systems",
        asset_class=Instrument.AssetClass.EQUITY,
        currency=Instrument.Currency.USD,
    )


@pytest.fixture
def portfolio(user) -> Portfolio:
    return Portfolio.objects.create(
        owner=user,
        name="Retirement",
        description="Long-term holdings",
    )


@pytest.fixture
def other_portfolio(other_user) -> Portfolio:
    return Portfolio.objects.create(owner=other_user, name="Private")
