"""Shared deterministic test fixtures."""

from __future__ import annotations

import pytest

from apps.accounts.models import User
from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument


@pytest.fixture
def user(db: None) -> User:
    return User.objects.create_user(
        username="alice",
        email="alice@example.com",
        password="StrongPass123!",
    )


@pytest.fixture
def exchange(db: None) -> Exchange:
    return Exchange.objects.create(code="XNAS", name="NASDAQ", timezone="America/New_York")


@pytest.fixture
def instrument(exchange: Exchange) -> Instrument:
    return Instrument.objects.create(
        exchange=exchange,
        symbol="AAPL",
        name="Apple Inc.",
        asset_class=Instrument.AssetClass.EQUITY,
        currency="USD",
    )
