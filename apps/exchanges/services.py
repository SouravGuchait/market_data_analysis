"""Idempotent exchange master-data seeding."""

from __future__ import annotations

import logging

from apps.exchanges.models import Exchange
from scripts.generate_market_data import EXCHANGES

logger = logging.getLogger(__name__)


def _upsert_exchange(code: str, name: str, timezone: str) -> str:
    exchange = Exchange.objects.filter(code=code).first()
    if exchange is None:
        Exchange.objects.create(code=code, name=name, timezone=timezone, is_active=True)
        return "created"

    changed: list[str] = []
    if exchange.name != name:
        exchange.name = name
        changed.append("name")
    if exchange.timezone != timezone:
        exchange.timezone = timezone
        changed.append("timezone")
    if not changed:
        return "unchanged"
    exchange.save(update_fields=[*changed, "updated_at"])
    return "updated"


def seed_exchange_records() -> tuple[int, int]:
    """Create generator exchanges and refresh seed-controlled name and timezone.

    ``is_active`` and exchanges outside the generator catalog are left unchanged.
    """

    created = 0
    updated = 0
    for code, name, timezone in EXCHANGES:
        outcome = _upsert_exchange(code, name, timezone)
        created += outcome == "created"
        updated += outcome == "updated"
    logger.info("Exchange seed finished: created=%s updated=%s", created, updated)
    return created, updated
