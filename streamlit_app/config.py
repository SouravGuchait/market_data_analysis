"""Environment-driven Streamlit configuration."""

from __future__ import annotations

import os
from functools import lru_cache


@lru_cache(maxsize=1)
def api_base_url() -> str:
    return os.environ.get("MDA_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


@lru_cache(maxsize=1)
def request_timeout_seconds() -> float:
    raw = os.environ.get("MDA_API_TIMEOUT_SECONDS", "30")
    try:
        return max(float(raw), 1.0)
    except ValueError:
        return 30.0


@lru_cache(maxsize=1)
def import_timeout_seconds() -> float:
    """Longer timeout for imports that download many objects before responding."""

    raw = os.environ.get("MDA_IMPORT_TIMEOUT_SECONDS", "600")
    try:
        configured = float(raw)
    except ValueError:
        configured = 600.0
    return max(configured, request_timeout_seconds(), 1.0)


@lru_cache(maxsize=1)
def reference_cache_ttl_seconds() -> int:
    raw = os.environ.get("STREAMLIT_REFERENCE_CACHE_TTL", "300")
    try:
        return max(int(raw), 30)
    except ValueError:
        return 300


@lru_cache(maxsize=1)
def market_cache_ttl_seconds() -> int:
    raw = os.environ.get("STREAMLIT_MARKET_CACHE_TTL", "30")
    try:
        return max(int(raw), 5)
    except ValueError:
        return 30
