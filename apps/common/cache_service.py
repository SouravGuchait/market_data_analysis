"""Small Redis-backed response cache with generation-counter invalidation.

Authentication and analytics scope resolution must happen before cache lookup.
Failures fail open to MySQL; version bumps recover with a forced new generation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction

logger = logging.getLogger("apps.common.cache")

MARKET_VERSION_KEY = "cache:gen:market"
CATALOG_VERSION_KEY = "cache:gen:catalog"
_PORTFOLIO_LIST_PREFIX = "cache:gen:portfolio_list:user:"
_PORTFOLIO_DETAIL_PREFIX = "cache:gen:portfolio:user:"


def query_hash(parts: Mapping[str, object]) -> str:
    """Stable short hash of sorted query dimensions."""

    payload = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _log(event: str, **fields: object) -> None:
    logger.info(event, extra=fields)


def cache_get(key: str) -> object | None:
    """Return a cached value, or None on miss or Redis failure."""

    try:
        value = cache.get(key)
    except Exception:
        logger.exception("cache_get_failed", extra={"cache_key": key})
        return None
    if value is None:
        _log("cache_miss", cache_key=key)
        return None
    _log("cache_hit", cache_key=key)
    return value


def cache_set(key: str, value: object, ttl: int) -> None:
    """Store a JSON-serializable value; log and continue on Redis failure."""

    try:
        encoded = json.dumps(value, cls=DjangoJSONEncoder, separators=(",", ":"))
        cache.set(key, encoded, timeout=ttl)
    except Exception:
        logger.exception("cache_set_failed", extra={"cache_key": key, "ttl": ttl})


def _decode_json_dict(raw: str, key: str) -> dict[str, object] | None:
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        logger.exception("cache_decode_failed", extra={"cache_key": key})
        return None
    if not isinstance(decoded, dict):
        return None
    return {str(item_key): item for item_key, item in decoded.items()}


def cache_load_dict(key: str) -> dict[str, object] | None:
    """GET and decode a JSON object payload."""

    raw = cache_get(key)
    if raw is None:
        return None
    if isinstance(raw, dict):
        return {str(item_key): item for item_key, item in raw.items()}
    return _decode_json_dict(raw, key) if isinstance(raw, str) else None


def get_or_build(
    key: str,
    ttl: int,
    builder: Callable[[], dict[str, object]],
) -> dict[str, object]:
    """Return a cached dict or build, store, and return a fresh one."""

    hit = cache_load_dict(key)
    if hit is not None:
        return hit
    payload = builder()
    cache_set(key, payload, ttl)
    return payload


def _version_value(key: str) -> int:
    value = cache.get(key)
    if isinstance(value, int):
        return value
    if value is None:
        # timeout=None => never expire (timeout=0 expires immediately).
        cache.add(key, 1, timeout=None)
        return 1
    return int(value)


def _read_version(key: str) -> int:
    try:
        return _version_value(key)
    except Exception:
        logger.exception("cache_version_read_failed", extra={"cache_key": key})
        return int(time.time())


def _bump_version(key: str, scope: str) -> None:
    try:
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=None)
        _log("cache_invalidated", scope=scope, cache_key=key)
        return
    except Exception:
        logger.exception("cache_invalidation_failure", extra={"scope": scope, "cache_key": key})
    try:
        cache.set(key, int(time.time()), timeout=None)
        _log("cache_invalidation_recovered", scope=scope, cache_key=key)
    except Exception:
        logger.exception(
            "cache_invalidation_recovery_failed",
            extra={"scope": scope, "cache_key": key},
        )


def market_version() -> int:
    return _read_version(MARKET_VERSION_KEY)


def catalog_version() -> int:
    return _read_version(CATALOG_VERSION_KEY)


def portfolio_list_version(user_id: int) -> int:
    return _read_version(f"{_PORTFOLIO_LIST_PREFIX}{user_id}")


def portfolio_detail_version(user_id: int, portfolio_id: object) -> int:
    return _read_version(f"{_PORTFOLIO_DETAIL_PREFIX}{user_id}:{portfolio_id}")


def after_commit(callback: Callable[[], None]) -> None:
    """Run after DB commit; eagerly in tests that wrap work in rolled-back transactions."""

    if bool(getattr(settings, "CACHE_BUMP_EAGER", False)):
        callback()
        return
    transaction.on_commit(callback)


def bump_market_version() -> None:
    _bump_version(MARKET_VERSION_KEY, "market")


def bump_catalog_version() -> None:
    _bump_version(CATALOG_VERSION_KEY, "catalog")


def bump_portfolio_list_version(user_id: int) -> None:
    _bump_version(f"{_PORTFOLIO_LIST_PREFIX}{user_id}", "portfolio_list")


def bump_portfolio_detail_version(user_id: int, portfolio_id: object) -> None:
    _bump_version(f"{_PORTFOLIO_DETAIL_PREFIX}{user_id}:{portfolio_id}", "portfolio_detail")


def is_recent_range(end: datetime | date | None) -> bool:
    """True when the query has no end or the end is inside the recent window."""

    if end is None:
        return True
    window = timedelta(seconds=int(settings.CACHE_RECENT_WINDOW_SECONDS))
    now = datetime.now(UTC)
    if isinstance(end, datetime):
        end_utc = end if end.tzinfo is not None else end.replace(tzinfo=UTC)
        return end_utc >= now - window
    return datetime.combine(end, datetime.min.time(), tzinfo=UTC) >= now - window


def ttl_catalog() -> int:
    return int(settings.CACHE_TTL_CATALOG)


def ttl_portfolio() -> int:
    return int(settings.CACHE_TTL_PORTFOLIO)


def ttl_candles(*, recent: bool) -> int:
    if recent:
        return int(settings.CACHE_TTL_CANDLES_RECENT)
    return int(settings.CACHE_TTL_CANDLES_HISTORICAL)


def ttl_indicators(*, recent: bool) -> int:
    if recent:
        return int(settings.CACHE_TTL_INDICATORS_RECENT)
    return int(settings.CACHE_TTL_INDICATORS_HISTORICAL)


def ttl_returns() -> int:
    return int(settings.CACHE_TTL_RETURNS)


def market_key(kind: str, parts: Mapping[str, object]) -> str:
    return f"market:v{market_version()}:{kind}:{query_hash(parts)}"


def catalog_key(kind: str, parts: Mapping[str, object]) -> str:
    return f"reference:v{catalog_version()}:{kind}:{query_hash(parts)}"


def portfolio_list_key(user_id: int, parts: Mapping[str, object]) -> str:
    version = portfolio_list_version(user_id)
    return f"portfolio:v{version}:user:{user_id}:list:{query_hash(parts)}"


def portfolio_detail_key(user_id: int, portfolio_id: object) -> str:
    version = portfolio_detail_version(user_id, portfolio_id)
    return f"portfolio:v{version}:user:{user_id}:detail:{portfolio_id}"


def portfolio_instruments_key(
    user_id: int,
    portfolio_id: object,
    parts: Mapping[str, object],
) -> str:
    version = portfolio_detail_version(user_id, portfolio_id)
    return f"portfolio:v{version}:user:{user_id}:instruments:{portfolio_id}:{query_hash(parts)}"
