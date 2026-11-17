"""Fixtures for the user-owned validation configuration API."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.cache import caches
from rest_framework.test import APIClient

TEST_PASSWORD = "Rule!Test-Password_9274"


@pytest.fixture(autouse=True)
def clear_throttle_cache() -> None:
    caches["default"].clear()


@pytest.fixture
def user(db) -> User:
    return User.objects.create_user(
        username="rule-owner",
        email="rule-owner@example.com",
        password=TEST_PASSWORD,
    )


@pytest.fixture
def other_user(db) -> User:
    return User.objects.create_user(
        username="other-rule-owner",
        email="other-rule-owner@example.com",
        password=TEST_PASSWORD,
    )


@pytest.fixture
def staff_user(db) -> User:
    return User.objects.create_user(
        username="rule-admin",
        email="rule-admin@example.com",
        password=TEST_PASSWORD,
        is_staff=True,
    )


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def auth_client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def other_auth_client(other_user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=other_user)
    return client


@pytest.fixture
def staff_auth_client(staff_user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=staff_user)
    return client
