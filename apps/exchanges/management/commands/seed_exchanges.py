"""Idempotently load exchange master records from the data generator."""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.exchanges.models import Exchange
from apps.market_data.models import AssetClass, Currency, GeneratorSymbol, TickColumn
from scripts.generate_market_data import (
    ASSET_CLASSES,
    CURRENCIES,
    EXCHANGES,
    SYMBOL_POOL,
    TICK_COLUMNS,
)


class Command(BaseCommand):
    help = "Create missing exchange master records from generator metadata."

    @transaction.atomic
    def handle(self, *args: object, **options: object) -> None:
        created = 0
        for code, name, timezone in EXCHANGES:
            _, was_created = Exchange.objects.get_or_create(
                code=code,
                defaults={"name": name, "timezone": timezone, "is_active": True},
            )
            created += was_created
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
        self.stdout.write(
            self.style.SUCCESS(f"Reference seed complete; created {created} exchanges.")
        )
