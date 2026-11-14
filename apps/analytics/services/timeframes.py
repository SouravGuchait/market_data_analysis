"""Central candle timeframe definitions and bucket boundary helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apps.market_data.models import CandleTimeframe

_INTERVAL_DELTAS: dict[str, timedelta] = {
    CandleTimeframe.ONE_MINUTE: timedelta(minutes=1),
    CandleTimeframe.FIVE_MINUTES: timedelta(minutes=5),
    CandleTimeframe.FIFTEEN_MINUTES: timedelta(minutes=15),
    CandleTimeframe.THIRTY_MINUTES: timedelta(minutes=30),
    CandleTimeframe.ONE_HOUR: timedelta(hours=1),
    CandleTimeframe.ONE_DAY: timedelta(days=1),
}

SUPPORTED_TIMEFRAMES: tuple[str, ...] = tuple(_INTERVAL_DELTAS)


class UnsupportedTimeframeError(ValueError):
    """Raised when a candle timeframe is not configured."""


def interval_delta(timeframe: str) -> timedelta:
    """Return the duration of one candle interval."""

    delta = _INTERVAL_DELTAS.get(timeframe)
    if delta is None:
        raise UnsupportedTimeframeError(f"Unsupported candle timeframe: {timeframe}")
    return delta


def resolve_zone(timezone_name: str) -> ZoneInfo:
    """Resolve an IANA timezone, falling back to UTC when invalid."""

    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _floor_utc_minutes(moment: datetime, minutes: int) -> datetime:
    utc = moment.astimezone(UTC)
    floored_minute = utc.minute - (utc.minute % minutes)
    return utc.replace(minute=floored_minute, second=0, microsecond=0)


def _daily_bucket_start(moment: datetime, exchange_tz: ZoneInfo) -> datetime:
    local = moment.astimezone(exchange_tz)
    local_midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(UTC)


def bucket_start_for(moment: datetime, timeframe: str, exchange_tz: ZoneInfo) -> datetime:
    """Return the inclusive UTC start of the candle containing ``moment``."""

    if moment.tzinfo is None:
        raise ValueError("Trade event timestamps must be timezone-aware.")
    if timeframe == CandleTimeframe.ONE_DAY:
        return _daily_bucket_start(moment, exchange_tz)
    if timeframe == CandleTimeframe.ONE_HOUR:
        utc = moment.astimezone(UTC)
        return utc.replace(minute=0, second=0, microsecond=0)
    minutes = int(interval_delta(timeframe).total_seconds() // 60)
    return _floor_utc_minutes(moment, minutes)


def _daily_bucket_end(bucket_start: datetime, exchange_tz: ZoneInfo) -> datetime:
    local_start = bucket_start.astimezone(exchange_tz)
    next_day: date = local_start.date() + timedelta(days=1)
    local_end = datetime.combine(next_day, time.min, tzinfo=exchange_tz)
    return local_end.astimezone(UTC)


def bucket_end_for(
    bucket_start: datetime,
    timeframe: str,
    exchange_tz: ZoneInfo | None = None,
) -> datetime:
    """Return the exclusive UTC end of a candle bucket."""

    if timeframe == CandleTimeframe.ONE_DAY:
        if exchange_tz is None:
            raise ValueError("exchange_tz is required for daily candle buckets.")
        return _daily_bucket_end(bucket_start, exchange_tz)
    return bucket_start + interval_delta(timeframe)


def _next_bucket_start(
    current: datetime,
    timeframe: str,
    exchange_tz: ZoneInfo,
) -> datetime:
    if timeframe == CandleTimeframe.ONE_DAY:
        return bucket_end_for(current, timeframe, exchange_tz)
    return current + interval_delta(timeframe)


def iter_bucket_starts(
    range_start: datetime,
    range_end: datetime,
    timeframe: str,
    exchange_tz: ZoneInfo,
) -> list[datetime]:
    """Return bucket starts that intersect ``[range_start, range_end]`` inclusive."""

    if range_end < range_start:
        raise ValueError("range_end must be greater than or equal to range_start.")
    first = bucket_start_for(range_start, timeframe, exchange_tz)
    last = bucket_start_for(range_end, timeframe, exchange_tz)
    starts: list[datetime] = []
    current = first
    while current <= last:
        starts.append(current)
        current = _next_bucket_start(current, timeframe, exchange_tz)
    return starts
