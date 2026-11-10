"""Dependency health checks.

Health checks must never leak credentials, connection strings or stack traces
to API clients.  Technical details are written to the application log only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from django.core.cache import cache
from django.db import connections

logger = logging.getLogger("apps.health")

HealthState = Literal["ok", "unavailable"]


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    """Result of checking one external dependency."""

    status: HealthState


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    """Aggregate readiness result for the service."""

    is_ready: bool
    checks: dict[str, DependencyStatus]
    errors: dict[str, list[str]]


def check_database() -> DependencyStatus:
    """Verify that the configured relational database accepts a query."""

    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        logger.exception("database_health_check_failed")
        return DependencyStatus(status="unavailable")
    return DependencyStatus(status="ok")


def check_cache() -> DependencyStatus:
    """Verify that the configured cache can round-trip a value."""

    key = "health:readiness"
    try:
        cache.set(key, "ok", timeout=5)
        if cache.get(key) != "ok":
            logger.error("cache_health_check_value_mismatch")
            return DependencyStatus(status="unavailable")
    except Exception:
        logger.exception("cache_health_check_failed")
        return DependencyStatus(status="unavailable")
    return DependencyStatus(status="ok")


def run_readiness_checks() -> ReadinessResult:
    """Run every readiness dependency check and aggregate the result."""

    checks = {
        "database": check_database(),
        "cache": check_cache(),
    }
    errors = {name: ["unavailable"] for name, result in checks.items() if result.status != "ok"}
    return ReadinessResult(is_ready=not errors, checks=checks, errors=errors)
