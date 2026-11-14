"""Batch persistence for tick trade DataFrames."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import pandas as pd
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.ingestion.converters.types import SOURCE_ROW_COLUMN
from apps.ingestion.instrument_lookup import load_instrument_map
from apps.ingestion.models import Ingestion
from apps.instruments.models import Instrument
from apps.market_data.models import Trade


@dataclass(frozen=True)
class TradePersistResult:
    """Counts and conflict rows from one trade batch write."""

    inserted: int
    duplicates: int
    conflicts: pd.DataFrame


@dataclass(frozen=True)
class _DedupeParts:
    ingestion_id: str
    exchange_id: int
    instrument_id: int
    source_row: int
    exchange_trade_id: str
    sequence_number: int | None
    event_timestamp: datetime
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class _ParsedTrade:
    trade: Trade
    key: tuple[int, int, str]


def _text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _as_timestamp(value: object) -> datetime | None:
    stamp = value if isinstance(value, datetime) else None
    if stamp is None and value is not None and not (isinstance(value, float) and pd.isna(value)):
        stamp = parse_datetime(str(value).strip())
    if stamp is None:
        return None
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def _sequence(value: object) -> int | None:
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _dedupe_key(parts: _DedupeParts) -> str:
    if parts.exchange_trade_id:
        material = f"trade_id:{parts.exchange_id}:{parts.instrument_id}:{parts.exchange_trade_id}"
    elif parts.sequence_number is not None:
        material = f"seq:{parts.exchange_id}:{parts.instrument_id}:{parts.sequence_number}"
    else:
        material = (
            f"fallback:{parts.ingestion_id}:{parts.source_row}:"
            f"{parts.event_timestamp.isoformat()}:{parts.price}:{parts.quantity}"
        )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _empty_conflicts() -> pd.DataFrame:
    empty: dict[str, list[object]] = {
        SOURCE_ROW_COLUMN: [],
        "reason_code": [],
        "field_name": [],
        "message": [],
        "stage": [],
    }
    return pd.DataFrame(empty)


def _conflict(source_row: int) -> dict[str, object]:
    return {
        SOURCE_ROW_COLUMN: source_row,
        "reason_code": "PERSIST_MAPPING",
        "field_name": "",
        "message": "Unable to map trade row for persistence.",
        "stage": "PERSISTENCE",
    }


def _build_trade(row: pd.Series, ingestion: Ingestion, instrument: Instrument) -> Trade | None:
    source_row = int(row[SOURCE_ROW_COLUMN])
    event_timestamp = _as_timestamp(row.get("event_timestamp"))
    price = _decimal(row.get("price"))
    quantity = _decimal(row.get("quantity"))
    if event_timestamp is None or price is None or quantity is None:
        return None
    trade_id = _text(row.get("exchange_trade_id"))
    sequence_number = _sequence(row.get("sequence_number"))
    dedupe = _dedupe_key(
        _DedupeParts(
            ingestion_id=str(ingestion.pk),
            exchange_id=instrument.exchange.pk,
            instrument_id=instrument.pk,
            source_row=source_row,
            exchange_trade_id=trade_id,
            sequence_number=sequence_number,
            event_timestamp=event_timestamp,
            price=price,
            quantity=quantity,
        )
    )
    return Trade(
        instrument=instrument,
        exchange=instrument.exchange,
        event_timestamp=event_timestamp,
        price=price,
        quantity=quantity,
        exchange_trade_id=trade_id,
        sequence_number=sequence_number,
        side=_text(row.get("side"))[:16],
        ingestion=ingestion,
        source_file_reference=ingestion.object_key,
        ingestion_timestamp=timezone.now(),
        deduplication_key=dedupe,
        source_metadata={},
    )


def _parse_trades(
    frame: pd.DataFrame,
    ingestion: Ingestion,
) -> tuple[list[_ParsedTrade], list[dict[str, object]]]:
    pairs = [
        (_text(row.get("exchange_identifier")), _text(row.get("instrument_identifier")))
        for _, row in frame.iterrows()
    ]
    instruments = load_instrument_map(pairs)
    prepared: list[_ParsedTrade] = []
    conflicts: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        source_row = int(row[SOURCE_ROW_COLUMN])
        key = (
            _text(row.get("exchange_identifier")).lower(),
            _text(row.get("instrument_identifier")).lower(),
        )
        instrument = instruments.get(key)
        if instrument is None:
            conflicts.append(_conflict(source_row))
            continue
        trade = _build_trade(row, ingestion, instrument)
        if trade is None:
            conflicts.append(_conflict(source_row))
            continue
        prepared.append(
            _ParsedTrade(
                trade,
                (trade.exchange.pk, trade.instrument.pk, trade.deduplication_key),
            )
        )
    return prepared, conflicts


def _split_duplicates(prepared: list[_ParsedTrade]) -> tuple[list[Trade], int]:
    existing_keys = {
        (int(exchange_pk), int(instrument_pk), str(dedupe))
        for exchange_pk, instrument_pk, dedupe in Trade.objects.filter(
            exchange_id__in={item.key[0] for item in prepared},
            instrument_id__in={item.key[1] for item in prepared},
            deduplication_key__in={item.key[2] for item in prepared},
        ).values_list("exchange_id", "instrument_id", "deduplication_key")
    }
    to_create: list[Trade] = []
    duplicates = 0
    seen: set[tuple[int, int, str]] = set()
    for item in prepared:
        if item.key in existing_keys or item.key in seen:
            duplicates += 1
            continue
        seen.add(item.key)
        to_create.append(item.trade)
    return to_create, duplicates


def persist_trade_dataframe(frame: pd.DataFrame, ingestion: Ingestion) -> TradePersistResult:
    """Insert new trades; existing dedupe keys count as duplicates."""

    if frame.empty:
        return TradePersistResult(0, 0, _empty_conflicts())
    prepared, conflicts = _parse_trades(frame, ingestion)
    if not prepared:
        frame_out = pd.DataFrame(conflicts) if conflicts else _empty_conflicts()
        return TradePersistResult(0, 0, frame_out)
    to_create, duplicates = _split_duplicates(prepared)
    if to_create:
        Trade.objects.bulk_create(to_create, batch_size=500)
    conflict_frame = pd.DataFrame(conflicts) if conflicts else _empty_conflicts()
    return TradePersistResult(len(to_create), duplicates, conflict_frame)
