"""Validation and persistence for synthetic daily market rows."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_date, parse_datetime

from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from apps.market_data.models import DailyOHLCV

logger = logging.getLogger(__name__)


class DataMappingError(ValueError):
    """A source row cannot be mapped to the market-data schema."""


def _decimal(value: object, field: str, *, optional: bool = False) -> Decimal | None:
    if optional and value in (None, ""):
        return None
    if value in (None, "") or isinstance(value, bool):
        raise DataMappingError(f"{field} is required.")
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise DataMappingError(f"{field} must be numeric.") from exc
    if not result.is_finite():
        raise DataMappingError(f"{field} must be finite.")
    return result


def _integer(value: object, field: str) -> int:
    if value in (None, "") or isinstance(value, bool):
        raise DataMappingError(f"{field} is required.")
    try:
        result = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise DataMappingError(f"{field} must be an integer.") from exc
    if str(value).strip() not in (str(result), f"+{result}"):
        raise DataMappingError(f"{field} must be an integer.")
    if result < 0:
        raise DataMappingError(f"{field} must be non-negative.")
    return result


def _required_text(row: Mapping[str, object], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DataMappingError(f"{field} is required.")
    return value.strip()


def _resolve_exchange(code: str) -> Exchange:
    try:
        return Exchange.objects.get(code=code, is_active=True)
    except Exchange.DoesNotExist as exc:
        raise DataMappingError(f"Unknown or inactive exchange: {code}.") from exc


def _resolve_trading_time(row: Mapping[str, object], exchange: Exchange) -> tuple[date, datetime]:
    raw_date = row.get("date")
    trading_date = (
        raw_date
        if isinstance(raw_date, date) and not isinstance(raw_date, datetime)
        else parse_date(str(raw_date))
    )
    raw_timestamp = row.get("timestamp")
    timestamp = (
        raw_timestamp if isinstance(raw_timestamp, datetime) else parse_datetime(str(raw_timestamp))
    )
    if trading_date is None or timestamp is None or timestamp.utcoffset() is None:
        raise DataMappingError("date and timezone-aware timestamp are required.")
    timestamp = timestamp.astimezone(UTC)
    try:
        local_date = timestamp.astimezone(ZoneInfo(exchange.timezone)).date()
    except ZoneInfoNotFoundError as exc:
        raise DataMappingError(f"Invalid timezone configured for {exchange.code}.") from exc
    if local_date != trading_date:
        raise DataMappingError("date does not match the exchange-local timestamp date.")
    return trading_date, timestamp


def _resolve_instrument(
    row: Mapping[str, object],
    exchange: Exchange,
    symbol: str,
    allow_creation: bool,
) -> Instrument:
    try:
        instrument = Instrument.objects.get(exchange=exchange, symbol=symbol)
    except Instrument.DoesNotExist:
        if not allow_creation:
            raise DataMappingError(f"Instrument {symbol} is not registered.") from None
        details = (row.get("name"), row.get("asset_class"), row.get("currency"))
        if any(not isinstance(value, str) or not value.strip() for value in details):
            raise DataMappingError(
                "name, asset_class, and currency are required for a new instrument."
            ) from None
        name, asset_class, currency = (str(value).strip() for value in details)
        try:
            return Instrument.objects.create(
                exchange=exchange,
                symbol=symbol,
                name=name,
                asset_class=asset_class,
                currency=currency,
            )
        except (IntegrityError, ValueError) as exc:
            raise DataMappingError(f"Invalid metadata for instrument {symbol}.") from exc

    incoming = (row.get("name"), row.get("asset_class"), row.get("currency"))
    current = (instrument.name, instrument.asset_class, instrument.currency)
    if any(value is not None for value in incoming) and incoming != current:
        raise DataMappingError(f"Conflicting metadata for instrument {symbol}.")
    return instrument


def _parse_ohlcv(
    row: Mapping[str, object],
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal | None, int]:
    open_price = _decimal(row.get("open"), "open")
    high = _decimal(row.get("high"), "high")
    low = _decimal(row.get("low"), "low")
    close = _decimal(row.get("close"), "close")
    adjusted_close = _decimal(row.get("adjusted_close"), "adjusted_close", optional=True)
    volume = _integer(row.get("volume"), "volume")
    if open_price is None or high is None or low is None or close is None:
        raise DataMappingError("OHLC prices are required.")
    if min(open_price, high, low, close) <= 0:
        raise DataMappingError("OHLC prices must be positive.")
    if high < max(open_price, close) or low > min(open_price, close) or high < low:
        raise DataMappingError("OHLC values are inconsistent.")
    return open_price, high, low, close, adjusted_close, volume


def _persist_candle(
    instrument: Instrument,
    trading_date: date,
    timestamp: datetime,
    values: tuple[Decimal, Decimal, Decimal, Decimal, Decimal | None, int],
) -> tuple[DailyOHLCV, str]:
    open_price, high, low, close, adjusted_close, volume = values
    existing = DailyOHLCV.objects.filter(instrument=instrument, trading_date=trading_date).first()
    if existing is not None:
        current = (
            existing.open,
            existing.high,
            existing.low,
            existing.close,
            existing.adjusted_close,
            existing.volume,
        )
        if current != values:
            raise DataMappingError(
                f"Conflicting daily candle for {instrument.symbol} on {trading_date}."
            )
        logger.info(
            "Skipping duplicate daily candle",
            extra={"symbol": instrument.symbol, "trading_date": str(trading_date)},
        )
        return existing, "skipped_duplicate"
    candle = DailyOHLCV.objects.create(
        instrument=instrument,
        trading_date=trading_date,
        timestamp=timestamp,
        open=open_price,
        high=high,
        low=low,
        close=close,
        adjusted_close=adjusted_close,
        volume=volume,
    )
    return candle, "inserted"


class MarketDataMappingService:
    """Map one generator row; return its candle and persistence outcome."""

    @classmethod
    @transaction.atomic
    def map_and_persist_row(
        cls, row: Mapping[str, object], *, allow_instrument_creation: bool = True
    ) -> tuple[DailyOHLCV, str]:
        exchange_code = _required_text(row, "exchange")
        symbol = _required_text(row, "symbol")
        exchange = _resolve_exchange(exchange_code)
        trading_date, timestamp = _resolve_trading_time(row, exchange)
        instrument = _resolve_instrument(row, exchange, symbol, allow_instrument_creation)
        values = _parse_ohlcv(row)
        return _persist_candle(instrument, trading_date, timestamp, values)
