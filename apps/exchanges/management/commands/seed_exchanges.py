"""Idempotently load exchange master records from the data generator."""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.exchanges.services import seed_exchange_records
from apps.market_data.models import AssetClass, Currency, GeneratorSymbol, TickColumn
from scripts.generate_market_data import (
    ASSET_CLASSES,
    CURRENCIES,
    SYMBOL_POOL,
    TICK_COLUMNS,
)


def _seed_catalogs() -> None:
    for symbol, name in SYMBOL_POOL:
        GeneratorSymbol.objects.get_or_create(symbol=symbol, defaults={"name": name})
    catalogs = (
        (TickColumn, "name", TICK_COLUMNS),
        (AssetClass, "code", list(dict.fromkeys(ASSET_CLASSES))),
        (Currency, "code", list(dict.fromkeys(CURRENCIES))),
    )
    for model, field, values in catalogs:
        for position, value in enumerate(values):
            model.objects.get_or_create(**{field: value}, defaults={"position": position})


class Command(BaseCommand):
    help = "Create missing exchange master records and refresh seed-controlled metadata."

    @transaction.atomic
    def handle(self, *args: object, **options: object) -> None:
        created, updated = seed_exchange_records()
        _seed_catalogs()
        self.stdout.write(
            self.style.SUCCESS(f"Exchange seed complete; created {created}, updated {updated}.")
        )
