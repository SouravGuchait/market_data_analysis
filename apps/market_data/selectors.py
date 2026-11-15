"""Bounded trade/tick and daily OHLCV query helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from django.db.models import QuerySet

from apps.market_data.models import DailyOHLCV, Trade

MAX_TICK_RANGE_DAYS = 31
MAX_DAILY_RANGE_DAYS = 366


@dataclass(frozen=True)
class TradeQuery:
    instrument_ids: list[int] | None
    exchange_ids: list[int] | None
    range_start: datetime | None
    range_end: datetime | None
    exchange_trade_id: str | None
    sequence_number: int | None
    newest_first: bool = False


@dataclass(frozen=True)
class DailyOHLCVQuery:
    instrument_ids: list[int] | None
    start_date: date | None
    end_date: date | None


def list_trades(query: TradeQuery) -> QuerySet[Trade]:
    queryset = _filter_trades(Trade.objects.select_related("instrument", "exchange"), query)
    if query.newest_first:
        return queryset.order_by("-event_timestamp", "-sequence_number", "-id")
    return queryset.order_by("event_timestamp", "sequence_number", "id")


def _filter_trades(queryset: QuerySet[Trade], query: TradeQuery) -> QuerySet[Trade]:
    if query.instrument_ids is not None:
        queryset = queryset.filter(instrument_id__in=query.instrument_ids)
    if query.exchange_ids is not None:
        queryset = queryset.filter(exchange_id__in=query.exchange_ids)
    if query.range_start is not None:
        queryset = queryset.filter(event_timestamp__gte=query.range_start)
    if query.range_end is not None:
        queryset = queryset.filter(event_timestamp__lte=query.range_end)
    if query.exchange_trade_id:
        queryset = queryset.filter(exchange_trade_id=query.exchange_trade_id)
    if query.sequence_number is not None:
        queryset = queryset.filter(sequence_number=query.sequence_number)
    return queryset


def list_daily_bars(query: DailyOHLCVQuery) -> QuerySet[DailyOHLCV]:
    queryset = DailyOHLCV.objects.select_related("instrument", "instrument__exchange")
    if query.instrument_ids is not None:
        queryset = queryset.filter(instrument_id__in=query.instrument_ids)
    if query.start_date is not None:
        queryset = queryset.filter(trading_date__gte=query.start_date)
    if query.end_date is not None:
        queryset = queryset.filter(trading_date__lte=query.end_date)
    return queryset.order_by("-trading_date", "-id")
