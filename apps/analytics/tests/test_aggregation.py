"""Unit tests for trade-to-candle aggregation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.analytics.services.aggregation import AggregationScope, aggregate_scope
from apps.analytics.services.candle_write import BucketRef, write_candle_bucket
from apps.analytics.services.timeframes import bucket_start_for
from apps.analytics.tests.conftest import make_trade
from apps.market_data.models import Candle, CandleTimeframe


@pytest.mark.django_db
def test_one_minute_ohlc_volume_and_trade_count(instrument, exchange) -> None:
    make_trade(
        instrument=instrument,
        exchange=exchange,
        event_timestamp=datetime(2024, 1, 2, 15, 0, 10, tzinfo=UTC),
        price="10.00",
        quantity="1.25",
        sequence_number=1,
    )
    make_trade(
        instrument=instrument,
        exchange=exchange,
        event_timestamp=datetime(2024, 1, 2, 15, 0, 20, tzinfo=UTC),
        price="12.00",
        quantity="2.00",
        sequence_number=2,
    )
    make_trade(
        instrument=instrument,
        exchange=exchange,
        event_timestamp=datetime(2024, 1, 2, 15, 0, 30, tzinfo=UTC),
        price="11.00",
        quantity="0.50",
        sequence_number=3,
    )
    totals = aggregate_scope(
        AggregationScope(
            instrument_ids=(instrument.pk,),
            exchange_ids=(exchange.pk,),
            timeframes=(CandleTimeframe.ONE_MINUTE,),
            range_start=datetime(2024, 1, 2, 15, 0, tzinfo=UTC),
            range_end=datetime(2024, 1, 2, 15, 1, tzinfo=UTC),
        )
    )
    assert totals.created == 1
    candle = Candle.objects.get()
    assert candle.open == Decimal("10.000000")
    assert candle.high == Decimal("12.000000")
    assert candle.low == Decimal("10.000000")
    assert candle.close == Decimal("11.000000")
    assert candle.volume == Decimal("3.75000000")
    assert candle.trade_count == 3
    assert candle.timeframe == CandleTimeframe.ONE_MINUTE


@pytest.mark.django_db
def test_same_timestamp_uses_sequence_then_id(instrument, exchange) -> None:
    stamp = datetime(2024, 1, 2, 15, 5, 0, tzinfo=UTC)
    make_trade(
        instrument=instrument,
        exchange=exchange,
        event_timestamp=stamp,
        price="9.00",
        sequence_number=None,
    )
    make_trade(
        instrument=instrument,
        exchange=exchange,
        event_timestamp=stamp,
        price="8.00",
        sequence_number=2,
    )
    make_trade(
        instrument=instrument,
        exchange=exchange,
        event_timestamp=stamp,
        price="7.00",
        sequence_number=1,
    )
    zone = ZoneInfo(exchange.timezone)
    start = bucket_start_for(stamp, CandleTimeframe.ONE_MINUTE, zone)
    write_candle_bucket(
        BucketRef(instrument.pk, exchange.pk, CandleTimeframe.ONE_MINUTE, start),
        zone,
    )
    candle = Candle.objects.get()
    assert candle.open == Decimal("7.000000")
    assert candle.close == Decimal("9.000000")


@pytest.mark.django_db
def test_boundary_trade_belongs_to_starting_bucket(instrument, exchange) -> None:
    make_trade(
        instrument=instrument,
        exchange=exchange,
        event_timestamp=datetime(2024, 1, 2, 15, 5, 0, tzinfo=UTC),
        price="10.00",
    )
    totals = aggregate_scope(
        AggregationScope(
            instrument_ids=(instrument.pk,),
            exchange_ids=(exchange.pk,),
            timeframes=(CandleTimeframe.FIVE_MINUTES,),
            range_start=datetime(2024, 1, 2, 15, 0, tzinfo=UTC),
            range_end=datetime(2024, 1, 2, 15, 10, tzinfo=UTC),
        )
    )
    assert totals.created == 1
    candle = Candle.objects.get()
    assert candle.bucket_start == datetime(2024, 1, 2, 15, 5, tzinfo=UTC)


@pytest.mark.django_db
def test_empty_intervals_are_omitted(instrument, exchange) -> None:
    make_trade(
        instrument=instrument,
        exchange=exchange,
        event_timestamp=datetime(2024, 1, 2, 15, 0, 1, tzinfo=UTC),
        price="10.00",
    )
    aggregate_scope(
        AggregationScope(
            instrument_ids=(instrument.pk,),
            exchange_ids=(exchange.pk,),
            timeframes=(CandleTimeframe.ONE_MINUTE,),
            range_start=datetime(2024, 1, 2, 15, 0, tzinfo=UTC),
            range_end=datetime(2024, 1, 2, 15, 10, tzinfo=UTC),
        )
    )
    assert Candle.objects.count() == 1


@pytest.mark.django_db
def test_repeated_aggregation_is_idempotent(instrument, exchange) -> None:
    make_trade(
        instrument=instrument,
        exchange=exchange,
        event_timestamp=datetime(2024, 1, 2, 15, 0, 1, tzinfo=UTC),
        price="10.00",
        quantity="2",
    )
    scope = AggregationScope(
        instrument_ids=(instrument.pk,),
        exchange_ids=(exchange.pk,),
        timeframes=(CandleTimeframe.ONE_MINUTE,),
        range_start=datetime(2024, 1, 2, 15, 0, tzinfo=UTC),
        range_end=datetime(2024, 1, 2, 15, 1, tzinfo=UTC),
    )
    first = aggregate_scope(scope)
    second = aggregate_scope(scope)
    assert first.created == 1
    assert second.unchanged == 1
    assert Candle.objects.count() == 1


@pytest.mark.django_db
def test_late_arriving_trade_updates_existing_candle(instrument, exchange) -> None:
    make_trade(
        instrument=instrument,
        exchange=exchange,
        event_timestamp=datetime(2024, 1, 2, 15, 0, 10, tzinfo=UTC),
        price="10.00",
        quantity="1",
    )
    scope = AggregationScope(
        instrument_ids=(instrument.pk,),
        exchange_ids=(exchange.pk,),
        timeframes=(CandleTimeframe.ONE_MINUTE,),
        range_start=datetime(2024, 1, 2, 15, 0, tzinfo=UTC),
        range_end=datetime(2024, 1, 2, 15, 1, tzinfo=UTC),
    )
    aggregate_scope(scope)
    make_trade(
        instrument=instrument,
        exchange=exchange,
        event_timestamp=datetime(2024, 1, 2, 15, 0, 5, tzinfo=UTC),
        price="9.00",
        quantity="3",
    )
    result = aggregate_scope(scope)
    candle = Candle.objects.get()
    assert result.updated == 1
    assert candle.open == Decimal("9.000000")
    assert candle.close == Decimal("10.000000")
    assert candle.volume == Decimal("4.00000000")
    assert candle.trade_count == 2
    assert candle.aggregation_version == 2


@pytest.mark.django_db
def test_daily_bucket_uses_exchange_local_midnight(instrument, exchange) -> None:
    # 2024-01-02 05:30 UTC is 2024-01-02 00:30 America/New_York (EST).
    make_trade(
        instrument=instrument,
        exchange=exchange,
        event_timestamp=datetime(2024, 1, 2, 5, 30, tzinfo=UTC),
        price="10.00",
    )
    # 2024-01-02 04:30 UTC is still 2024-01-01 23:30 America/New_York.
    make_trade(
        instrument=instrument,
        exchange=exchange,
        event_timestamp=datetime(2024, 1, 2, 4, 30, tzinfo=UTC),
        price="11.00",
    )
    aggregate_scope(
        AggregationScope(
            instrument_ids=(instrument.pk,),
            exchange_ids=(exchange.pk,),
            timeframes=(CandleTimeframe.ONE_DAY,),
            range_start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
            range_end=datetime(2024, 1, 3, 0, 0, tzinfo=UTC),
        )
    )
    candles = list(Candle.objects.order_by("bucket_start"))
    assert len(candles) == 2
    assert candles[0].close == Decimal("11.000000")
    assert candles[1].close == Decimal("10.000000")


@pytest.mark.django_db
def test_instruments_and_exchanges_are_isolated(
    instrument, exchange, other_instrument, other_exchange
) -> None:
    make_trade(
        instrument=instrument,
        exchange=exchange,
        event_timestamp=datetime(2024, 1, 2, 15, 0, 1, tzinfo=UTC),
        price="10.00",
    )
    make_trade(
        instrument=other_instrument,
        exchange=other_exchange,
        event_timestamp=datetime(2024, 1, 2, 15, 0, 1, tzinfo=UTC),
        price="20.00",
    )
    aggregate_scope(
        AggregationScope(
            instrument_ids=(instrument.pk, other_instrument.pk),
            exchange_ids=(exchange.pk, other_exchange.pk),
            timeframes=(CandleTimeframe.ONE_MINUTE,),
            range_start=datetime(2024, 1, 2, 15, 0, tzinfo=UTC),
            range_end=datetime(2024, 1, 2, 15, 1, tzinfo=UTC),
        )
    )
    assert Candle.objects.count() == 2
    assert Candle.objects.get(instrument=instrument).close == Decimal("10.000000")
    assert Candle.objects.get(instrument=other_instrument).close == Decimal("20.000000")
