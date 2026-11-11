"""Tests for generator reference catalogs and their repeatable seeding."""

import pytest
from django.core.management import call_command

from apps.exchanges.models import Exchange
from apps.market_data.models import AssetClass, Currency, GeneratorSymbol, TickColumn


@pytest.mark.django_db
def test_seed_exchanges_is_idempotent_and_loads_generator_catalogs():
    call_command("seed_exchanges")
    counts = (
        Exchange.objects.count(),
        GeneratorSymbol.objects.count(),
        TickColumn.objects.count(),
        AssetClass.objects.count(),
        Currency.objects.count(),
    )
    call_command("seed_exchanges")
    assert counts == (4, 10, 7, 3, 6)
    assert counts == (
        Exchange.objects.count(),
        GeneratorSymbol.objects.count(),
        TickColumn.objects.count(),
        AssetClass.objects.count(),
        Currency.objects.count(),
    )
    assert GeneratorSymbol.objects.get(symbol="ACME").name == "Acme Industrial"
    assert list(Currency.objects.order_by("position").values_list("code", flat=True)) == [
        "USD",
        "EUR",
        "GBP",
        "JPY",
        "CNY",
        "CHF",
    ]
