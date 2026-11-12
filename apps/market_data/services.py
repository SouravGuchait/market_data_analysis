"""Validation and persistence for synthetic daily market rows."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_date, parse_datetime

from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from apps.market_data.models import DailyOHLCV

logger = logging.getLogger(__name__)


class DataMappingError(ValueError):
    """A source row cannot be mapped to the market-data schema."""


def _decimal(value: Any, field: str, *, optional: bool = False) -> Decimal | None:
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


def _integer(value: Any, field: str) -> int:
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


def _row_text(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DataMappingError(f"{field} is required.")
    return value.strip()


def _active_exchange(code: str) -> Exchange:
    try:
        return Exchange.objects.get(code=code, is_active=True)
    except Exchange.DoesNotExist as exc:
        raise DataMappingError(f"Unknown or inactive exchange: {code}.") from exc


def _parsed_date(raw: object) -> date | None:
    if isinstance(raw, datetime):
        return None
    if isinstance(raw, date):
        return raw
    return parse_date(str(raw))


def _parsed_timestamp(raw: object) -> datetime | None:
    if isinstance(raw, datetime):
        return raw
    return parse_datetime(str(raw))


def _session_date(row: Mapping[str, Any], exchange: Exchange) -> tuple[date, datetime]:
    trading_date = _parsed_date(row.get("date"))
    timestamp = _parsed_timestamp(row.get("timestamp"))
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


def _reject_metadata_conflict(instrument: Instrument, row: Mapping[str, Any]) -> None:
    incoming = (row.get("name"), row.get("asset_class"), row.get("currency"))
    expected = (instrument.name, instrument.asset_class, instrument.currency)
    if any(value is not None for value in incoming) and incoming != expected:
        raise DataMappingError(f"Conflicting metadata for instrument {instrument.symbol}.")


def _create_listed_instrument(
    exchange: Exchange, symbol: str, row: Mapping[str, Any], *, allow_instrument_creation: bool
) -> Instrument:
    if not allow_instrument_creation:
        raise DataMappingError(f"Instrument {symbol} is not registered.")
    name = row.get("name")
    asset_class = row.get("asset_class")
    currency = row.get("currency")
    supplied = (name, asset_class, currency)
    if any(not isinstance(value, str) or not value.strip() for value in supplied):
        raise DataMappingError("name, asset_class, and currency are required for a new instrument.")
    try:
        return Instrument.objects.create(
            exchange=exchange,
            symbol=symbol,
            name=str(name).strip(),
            asset_class=str(asset_class),
            currency=str(currency),
        )
    except (IntegrityError, ValueError) as exc:
        raise DataMappingError(f"Invalid metadata for instrument {symbol}.") from exc


def _instrument_for_row(
    exchange: Exchange, symbol: str, row: Mapping[str, Any], *, allow_instrument_creation: bool
) -> Instrument:
    try:
        instrument = Instrument.objects.get(exchange=exchange, symbol=symbol)
    except Instrument.DoesNotExist:
        return _create_listed_instrument(
            exchange, symbol, row, allow_instrument_creation=allow_instrument_creation
        )
    _reject_metadata_conflict(instrument, row)
    return instrument


def _required_price(row: Mapping[str, Any], field: str) -> Decimal:
    value = _decimal(row.get(field), field)
    if value is None or value <= 0:
        raise DataMappingError("OHLC prices must be positive.")
    return value


def _check_ohlc_order(open_price: Decimal, high: Decimal, low: Decimal, close: Decimal) -> None:
    if high < max(open_price, close):
        raise DataMappingError("OHLC values are inconsistent.")
    if low > min(open_price, close) or high < low:
        raise DataMappingError("OHLC values are inconsistent.")


def _ohlc_values(
    row: Mapping[str, Any],
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal | None, int]:
    open_price = _required_price(row, "open")
    high = _required_price(row, "high")
    low = _required_price(row, "low")
    close = _required_price(row, "close")
    adjusted_close = _decimal(row.get("adjusted_close"), "adjusted_close", optional=True)
    _check_ohlc_order(open_price, high, low, close)
    return open_price, high, low, close, adjusted_close, _integer(row.get("volume"), "volume")


def _store_candle(
    instrument: Instrument,
    trading_date: date,
    timestamp: datetime,
    values: tuple[Decimal, Decimal, Decimal, Decimal, Decimal | None, int],
) -> tuple[DailyOHLCV, str]:
    existing = DailyOHLCV.objects.filter(instrument=instrument, trading_date=trading_date).first()
    open_price, high, low, close, adjusted_close, volume = values
    if existing is None:
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
    current = (
        existing.open,
        existing.high,
        existing.low,
        existing.close,
        existing.adjusted_close,
        existing.volume,
    )
    if current == values:
        logger.info(
            "Skipping duplicate daily candle for %s on %s",
            instrument.symbol,
            trading_date,
        )
        return existing, "skipped_duplicate"
    raise DataMappingError(f"Conflicting daily candle for {instrument.symbol} on {trading_date}.")


class MarketDataMappingService:
    """Map one generator row; return its candle and persistence outcome."""

    @classmethod
    @transaction.atomic
    def map_and_persist_row(
        cls, row: Mapping[str, Any], *, allow_instrument_creation: bool = False
    ) -> tuple[DailyOHLCV, str]:
        exchange = _active_exchange(_row_text(row, "exchange"))
        symbol = _row_text(row, "symbol")
        trading_date, timestamp = _session_date(row, exchange)
        instrument = _instrument_for_row(
            exchange, symbol, row, allow_instrument_creation=allow_instrument_creation
        )
        return _store_candle(instrument, trading_date, timestamp, _ohlc_values(row))


def resolve_listed_instrument(exchange_code: str, symbol: str) -> Instrument:
    """Resolve one shared listing. Ingestion must not create master data."""

    code = exchange_code.strip()
    ticker = symbol.strip()
    if not code:
        raise DataMappingError("exchange is required.")
    if not ticker:
        raise DataMappingError("symbol is required.")
    try:
        exchange = Exchange.objects.get(code=code, is_active=True)
    except Exchange.DoesNotExist as exc:
        raise DataMappingError(f"Unknown or inactive exchange: {code}.") from exc
    instrument = Instrument.objects.filter(
        exchange=exchange, symbol__iexact=ticker, is_active=True
    ).first()
    if instrument is None:
        raise DataMappingError(f"Instrument {ticker} is not registered.")
    return instrument
