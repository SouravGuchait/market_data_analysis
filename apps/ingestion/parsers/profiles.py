"""Canonical column profiles for daily OHLCV and tick data."""

from __future__ import annotations

from apps.ingestion.models import DataType

DAILY_OHLCV_COLUMNS: tuple[str, ...] = (
    "exchange_identifier",
    "instrument_identifier",
    "instrument_name",
    "asset_class",
    "currency",
    "trading_date",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "adjusted_close",
    "volume",
)

TICK_DATA_COLUMNS: tuple[str, ...] = (
    "exchange_identifier",
    "instrument_identifier",
    "event_timestamp",
    "price",
    "quantity",
    "exchange_trade_id",
    "sequence_number",
    "side",
    "source_metadata",
    "deduplication_key",
)

_DAILY_DEFAULT_MAP: dict[str, str] = {
    "exchange": "exchange_identifier",
    "symbol": "instrument_identifier",
    "name": "instrument_name",
    "asset_class": "asset_class",
    "currency": "currency",
    "date": "trading_date",
    "timestamp": "timestamp",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "adjusted_close": "adjusted_close",
    "volume": "volume",
}

_TICK_DEFAULT_MAP: dict[str, str] = {
    "exchange": "exchange_identifier",
    "symbol": "instrument_identifier",
    "timestamp": "event_timestamp",
    "price": "price",
    "size": "quantity",
    "quantity": "quantity",
    "trade_id": "exchange_trade_id",
    "sequence_number": "sequence_number",
    "side": "side",
}


def canonical_columns(data_type: str) -> tuple[str, ...]:
    if data_type == DataType.TICK_DATA:
        return TICK_DATA_COLUMNS
    if data_type == DataType.DAILY_OHLCV:
        return DAILY_OHLCV_COLUMNS
    raise ValueError(f"Unsupported data type: {data_type}")


def default_column_map(data_type: str) -> dict[str, str]:
    if data_type == DataType.TICK_DATA:
        return dict(_TICK_DEFAULT_MAP)
    if data_type == DataType.DAILY_OHLCV:
        return dict(_DAILY_DEFAULT_MAP)
    raise ValueError(f"Unsupported data type: {data_type}")
