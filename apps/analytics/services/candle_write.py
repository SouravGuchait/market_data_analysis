"""Idempotent persistence of one recomputed candle bucket."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.analytics.services.candle_values import (
    CandleValues,
    compute_candle_values,
    trades_in_bucket,
)
from apps.analytics.services.timeframes import bucket_end_for
from apps.market_data.models import Candle


@dataclass(frozen=True)
class CandleWriteResult:
    """Outcome of writing one candle bucket."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0


@dataclass(frozen=True)
class BucketRef:
    """Identity of one candle bucket."""

    instrument_id: int
    exchange_id: int
    timeframe: str
    bucket_start: datetime


def _same_values(candle: Candle, values: CandleValues, *, is_complete: bool) -> bool:
    return (
        candle.open == values.open
        and candle.high == values.high
        and candle.low == values.low
        and candle.close == values.close
        and candle.volume == values.volume
        and candle.trade_count == values.trade_count
        and candle.is_complete == is_complete
    )


def _locked_candle(ref: BucketRef) -> Candle | None:
    return (
        Candle.objects.select_for_update()
        .filter(
            instrument_id=ref.instrument_id,
            exchange_id=ref.exchange_id,
            timeframe=ref.timeframe,
            bucket_start=ref.bucket_start,
        )
        .first()
    )


def _update_candle(candle: Candle, values: CandleValues, *, is_complete: bool) -> CandleWriteResult:
    if _same_values(candle, values, is_complete=is_complete):
        return CandleWriteResult(unchanged=1)
    candle.open = values.open
    candle.high = values.high
    candle.low = values.low
    candle.close = values.close
    candle.volume = values.volume
    candle.trade_count = values.trade_count
    candle.is_complete = is_complete
    candle.aggregation_version += 1
    candle.save(
        update_fields=[
            "open",
            "high",
            "low",
            "close",
            "volume",
            "trade_count",
            "is_complete",
            "aggregation_version",
            "updated_at",
        ]
    )
    return CandleWriteResult(updated=1)


def _insert_candle(
    ref: BucketRef,
    bucket_end: datetime,
    values: CandleValues,
    *,
    is_complete: bool,
) -> Candle:
    return Candle.objects.create(
        instrument_id=ref.instrument_id,
        exchange_id=ref.exchange_id,
        timeframe=ref.timeframe,
        bucket_start=ref.bucket_start,
        bucket_end=bucket_end,
        open=values.open,
        high=values.high,
        low=values.low,
        close=values.close,
        volume=values.volume,
        trade_count=values.trade_count,
        is_complete=is_complete,
        aggregation_version=1,
    )


def _delete_empty(ref: BucketRef) -> CandleWriteResult:
    deleted, _ = Candle.objects.filter(
        instrument_id=ref.instrument_id,
        exchange_id=ref.exchange_id,
        timeframe=ref.timeframe,
        bucket_start=ref.bucket_start,
    ).delete()
    return CandleWriteResult(deleted=1 if deleted else 0)


def _upsert_values(
    ref: BucketRef,
    bucket_end: datetime,
    values: CandleValues,
    *,
    is_complete: bool,
) -> CandleWriteResult:
    existing = _locked_candle(ref)
    if existing is not None:
        return _update_candle(existing, values, is_complete=is_complete)
    try:
        _insert_candle(ref, bucket_end, values, is_complete=is_complete)
    except IntegrityError:
        locked = _locked_candle(ref)
        if locked is None:
            raise
        return _update_candle(locked, values, is_complete=is_complete)
    return CandleWriteResult(created=1)


def write_candle_bucket(ref: BucketRef, exchange_tz: ZoneInfo) -> CandleWriteResult:
    """Recompute one bucket from trades and upsert or delete as needed."""

    bucket_end = bucket_end_for(ref.bucket_start, ref.timeframe, exchange_tz)
    queryset = trades_in_bucket(ref.instrument_id, ref.exchange_id, ref.bucket_start, bucket_end)
    values = compute_candle_values(queryset)
    is_complete = bucket_end <= timezone.now()
    with transaction.atomic():
        if values is None:
            return _delete_empty(ref)
        return _upsert_values(ref, bucket_end, values, is_complete=is_complete)
