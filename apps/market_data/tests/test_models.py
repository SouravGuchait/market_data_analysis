"""Model and row-mapping coverage for Feature 002.5."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from apps.market_data.models import DailyOHLCV
from apps.market_data.services import DataMappingError, MarketDataMappingService
from apps.portfolios.models import Portfolio, PortfolioInstrument


@pytest.fixture
def exchange(db):
    return Exchange.objects.create(code="XNYS", name="NYSE", timezone="America/New_York")


@pytest.fixture
def instrument(exchange):
    return Instrument.objects.create(
        exchange=exchange,
        symbol="ACME",
        name="Acme Industrial",
        asset_class=Instrument.AssetClass.EQUITY,
        currency=Instrument.Currency.USD,
    )


@pytest.mark.django_db
def test_daily_ohlcv_unique_per_instrument_and_day(instrument):
    candle = DailyOHLCV.objects.create(
        instrument=instrument,
        trading_date=date(2024, 1, 2),
        timestamp=timezone.now(),
        open=Decimal(10),
        high=Decimal(12),
        low=Decimal(9),
        close=Decimal(11),
        volume=5,
    )
    assert candle.instrument == instrument
    with pytest.raises(IntegrityError), transaction.atomic():
        DailyOHLCV.objects.create(
            instrument=instrument,
            trading_date=date(2024, 1, 2),
            timestamp=timezone.now(),
            open=10,
            high=12,
            low=9,
            close=11,
            volume=5,
        )


@pytest.mark.django_db
def test_same_symbol_can_exist_on_different_exchanges(instrument):
    other_exchange = Exchange.objects.create(
        code="XNAS", name="Nasdaq", timezone="America/New_York"
    )
    other = Instrument.objects.create(
        exchange=other_exchange,
        symbol=instrument.symbol,
        name=instrument.name,
        asset_class=instrument.asset_class,
        currency=instrument.currency,
    )
    assert other.pk != instrument.pk


@pytest.mark.django_db
def test_two_users_can_reference_the_same_instrument(instrument):
    user_model = User
    first = Portfolio.objects.create(
        owner=user_model.objects.create_user(username="first"), name="First"
    )
    second = Portfolio.objects.create(
        owner=user_model.objects.create_user(username="second"), name="Second"
    )
    PortfolioInstrument.objects.create(portfolio=first, instrument=instrument)
    PortfolioInstrument.objects.create(portfolio=second, instrument=instrument)
    assert first.instruments.get() == second.instruments.get() == instrument


@pytest.mark.django_db
def test_portfolio_shares_instrument_and_rejects_duplicate_membership(instrument):
    user = User.objects.create_user(username="owner", password="test")
    portfolio = Portfolio.objects.create(owner=user, name="Main")
    PortfolioInstrument.objects.create(portfolio=portfolio, instrument=instrument)
    assert list(portfolio.instruments.all()) == [instrument]
    with pytest.raises(IntegrityError), transaction.atomic():
        PortfolioInstrument.objects.create(portfolio=portfolio, instrument=instrument)


@pytest.mark.django_db
def test_mapping_creates_instrument_and_daily_candle(exchange):
    timestamp = datetime(2024, 1, 2, 21, tzinfo=UTC)
    row = {
        "exchange": exchange.code,
        "symbol": "ACME",
        "name": "Acme Industrial",
        "asset_class": "equity",
        "currency": "USD",
        "date": "2024-01-02",
        "timestamp": timestamp.isoformat(),
        "open": "10",
        "high": "12",
        "low": "9",
        "close": "11",
        "adjusted_close": "",
        "volume": "5",
    }
    candle, result = MarketDataMappingService.map_and_persist_row(row)
    assert result == "inserted"
    assert candle.instrument.exchange == exchange
    duplicate, result = MarketDataMappingService.map_and_persist_row(row)
    assert duplicate.pk == candle.pk
    assert result == "skipped_duplicate"


@pytest.mark.django_db
def test_mapping_rejects_invalid_ohlc(exchange):
    row = {
        "exchange": exchange.code,
        "symbol": "ACME",
        "name": "Acme Industrial",
        "asset_class": "equity",
        "currency": "USD",
        "date": "2024-01-02",
        "timestamp": "2024-01-02T21:00:00+00:00",
        "open": "10",
        "high": "8",
        "low": "9",
        "close": "11",
        "volume": "5",
    }
    with pytest.raises(DataMappingError):
        MarketDataMappingService.map_and_persist_row(row)


@pytest.mark.django_db
def test_mapping_rejects_unknown_exchange_and_missing_instrument_policy(exchange):
    row = {
        "exchange": "XXXX",
        "symbol": "ACME",
        "date": "2024-01-02",
        "timestamp": "2024-01-02T21:00:00+00:00",
    }
    with pytest.raises(DataMappingError, match="Unknown or inactive exchange"):
        MarketDataMappingService.map_and_persist_row(row)
    row["exchange"] = exchange.code
    with pytest.raises(DataMappingError, match="not registered"):
        MarketDataMappingService.map_and_persist_row(row, allow_instrument_creation=False)
