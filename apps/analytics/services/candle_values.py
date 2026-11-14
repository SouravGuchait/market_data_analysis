"""Compute OHLCV values for one candle bucket from Trade rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db.models import Count, Max, Min, QuerySet, Sum

from apps.analytics.services.trade_order import trade_order_by
from apps.market_data.models import Trade


@dataclass(frozen=True)
class CandleValues:
    """OHLCV metrics for one non-empty trade bucket."""

    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int


def trades_in_bucket(
    instrument_id: int,
    exchange_id: int,
    bucket_start: datetime,
    bucket_end: datetime,
) -> QuerySet[Trade]:
    """Return trades in ``[bucket_start, bucket_end)`` for one instrument/exchange."""

    return Trade.objects.filter(
        instrument_id=instrument_id,
        exchange_id=exchange_id,
        event_timestamp__gte=bucket_start,
        event_timestamp__lt=bucket_end,
    )


def _edge_price(queryset: QuerySet[Trade], *, reverse: bool) -> Decimal | None:
    ordered = queryset.order_by(*trade_order_by())
    if reverse:
        ordered = ordered.reverse()
    value = ordered.values_list("price", flat=True).first()
    return None if value is None else Decimal(value)


def compute_candle_values(queryset: QuerySet[Trade]) -> CandleValues | None:
    """Aggregate OHLC, volume, and trade count; return None when empty."""

    aggregates = queryset.aggregate(
        high=Max("price"),
        low=Min("price"),
        volume=Sum("quantity"),
        trade_count=Count("id"),
    )
    trade_count = int(aggregates["trade_count"] or 0)
    if trade_count == 0:
        return None
    open_price = _edge_price(queryset, reverse=False)
    close_price = _edge_price(queryset, reverse=True)
    if open_price is None or close_price is None:
        return None
    return CandleValues(
        open=open_price,
        high=Decimal(aggregates["high"]),
        low=Decimal(aggregates["low"]),
        close=close_price,
        volume=Decimal(aggregates["volume"] or 0),
        trade_count=trade_count,
    )
