"""Generated daily and tick rows resolve to the same shared instruments."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
from django.core.management import call_command

from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from apps.market_data.models import DailyOHLCV
from apps.market_data.services import (
    DataMappingError,
    MarketDataMappingService,
    resolve_listed_instrument,
)
from apps.portfolios.models import Portfolio
from scripts.generate_market_data import build_instruments, generate_rows, generate_ticks


@pytest.fixture
def exchange(db) -> Exchange:
    return Exchange.objects.create(code="XNYS", name="NYSE", timezone="America/New_York")


@pytest.mark.django_db
def test_generated_daily_and_tick_rows_share_canonical_instruments():
    call_command("seed_market_data")
    instruments = build_instruments(5)
    rows = generate_rows(instruments, [date(2024, 1, 2)], np.random.default_rng(1))
    ticks = generate_ticks(rows, 4, np.random.default_rng(2))

    resolved = [resolve_listed_instrument(str(row["exchange"]), str(row["symbol"])) for row in rows]
    assert [item.symbol for item in resolved] == [row["symbol"] for row in rows]
    for row, instrument in zip(rows, resolved, strict=True):
        assert instrument.asset_class == row["asset_class"]
        assert instrument.currency == row["currency"]
        assert instrument.name == row["name"]

    tick_ids = {
        resolve_listed_instrument(str(tick["exchange"]), str(tick["symbol"])).pk for tick in ticks
    }
    assert tick_ids == {instrument.pk for instrument in resolved}
    assert Portfolio.objects.count() == 0


@pytest.mark.django_db
def test_ingestion_mapping_does_not_create_instruments_or_require_a_portfolio(exchange):
    row = {
        "exchange": exchange.code,
        "symbol": "ACME",
        "name": "Acme Industrial",
        "asset_class": "equity",
        "currency": "USD",
        "date": "2024-01-02",
        "timestamp": "2024-01-02T21:00:00+00:00",
        "open": "10",
        "high": "12",
        "low": "9",
        "close": "11",
        "volume": "5",
    }
    with pytest.raises(DataMappingError, match="not registered"):
        MarketDataMappingService.map_and_persist_row(row)
    assert Instrument.objects.count() == 0

    Instrument.objects.create(
        exchange=exchange,
        symbol="ACME",
        name="Acme Industrial",
        asset_class="equity",
        currency="USD",
    )
    candle, outcome = MarketDataMappingService.map_and_persist_row(row)
    assert outcome == "inserted"
    assert DailyOHLCV.objects.count() == 1
    assert candle.instrument.symbol == "ACME"
    assert Portfolio.objects.count() == 0


@pytest.mark.django_db
def test_unknown_symbol_has_a_meaningful_resolution_error(exchange):
    with pytest.raises(DataMappingError, match="not registered"):
        resolve_listed_instrument(exchange.code, "MISSING")
