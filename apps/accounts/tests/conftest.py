"""Shared pytest fixtures for account tests."""

from __future__ import annotations

import pytest
from django.core.cache import caches


@pytest.fixture(autouse=True)
def clear_throttle_cache() -> None:
    """Keep rate-limit counters from leaking across independent tests."""

    caches["default"].clear()
