"""Read-side queries for candles, returns, and aggregation runs."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

from django.db.models import QuerySet

from apps.analytics.models import AggregationRun
from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from apps.market_data.models import Candle, DailyOHLCV
from apps.portfolios.models import PortfolioInstrument
from apps.portfolios.selectors import get_user_portfolio


def list_candles(
    *,
    instrument_ids: Sequence[int] | None = None,
    exchange_ids: Sequence[int] | None = None,
    timeframe: str | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
) -> QuerySet[Candle]:
    """Return trade-derived candles with related instrument and exchange loaded."""

    queryset = Candle.objects.select_related("instrument", "exchange").all()
    if instrument_ids is not None:
        queryset = queryset.filter(instrument_id__in=instrument_ids)
    if exchange_ids is not None:
        queryset = queryset.filter(exchange_id__in=exchange_ids)
    if timeframe:
        queryset = queryset.filter(timeframe=timeframe)
    if range_start is not None:
        queryset = queryset.filter(bucket_start__gte=range_start)
    if range_end is not None:
        queryset = queryset.filter(bucket_start__lte=range_end)
    return queryset.order_by("bucket_start", "id")


def owned_portfolio_instrument_ids(user: Any, portfolio_id: UUID) -> list[int] | None:
    """Return instrument ids for an owned portfolio, or None when not found."""

    portfolio = get_user_portfolio(user, portfolio_id)
    if portfolio is None:
        return None
    return list(
        PortfolioInstrument.objects.filter(portfolio=portfolio, is_active=True).values_list(
            "instrument", flat=True
        )
    )


def resolve_exchange_ids(
    *,
    exchange_id: int | None = None,
    exchange_code: str | None = None,
) -> list[int] | None:
    """Resolve exchange filters to primary keys; empty list means no match."""

    if exchange_id is None and not exchange_code:
        return None
    queryset = Exchange.objects.all()
    if exchange_id is not None:
        queryset = queryset.filter(pk=exchange_id)
    if exchange_code:
        queryset = queryset.filter(code__iexact=exchange_code.strip())
    return list(queryset.values_list("pk", flat=True))


def active_instrument_ids(instrument_id: int | None = None) -> list[int] | None:
    if instrument_id is None:
        return None
    exists = Instrument.objects.filter(pk=instrument_id, is_active=True).exists()
    return [instrument_id] if exists else []


def resolve_instrument_ids_by_keys(
    *,
    instrument_symbol: str | None = None,
    instrument_name: str | None = None,
    exchange_ids: Sequence[int] | None = None,
) -> list[int] | None:
    """Resolve active instruments by symbol and/or name.

    Returns ``None`` when neither key is supplied. An empty list means no match.
    """

    if not instrument_symbol and not instrument_name:
        return None
    queryset = Instrument.objects.filter(is_active=True)
    if instrument_symbol:
        queryset = queryset.filter(symbol__iexact=instrument_symbol.strip())
    if instrument_name:
        queryset = queryset.filter(name__iexact=instrument_name.strip())
    if exchange_ids is not None:
        queryset = queryset.filter(exchange_id__in=list(exchange_ids))
    return list(queryset.values_list("pk", flat=True))


def list_daily_ohlcv(
    *,
    instrument_ids: Sequence[int],
    start_date: date | None,
    end_date: date | None,
) -> QuerySet[DailyOHLCV]:
    queryset = DailyOHLCV.objects.filter(instrument_id__in=instrument_ids).select_related(
        "instrument",
        "instrument__exchange",
    )
    if start_date is not None:
        queryset = queryset.filter(trading_date__gte=start_date)
    if end_date is not None:
        queryset = queryset.filter(trading_date__lte=end_date)
    return queryset.order_by("instrument_id", "trading_date")


def get_aggregation_run(run_id: UUID) -> AggregationRun | None:
    return AggregationRun.objects.filter(pk=run_id).first()
