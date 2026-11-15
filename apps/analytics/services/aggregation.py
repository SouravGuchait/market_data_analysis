"""Orchestrate bounded, idempotent trade-to-candle aggregation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db.models import Max, Min
from django.utils import timezone

from apps.analytics.models import AggregationRun, AggregationRunStatus
from apps.analytics.services.candle_write import BucketRef, CandleWriteResult, write_candle_bucket
from apps.analytics.services.timeframes import (
    SUPPORTED_TIMEFRAMES,
    bucket_start_for,
    resolve_zone,
)
from apps.exchanges.models import Exchange
from apps.market_data.models import Trade

_TRADE_BATCH = 1000


@dataclass
class AggregationTotals:
    """Counters accumulated across candle writes."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0

    def add(self, result: CandleWriteResult) -> None:
        self.created += result.created
        self.updated += result.updated
        self.unchanged += result.unchanged
        self.deleted += result.deleted


@dataclass(frozen=True)
class AggregationScope:
    """Bounded aggregation selection."""

    instrument_ids: tuple[int, ...]
    exchange_ids: tuple[int, ...]
    timeframes: tuple[str, ...]
    range_start: datetime
    range_end: datetime


def _pair_keys(
    instrument_ids: tuple[int, ...], exchange_ids: tuple[int, ...]
) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    for instrument_id in instrument_ids:
        for exchange_id in exchange_ids:
            pairs.append((instrument_id, exchange_id))
    return pairs


def _exchange_zones(exchange_ids: tuple[int, ...]) -> dict[int, ZoneInfo]:
    rows = Exchange.objects.filter(pk__in=exchange_ids).values_list("pk", "timezone")
    return {int(pk): resolve_zone(str(tz_name)) for pk, tz_name in rows}


def _trade_timestamps(
    instrument_id: int,
    exchange_id: int,
    range_start: datetime,
    range_end: datetime,
) -> list[datetime]:
    """Load event times in ``[range_start, range_end]`` plus open bucket spillover."""

    return list(
        Trade.objects.filter(
            instrument_id=instrument_id,
            exchange_id=exchange_id,
            event_timestamp__gte=range_start,
            event_timestamp__lte=range_end,
        )
        .order_by("event_timestamp")
        .values_list("event_timestamp", flat=True)
        .iterator(chunk_size=_TRADE_BATCH)
    )


def _write_affected(
    instrument_id: int,
    exchange_id: int,
    timeframe: str,
    timestamps: list[datetime],
    zone: ZoneInfo,
) -> list[CandleWriteResult]:
    starts = {bucket_start_for(moment, timeframe, zone) for moment in timestamps}
    return [
        write_candle_bucket(BucketRef(instrument_id, exchange_id, timeframe, start), zone)
        for start in sorted(starts)
    ]


def _expanded_start(scope: AggregationScope, zone: ZoneInfo) -> datetime:
    starts = [bucket_start_for(scope.range_start, tf, zone) for tf in scope.timeframes]
    return min(starts) if starts else scope.range_start


def _aggregate_pair(
    instrument_id: int,
    exchange_id: int,
    scope: AggregationScope,
    zone: ZoneInfo,
    totals: AggregationTotals,
) -> None:
    timestamps = _trade_timestamps(
        instrument_id,
        exchange_id,
        _expanded_start(scope, zone),
        scope.range_end,
    )
    if not timestamps:
        return
    for timeframe in scope.timeframes:
        for result in _write_affected(instrument_id, exchange_id, timeframe, timestamps, zone):
            totals.add(result)


def aggregate_scope(scope: AggregationScope) -> AggregationTotals:
    """Recompute all non-empty intersecting buckets for the given scope."""

    totals = AggregationTotals()
    zones = _exchange_zones(scope.exchange_ids)
    for instrument_id, exchange_id in _pair_keys(scope.instrument_ids, scope.exchange_ids):
        zone = zones.get(exchange_id)
        if zone is None:
            continue
        _aggregate_pair(instrument_id, exchange_id, scope, zone, totals)
    return totals


def scope_from_run(run: AggregationRun) -> AggregationScope:
    """Build an aggregation scope from a persisted run."""

    timeframes = tuple(run.timeframes) if run.timeframes else SUPPORTED_TIMEFRAMES
    return AggregationScope(
        instrument_ids=tuple(int(value) for value in run.instrument_ids),
        exchange_ids=tuple(int(value) for value in run.exchange_ids),
        timeframes=timeframes,
        range_start=run.range_start,
        range_end=run.range_end,
    )


def mark_run_running(run: AggregationRun) -> AggregationRun:
    run.status = AggregationRunStatus.RUNNING
    run.started_at = timezone.now()
    run.error_summary = ""
    run.save(update_fields=["status", "started_at", "error_summary", "updated_at"])
    return run


def mark_run_succeeded(run: AggregationRun, totals: AggregationTotals) -> AggregationRun:
    from apps.common.cache_service import bump_market_version

    run.status = AggregationRunStatus.SUCCEEDED
    run.finished_at = timezone.now()
    run.candles_created = totals.created
    run.candles_updated = totals.updated
    run.candles_unchanged = totals.unchanged
    run.error_summary = ""
    run.save(
        update_fields=[
            "status",
            "finished_at",
            "candles_created",
            "candles_updated",
            "candles_unchanged",
            "error_summary",
            "updated_at",
        ]
    )
    bump_market_version()
    return run


def mark_run_failed(run: AggregationRun, exc: BaseException) -> AggregationRun:
    run.status = AggregationRunStatus.FAILED
    run.finished_at = timezone.now()
    run.error_summary = str(exc)[:2000]
    run.save(update_fields=["status", "finished_at", "error_summary", "updated_at"])
    return run


def trade_bounds_for_ingestion(ingestion_id: UUID) -> tuple[datetime, datetime] | None:
    """Return min/max event timestamps for trades belonging to one ingestion."""

    bounds = Trade.objects.filter(ingestion_id=ingestion_id).aggregate(
        start=Min("event_timestamp"),
        end=Max("event_timestamp"),
    )
    start = bounds["start"]
    end = bounds["end"]
    if start is None or end is None:
        return None
    return start, end


def instrument_exchange_pairs(ingestion_id: UUID) -> list[tuple[int, int]]:
    """Distinct instrument/exchange pairs touched by one ingestion."""

    rows = (
        Trade.objects.filter(ingestion_id=ingestion_id)
        .values_list("instrument_id", "exchange_id")
        .distinct()
    )
    return [(int(instrument_id), int(exchange_id)) for instrument_id, exchange_id in rows]


@dataclass
class IngestionAggregationPlan:
    """Inputs derived from a successful tick ingestion."""

    instrument_ids: list[int] = field(default_factory=list)
    exchange_ids: list[int] = field(default_factory=list)
    range_start: datetime | None = None
    range_end: datetime | None = None

    @property
    def is_empty(self) -> bool:
        return not self.instrument_ids or self.range_start is None or self.range_end is None


def plan_from_ingestion(ingestion_id: UUID) -> IngestionAggregationPlan:
    """Build an aggregation plan from persisted trades for one ingestion."""

    pairs = instrument_exchange_pairs(ingestion_id)
    bounds = trade_bounds_for_ingestion(ingestion_id)
    if not pairs or bounds is None:
        return IngestionAggregationPlan()
    instrument_ids = sorted({pair[0] for pair in pairs})
    exchange_ids = sorted({pair[1] for pair in pairs})
    return IngestionAggregationPlan(
        instrument_ids=instrument_ids,
        exchange_ids=exchange_ids,
        range_start=bounds[0],
        range_end=bounds[1],
    )
