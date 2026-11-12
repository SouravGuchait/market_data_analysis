"""Idempotent instrument master-data seeding."""

from __future__ import annotations

import logging

from django.db import transaction

from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from scripts.generate_market_data import canonical_instruments

logger = logging.getLogger(__name__)


class InstrumentSeedError(Exception):
    """The instrument catalog cannot be seeded with the current exchanges."""


def _metadata(instrument: Instrument) -> tuple[str, str, str]:
    return (instrument.name, instrument.asset_class, instrument.currency)


def _upsert_instrument(
    exchange: Exchange,
    symbol: str,
    name: str,
    asset_class: str,
    currency: str,
) -> str:
    instrument = Instrument.objects.filter(exchange=exchange, symbol__iexact=symbol).first()
    incoming = (name, asset_class, currency)
    if instrument is None:
        Instrument.objects.create(
            exchange=exchange,
            symbol=symbol,
            name=name,
            asset_class=asset_class,
            currency=currency,
        )
        return "created"
    if _metadata(instrument) == incoming:
        return "unchanged"
    instrument.name, instrument.asset_class, instrument.currency = incoming
    instrument.save(update_fields=["name", "asset_class", "currency", "updated_at"])
    return "updated"


@transaction.atomic
def seed_instrument_records(count: int | None = None) -> tuple[int, int, int]:
    """Create the generator catalog and refresh seed-controlled metadata.

    Active flags and instruments outside the requested catalog are preserved.
    """

    created = 0
    updated = 0
    unchanged = 0
    for row in canonical_instruments(count):
        code = str(row["exchange"])
        exchange = Exchange.objects.filter(code=code).first()
        if exchange is None:
            raise InstrumentSeedError(f"Exchange {code} is not seeded. Run seed_exchanges first.")
        outcome = _upsert_instrument(
            exchange,
            str(row["symbol"]),
            str(row["name"]),
            str(row["asset_class"]),
            str(row["currency"]),
        )
        created += outcome == "created"
        updated += outcome == "updated"
        unchanged += outcome == "unchanged"
    logger.info(
        "Instrument seed finished: created=%s updated=%s unchanged=%s",
        created,
        updated,
        unchanged,
    )
    return created, updated, unchanged
