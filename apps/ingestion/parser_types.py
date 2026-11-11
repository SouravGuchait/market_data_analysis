"""Shared tick parser types and row-level validation."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import BinaryIO
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_IDENTITY_CACHE_SIZE = 10_000
MAX_IDENTITY_CACHE_SIZE = 100_000


class FileParseError(ValueError):
    """Raised when a file cannot be interpreted using its configured schema."""


@dataclass(frozen=True)
class TickRecord:
    instrument_identifier: str
    exchange_identifier: str
    event_timestamp: datetime
    price: Decimal
    quantity: Decimal
    ingestion_id: UUID
    source_file_reference: str
    ingestion_timestamp: datetime
    exchange_trade_id: str | None = None
    sequence_number: str | None = None
    side: str | None = None
    source_metadata: dict[str, str] = field(default_factory=dict)
    deduplication_key: str | None = None


@dataclass(frozen=True)
class RowRejection:
    ingestion_id: UUID
    row_number: int
    reason_code: str
    message: str
    rejected_at: datetime


@dataclass(frozen=True)
class ParsedRow:
    record: TickRecord | None = None
    rejection: RowRejection | None = None


@dataclass(frozen=True)
class ParserRequest:
    file_name: str
    stream: BinaryIO
    schema_mapping: Mapping[str, object]
    ingestion_id: UUID
    source_file_reference: str
    ingestion_timestamp: datetime | None = None


@dataclass
class ParserContext:
    columns: dict[str, str]
    schema_mapping: Mapping[str, object]
    ingestion_id: UUID
    source_file_reference: str
    ingestion_timestamp: datetime
    seen_identity_keys: OrderedDict[str, None] = field(default_factory=OrderedDict)


@dataclass(frozen=True)
class NormalizedValues:
    instrument: str
    exchange: str
    timestamp: datetime
    price: Decimal
    quantity: Decimal
    trade_id: str | None
    sequence: str | None
    side: str | None
    deduplication_key: str | None


def _schema_object(schema: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = schema.get(key)
    if not isinstance(value, dict):
        raise FileParseError(f"Schema mapping must define a {key!r} object.")
    return value


def _validate_columns(schema: Mapping[str, object]) -> dict[str, str]:
    columns = _schema_object(schema, "columns")
    required = {
        "instrument_identifier",
        "exchange_identifier",
        "event_timestamp",
        "price",
        "quantity",
    }
    mapped: dict[str, str] = {}
    for key, value in columns.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
            raise FileParseError("Schema mapping column names must be non-empty strings.")
        mapped[key] = value.strip()
    if any(name not in mapped for name in required):
        raise FileParseError("Schema mapping is missing one or more required column names.")
    if len(mapped.values()) != len(set(mapped.values())):
        raise FileParseError("Each canonical field must map to a distinct source column.")
    return mapped


def _validate_precision(schema: Mapping[str, object]) -> None:
    for name in ("price", "quantity"):
        for option in ("minimum", "decimal_places", "max_digits"):
            key = f"{name}_{option}"
            raw_value = schema.get(key)
            if raw_value is not None:
                _validate_precision_value(key, raw_value, option)


def _validate_precision_value(key: str, value: object, option: str) -> None:
    try:
        parsed = Decimal(str(value)) if option == "minimum" else int(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise FileParseError(f"Schema mapping option {key!r} is invalid.") from exc
    if option == "minimum":
        if not isinstance(parsed, Decimal) or not parsed.is_finite() or parsed < 0:
            raise FileParseError(f"Schema mapping option {key!r} must be non-negative and finite.")
        return
    minimum = 1 if option == "max_digits" else 0
    if parsed < minimum:
        raise FileParseError(f"Schema mapping option {key!r} is outside the allowed range.")


def _validate_timezone(schema: Mapping[str, object]) -> None:
    fold = schema.get("ambiguous_time_fold")
    if fold is not None and fold not in (0, 1):
        raise FileParseError("ambiguous_time_fold must be 0 or 1.")
    if "source_timezone" not in schema:
        return
    try:
        ZoneInfo(str(schema["source_timezone"]))
    except ZoneInfoNotFoundError as exc:
        raise FileParseError("Configured source_timezone is invalid.") from exc


def validate_mapping(schema: Mapping[str, object]) -> dict[str, str]:
    """Validate the canonical-to-source column map and numeric/time settings."""

    columns = _validate_columns(schema)
    _validate_precision(schema)
    _validate_timezone(schema)
    cache_size = schema.get("deduplication_cache_size", DEFAULT_IDENTITY_CACHE_SIZE)
    if not isinstance(cache_size, int) or not 1 <= cache_size <= MAX_IDENTITY_CACHE_SIZE:
        raise FileParseError(
            f"deduplication_cache_size must be between 1 and {MAX_IDENTITY_CACHE_SIZE}."
        )
    return columns


def build_context(request: ParserRequest) -> ParserContext:
    """Create the per-file validation context after checking its schema."""

    columns = validate_mapping(request.schema_mapping)
    timestamp = request.ingestion_timestamp or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise FileParseError("Ingestion timestamp must be timezone-aware.")
    return ParserContext(
        columns=columns,
        schema_mapping=request.schema_mapping,
        ingestion_id=request.ingestion_id,
        source_file_reference=request.source_file_reference,
        ingestion_timestamp=timestamp.astimezone(UTC),
    )


def reject(ingestion_id: UUID, row_number: int, reason_code: str, message: str) -> ParsedRow:
    """Build safe, attributable rejection details for one source row."""

    return ParsedRow(
        rejection=RowRejection(
            ingestion_id=ingestion_id,
            row_number=row_number,
            reason_code=reason_code,
            message=message,
            rejected_at=datetime.now(UTC),
        )
    )


def _parse_timestamp(value: object, schema: Mapping[str, object]) -> datetime:
    raw = str(value).strip()
    if not raw:
        raise ValueError("timestamp is missing")
    timestamp_format = schema.get("timestamp_format")
    try:
        parsed = (
            datetime.strptime(raw, str(timestamp_format))
            if timestamp_format
            else datetime.fromisoformat(raw)
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("timestamp is invalid") from exc
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        return parsed.astimezone(UTC)
    return _localize_timestamp(parsed, schema)


def _localize_timestamp(parsed: datetime, schema: Mapping[str, object]) -> datetime:
    zone_name = schema.get("source_timezone")
    if not zone_name:
        raise ValueError("timestamp has no timezone or configured source timezone")
    try:
        zone = ZoneInfo(str(zone_name))
    except ZoneInfoNotFoundError as exc:
        raise ValueError("configured source timezone is invalid") from exc
    fold = schema.get("ambiguous_time_fold")
    first = parsed.replace(tzinfo=zone, fold=0)
    second = parsed.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset() and fold is None:
        raise ValueError("ambiguous local timestamp requires ambiguous_time_fold")
    fold_value = fold if isinstance(fold, int) else 0
    selected = parsed.replace(tzinfo=zone, fold=fold_value)
    if selected.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != parsed:
        raise ValueError("local timestamp does not exist in the configured timezone")
    return selected.astimezone(UTC)


def _parse_decimal(value: object, name: str, schema: Mapping[str, object]) -> Decimal:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise ValueError(f"{name} is not a valid number") from exc
    _validate_decimal(number, name, schema)
    return number


def _validate_decimal(number: Decimal, name: str, schema: Mapping[str, object]) -> None:
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    minimum = Decimal(str(schema.get(f"{name}_minimum", "0")))
    if number < minimum or (name == "price" and number == 0):
        raise ValueError(f"{name} is outside the allowed range")
    exponent = number.as_tuple().exponent
    decimal_places = schema.get(f"{name}_decimal_places")
    if (
        decimal_places is not None
        and isinstance(exponent, int)
        and max(0, -exponent) > int(str(decimal_places))
    ):
        raise ValueError(f"{name} exceeds configured decimal precision")
    max_digits = schema.get(f"{name}_max_digits")
    digit_count = len(number.as_tuple().digits) + max(int(exponent), 0)
    if max_digits is not None and digit_count > int(str(max_digits)):
        raise ValueError(f"{name} exceeds configured precision")


def _source_value(row: Mapping[str, object], context: ParserContext, name: str) -> object | None:
    source = context.columns.get(name)
    return row.get(source) if source else None


def _text_value(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _deduplication_key(
    row: Mapping[str, object], context: ParserContext, exchange: str, instrument: str
) -> str | None:
    trade_id = _text_value(_source_value(row, context, "exchange_trade_id"))
    sequence = _text_value(_source_value(row, context, "sequence_number"))
    if trade_id:
        identity = f"trade:{exchange}:{instrument}:{trade_id}"
    elif sequence:
        identity = f"sequence:{exchange}:{instrument}:{sequence}"
    else:
        identity = _fallback_identity(row, context, exchange, instrument)
    if identity is None:
        return None
    key = hashlib.sha256(identity.encode()).hexdigest()
    if key in context.seen_identity_keys:
        raise ValueError("duplicate trade identity in file")
    context.seen_identity_keys[key] = None
    cache_size = int(
        str(context.schema_mapping.get("deduplication_cache_size", DEFAULT_IDENTITY_CACHE_SIZE))
    )
    if len(context.seen_identity_keys) > cache_size:
        context.seen_identity_keys.popitem(last=False)
    return key


def _fallback_identity(
    row: Mapping[str, object], context: ParserContext, exchange: str, instrument: str
) -> str | None:
    fields = context.schema_mapping.get("deduplication_fields")
    if fields is None:
        return None
    if (
        not isinstance(fields, list)
        or not fields
        or any(not isinstance(name, str) or name not in context.columns for name in fields)
    ):
        raise FileParseError("Configured deduplication_fields must name mapped source fields.")
    values = [_text_value(_source_value(row, context, name)) for name in fields]
    if any(value is None for value in values):
        raise ValueError("configured deduplication fields are incomplete")
    return "fallback:" + "|".join((exchange, instrument, *(value or "" for value in values)))


def _validated_values(row: Mapping[str, object], context: ParserContext) -> NormalizedValues:
    instrument = _text_value(_source_value(row, context, "instrument_identifier"))
    exchange = _text_value(_source_value(row, context, "exchange_identifier"))
    if not instrument or not exchange:
        raise ValueError("instrument and exchange identifiers are required")
    timestamp = _parse_timestamp(
        _source_value(row, context, "event_timestamp") or "", context.schema_mapping
    )
    price = _parse_decimal(
        _source_value(row, context, "price") or "", "price", context.schema_mapping
    )
    quantity = _parse_decimal(
        _source_value(row, context, "quantity") or "", "quantity", context.schema_mapping
    )
    return NormalizedValues(
        instrument=instrument,
        exchange=exchange,
        timestamp=timestamp,
        price=price,
        quantity=quantity,
        trade_id=_text_value(_source_value(row, context, "exchange_trade_id")),
        sequence=_text_value(_source_value(row, context, "sequence_number")),
        side=_text_value(_source_value(row, context, "side")),
        deduplication_key=_deduplication_key(row, context, exchange, instrument),
    )


def normalize_row(row: Mapping[str, object], context: ParserContext, row_number: int) -> ParsedRow:
    """Convert one source row to a canonical tick or an individual rejection."""

    try:
        values = _validated_values(row, context)
    except FileParseError:
        raise
    except (ValueError, TypeError, OverflowError) as exc:
        return reject(context.ingestion_id, row_number, "invalid_row", str(exc))
    record = TickRecord(
        instrument_identifier=values.instrument,
        exchange_identifier=values.exchange,
        event_timestamp=values.timestamp,
        price=values.price,
        quantity=values.quantity,
        ingestion_id=context.ingestion_id,
        source_file_reference=context.source_file_reference,
        ingestion_timestamp=context.ingestion_timestamp,
        exchange_trade_id=values.trade_id,
        sequence_number=values.sequence,
        side=values.side,
        source_metadata={
            "source_row": str(row_number),
            "source_file": context.source_file_reference,
            "source_event_timestamp": str(_source_value(row, context, "event_timestamp")),
        },
        deduplication_key=values.deduplication_key,
    )
    return ParsedRow(record=record)
