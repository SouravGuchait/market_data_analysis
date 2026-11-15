"""Unit tests for Feature 015 cache helpers and generation counters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache

from apps.common.cache_service import (
    MARKET_VERSION_KEY,
    bump_market_version,
    bump_portfolio_detail_version,
    bump_portfolio_list_version,
    cache_get,
    cache_set,
    get_or_build,
    is_recent_range,
    market_key,
    portfolio_detail_key,
    portfolio_list_key,
    query_hash,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    cache.clear()


def test_query_hash_is_stable_for_key_order() -> None:
    left = query_hash({"b": 2, "a": 1})
    right = query_hash({"a": 1, "b": 2})
    assert left == right


def test_market_key_changes_when_generation_bumps() -> None:
    first = market_key("candles", {"instrument_ids": [1]})
    bump_market_version()
    second = market_key("candles", {"instrument_ids": [1]})
    assert first != second


def test_portfolio_keys_are_user_scoped() -> None:
    alice_list = portfolio_list_key(1, {"page": "1"})
    bob_list = portfolio_list_key(2, {"page": "1"})
    assert alice_list != bob_list
    alice_detail = portfolio_detail_key(1, "p1")
    bob_detail = portfolio_detail_key(2, "p1")
    assert alice_detail != bob_detail


def test_portfolio_list_bump_isolates_from_detail() -> None:
    list_before = portfolio_list_key(9, {"page": "1"})
    detail_before = portfolio_detail_key(9, "abc")
    bump_portfolio_list_version(9)
    assert portfolio_list_key(9, {"page": "1"}) != list_before
    assert portfolio_detail_key(9, "abc") == detail_before


def test_portfolio_detail_bump_isolates_from_list() -> None:
    list_before = portfolio_list_key(9, {"page": "1"})
    detail_before = portfolio_detail_key(9, "abc")
    bump_portfolio_detail_version(9, "abc")
    assert portfolio_detail_key(9, "abc") != detail_before
    assert portfolio_list_key(9, {"page": "1"}) == list_before


def test_get_or_build_returns_cached_payload() -> None:
    calls = {"n": 0}

    def builder() -> dict[str, object]:
        calls["n"] += 1
        return {"success": True, "message": "ok", "data": {"v": 1}, "errors": None}

    first = get_or_build("unit:key", 60, builder)
    second = get_or_build("unit:key", 60, builder)
    assert first == second
    assert calls["n"] == 1


def test_cache_get_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.common.cache_service.cache.get",
        MagicMock(side_effect=ConnectionError("down")),
    )
    assert cache_get("any") is None


def test_cache_set_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.common.cache_service.cache.set",
        MagicMock(side_effect=ConnectionError("down")),
    )
    cache_set("any", {"ok": True}, 30)


def test_bump_recovers_when_incr_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.common.cache_service.cache.incr",
        MagicMock(side_effect=ConnectionError("down")),
    )
    with patch("apps.common.cache_service.cache.set") as set_mock:
        bump_market_version()
        set_mock.assert_called()
        assert set_mock.call_args.args[0] == MARKET_VERSION_KEY


def test_is_recent_range_none_is_recent() -> None:
    assert is_recent_range(None) is True


def test_is_recent_range_old_end_is_historical() -> None:
    old = datetime.now(UTC) - timedelta(days=30)
    assert is_recent_range(old) is False
