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


class MarketDataMappingService:
    """Map one generator row; return its candle and persistence outcome."""

    @classmethod
    @transaction.atomic
    def map_and_persist_row(
        cls, row: Mapping[str, Any], *, allow_instrument_creation: bool = True
    ) -> tuple[DailyOHLCV, str]:
        exchange_code = row.get("exchange")
        symbol = row.get("symbol")
        if not isinstance(exchange_code, str) or not exchange_code.strip():
            raise DataMappingError("exchange is required.")
        if not isinstance(symbol, str) or not symbol.strip():
            raise DataMappingError("symbol is required.")
        exchange_code, symbol = exchange_code.strip(), symbol.strip()

        try:
            exchange = Exchange.objects.get(code=exchange_code, is_active=True)
        except Exchange.DoesNotExist as exc:
            raise DataMappingError(f"Unknown or inactive exchange: {exchange_code}.") from exc

        raw_date = row.get("date")
        trading_date = (
            raw_date
            if isinstance(raw_date, date) and not isinstance(raw_date, datetime)
            else parse_date(str(raw_date))
        )
        raw_timestamp = row.get("timestamp")
        timestamp = (
            raw_timestamp
            if isinstance(raw_timestamp, datetime)
            else parse_datetime(str(raw_timestamp))
        )
        if trading_date is None or timestamp is None or timestamp.utcoffset() is None:
            raise DataMappingError("date and timezone-aware timestamp are required.")
        timestamp = timestamp.astimezone(UTC)
        try:
            local_date = timestamp.astimezone(ZoneInfo(exchange.timezone)).date()
        except ZoneInfoNotFoundError as exc:
            raise DataMappingError(f"Invalid timezone configured for {exchange_code}.") from exc
        if local_date != trading_date:
            raise DataMappingError("date does not match the exchange-local timestamp date.")

        asset_class = row.get("asset_class")
        currency = row.get("currency")
        instrument_defaults = {
            "name": row.get("name"),
            "asset_class": asset_class,
            "currency": currency,
        }
        try:
            instrument = Instrument.objects.get(exchange=exchange, symbol=symbol)
        except Instrument.DoesNotExist:
            if not allow_instrument_creation:
                raise DataMappingError(f"Instrument {symbol} is not registered.") from None
            if any(
                not isinstance(value, str) or not value.strip()
                for value in instrument_defaults.values()
            ):
                raise DataMappingError(
                    "name, asset_class, and currency are required for a new instrument."
                ) from None
            try:
                instrument = Instrument.objects.create(
                    exchange=exchange,
                    symbol=symbol,
                    name=str(instrument_defaults["name"]).strip(),
                    asset_class=str(asset_class),
                    currency=str(currency),
                )
            except (IntegrityError, ValueError) as exc:
                raise DataMappingError(f"Invalid metadata for instrument {symbol}.") from exc
        else:
            expected = (instrument.name, instrument.asset_class, instrument.currency)
            incoming = (instrument_defaults["name"], asset_class, currency)
            if any(value is not None for value in incoming) and incoming != expected:
                raise DataMappingError(f"Conflicting metadata for instrument {symbol}.")

        open_price = _decimal(row.get("open"), "open")
        high = _decimal(row.get("high"), "high")
        low = _decimal(row.get("low"), "low")
        close = _decimal(row.get("close"), "close")
        adjusted_close = _decimal(row.get("adjusted_close"), "adjusted_close", optional=True)
        volume = _integer(row.get("volume"), "volume")
        assert open_price is not None and high is not None and low is not None and close is not None
        if min(open_price, high, low, close) <= 0:
            raise DataMappingError("OHLC prices must be positive.")
        if high < max(open_price, close) or low > min(open_price, close) or high < low:
            raise DataMappingError("OHLC values are inconsistent.")

        existing = DailyOHLCV.objects.filter(
            instrument=instrument, trading_date=trading_date
        ).first()
        values = (open_price, high, low, close, adjusted_close, volume)
        if existing is not None:
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
                    "Skipping duplicate daily candle",
                    extra={"symbol": symbol, "trading_date": str(trading_date)},
                )
                return existing, "skipped_duplicate"
            raise DataMappingError(f"Conflicting daily candle for {symbol} on {trading_date}.")

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
