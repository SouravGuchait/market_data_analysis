"""Fixtures for audit feature tests."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.cache import caches
from rest_framework.test import APIClient

from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument

TEST_PASSWORD = "Audit!Test-Password_9274"


@pytest.fixture(autouse=True)
def clear_throttle_cache() -> None:
    caches["default"].clear()


@pytest.fixture
def user(db) -> User:
    return User.objects.create_user(
        username="audit-user",
        email="audit-user@example.com",
        password=TEST_PASSWORD,
    )


@pytest.fixture
def other_user(db) -> User:
    return User.objects.create_user(
        username="audit-other",
        email="audit-other@example.com",
        password=TEST_PASSWORD,
    )


@pytest.fixture
def staff_user(db) -> User:
    return User.objects.create_user(
        username="audit-admin",
        email="audit-admin@example.com",
        password=TEST_PASSWORD,
        is_staff=True,
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
def staff_auth_client(staff_user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=staff_user)
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
