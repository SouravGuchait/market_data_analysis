"""Database-configured validators implemented in application code."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pandas as pd

from apps.ingestion.converters.types import SOURCE_ROW_COLUMN
from apps.ingestion.models import DataType
from apps.instruments.models import Instrument


@dataclass(frozen=True)
class ValidatorSpec:
    """One enabled validator drawn from a rule-set snapshot."""

    field_name: str
    validator: str
    parameters: Mapping[str, object]


REJECTION_COLUMNS = (
    SOURCE_ROW_COLUMN,
    "reason_code",
    "field_name",
    "message",
    "stage",
)


def default_validators(data_type: str) -> list[ValidatorSpec]:
    if data_type == DataType.TICK_DATA:
        return [
            ValidatorSpec("exchange_identifier", "REQUIRED", {}),
            ValidatorSpec("instrument_identifier", "REQUIRED", {}),
            ValidatorSpec("event_timestamp", "DATETIME", {}),
            ValidatorSpec("price", "NUMERIC", {}),
            ValidatorSpec("price", "POSITIVE", {}),
            ValidatorSpec("quantity", "NUMERIC", {}),
            ValidatorSpec("quantity", "POSITIVE", {}),
            ValidatorSpec("", "EXCHANGE_INSTRUMENT_MATCH", {}),
            ValidatorSpec("", "DUPLICATE_CHECK", {}),
        ]
    return [
        ValidatorSpec("exchange_identifier", "REQUIRED", {}),
        ValidatorSpec("instrument_identifier", "REQUIRED", {}),
        ValidatorSpec("trading_date", "REQUIRED", {}),
        ValidatorSpec("open", "NUMERIC", {}),
        ValidatorSpec("high", "NUMERIC", {}),
        ValidatorSpec("low", "NUMERIC", {}),
        ValidatorSpec("close", "NUMERIC", {}),
        ValidatorSpec("volume", "NON_NEGATIVE", {}),
        ValidatorSpec("", "OHLC_RELATIONSHIP", {}),
        ValidatorSpec("", "EXCHANGE_INSTRUMENT_MATCH", {}),
        ValidatorSpec("", "DUPLICATE_CHECK", {}),
    ]


def load_validators(
    data_type: str,
    rule_rows: Sequence[Mapping[str, object]] | None,
) -> list[ValidatorSpec]:
    if not rule_rows:
        return default_validators(data_type)
    specs: list[ValidatorSpec] = []
    for row in rule_rows:
        if not bool(row.get("is_enabled", True)):
            continue
        specs.append(
            ValidatorSpec(
                field_name=str(row.get("field_name", "")),
                validator=str(row.get("validator", "")).upper(),
                parameters=_as_mapping(row.get("parameters")),
            )
        )
    return specs or default_validators(data_type)


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    return {}


def empty_rejection_frame() -> pd.DataFrame:
    data = {column: pd.Series(dtype="object") for column in REJECTION_COLUMNS}
    return pd.DataFrame(data)


def _series(frame: pd.DataFrame, field_name: str) -> pd.Series:
    return pd.Series(frame[field_name], index=frame.index)


def _numeric_series(frame: pd.DataFrame, field_name: str) -> pd.Series:
    converted = pd.to_numeric(_series(frame, field_name), errors="coerce")
    return pd.Series(converted, index=frame.index, dtype="float64")


def _bool_mask(values: object, index: pd.Index) -> pd.Series:
    return pd.Series(values, index=index, dtype="boolean").fillna(False).astype(bool)


def _reject_mask(
    frame: pd.DataFrame,
    mask: pd.Series,
    reason_code: str,
    field_name: str,
    message: str,
) -> pd.DataFrame:
    selected = _bool_mask(mask, frame.index)
    if not bool(selected.any()):
        return empty_rejection_frame()
    rejected = frame.loc[selected, [SOURCE_ROW_COLUMN]].copy()
    rejected["reason_code"] = reason_code
    rejected["field_name"] = field_name
    rejected["message"] = message
    rejected["stage"] = "VALIDATION"
    return rejected


def _required(frame: pd.DataFrame, field_name: str) -> pd.DataFrame:
    series = _series(frame, field_name)
    mask = series.isna() | (series.astype(str).str.strip() == "")
    return _reject_mask(frame, mask, "REQUIRED", field_name, f"{field_name} is required.")


def _numeric(frame: pd.DataFrame, field_name: str) -> pd.DataFrame:
    original = _series(frame, field_name)
    series = _numeric_series(frame, field_name)
    mask = series.isna() & original.notna()
    return _reject_mask(frame, mask, "NUMERIC", field_name, f"{field_name} must be numeric.")


def _positive(frame: pd.DataFrame, field_name: str) -> pd.DataFrame:
    series = _numeric_series(frame, field_name)
    mask = series.notna() & (series <= 0)
    return _reject_mask(frame, mask, "POSITIVE", field_name, f"{field_name} must be > 0.")


def _non_negative(frame: pd.DataFrame, field_name: str) -> pd.DataFrame:
    series = _numeric_series(frame, field_name)
    mask = series.notna() & (series < 0)
    return _reject_mask(frame, mask, "NON_NEGATIVE", field_name, f"{field_name} must be >= 0.")


def _datetime(frame: pd.DataFrame, field_name: str) -> pd.DataFrame:
    original = _series(frame, field_name)
    parsed = pd.to_datetime(original, errors="coerce", utc=True)
    parsed_series = pd.Series(parsed, index=frame.index)
    mask = parsed_series.isna()
    return _reject_mask(
        frame, mask, "DATETIME", field_name, f"{field_name} must be a valid datetime."
    )


def _ohlc(frame: pd.DataFrame) -> pd.DataFrame:
    open_ = _numeric_series(frame, "open")
    high = _numeric_series(frame, "high")
    low = _numeric_series(frame, "low")
    close = _numeric_series(frame, "close")
    valid = open_.notna() & high.notna() & low.notna() & close.notna()
    bad = (high < low) | (high < open_) | (high < close) | (low > open_) | (low > close)
    return _reject_mask(
        frame,
        valid & bad,
        "OHLC_RELATIONSHIP",
        "ohlc",
        "Invalid OHLC relationship.",
    )


def _instrument_lookup(frame: pd.DataFrame) -> set[tuple[str, str]]:
    exchanges = _series(frame, "exchange_identifier").astype(str).str.upper()
    symbols = _series(frame, "instrument_identifier").astype(str).str.upper()
    pairs = {(str(ex), str(sym)) for ex, sym in zip(exchanges, symbols, strict=True)}
    if not pairs:
        return set()
    exchange_codes = {pair[0] for pair in pairs}
    symbols_set = {pair[1] for pair in pairs}
    rows = Instrument.objects.filter(
        exchange__code__in=exchange_codes,
        symbol__in=symbols_set,
        is_active=True,
    ).values_list("exchange__code", "symbol")
    return {(str(code).upper(), str(symbol).upper()) for code, symbol in rows}


def _exchange_instrument_match(frame: pd.DataFrame) -> pd.DataFrame:
    known = _instrument_lookup(frame)
    exchanges = _series(frame, "exchange_identifier").astype(str).str.upper()
    symbols = _series(frame, "instrument_identifier").astype(str).str.upper()
    flags = [(str(ex), str(sym)) not in known for ex, sym in zip(exchanges, symbols, strict=True)]
    return _reject_mask(
        frame,
        pd.Series(flags, index=frame.index),
        "EXCHANGE_INSTRUMENT_MATCH",
        "instrument_identifier",
        "Exchange and instrument combination was not found.",
    )


def _daily_dup_keys(frame: pd.DataFrame) -> pd.Series:
    return (
        _series(frame, "exchange_identifier").astype(str)
        + "|"
        + _series(frame, "instrument_identifier").astype(str)
        + "|"
        + _series(frame, "trading_date").astype(str)
    )


def _tick_dup_keys(frame: pd.DataFrame) -> pd.Series:
    if "deduplication_key" in frame.columns:
        filled = _series(frame, "deduplication_key").astype(str)
        if bool((filled.str.strip() != "").any()):
            return filled
    if "exchange_trade_id" in frame.columns:
        trade_id = _series(frame, "exchange_trade_id").astype(str)
    else:
        trade_id = pd.Series([""] * len(frame), index=frame.index, dtype="object")
    return (
        _series(frame, "exchange_identifier").astype(str)
        + "|"
        + _series(frame, "instrument_identifier").astype(str)
        + "|"
        + trade_id
        + "|"
        + _series(frame, "event_timestamp").astype(str)
    )


def _duplicate_check(frame: pd.DataFrame, data_type: str) -> pd.DataFrame:
    keys = _tick_dup_keys(frame) if data_type == DataType.TICK_DATA else _daily_dup_keys(frame)
    mask = keys.duplicated(keep="first") & keys.notna()
    return _reject_mask(
        frame, mask, "DUPLICATE_CHECK", "deduplication_key", "Duplicate row within chunk."
    )


_FIELD_VALIDATORS = {
    "REQUIRED": _required,
    "NUMERIC": _numeric,
    "POSITIVE": _positive,
    "NON_NEGATIVE": _non_negative,
    "DATETIME": _datetime,
}


def _run_frame_validator(frame: pd.DataFrame, name: str) -> pd.DataFrame | None:
    if name == "OHLC_RELATIONSHIP":
        return _ohlc(frame)
    if name == "EXCHANGE_INSTRUMENT_MATCH":
        return _exchange_instrument_match(frame)
    return None


def _run_spec(frame: pd.DataFrame, spec: ValidatorSpec) -> pd.DataFrame | None:
    if spec.validator in {"OHLC_RELATIONSHIP", "EXCHANGE_INSTRUMENT_MATCH"}:
        return _run_frame_validator(frame, spec.validator)
    handler = _FIELD_VALIDATORS.get(spec.validator)
    if handler is None or spec.field_name not in frame.columns:
        return None
    return handler(frame, spec.field_name)


def _collect_rejections(
    frame: pd.DataFrame,
    specs: Sequence[ValidatorSpec],
) -> pd.DataFrame:
    parts = [part for spec in specs if (part := _run_spec(frame, spec)) is not None]
    if not parts:
        return empty_rejection_frame()
    return pd.concat(parts, ignore_index=True)


def _without_rows(frame: pd.DataFrame, row_numbers: Sequence[object]) -> pd.DataFrame:
    if not row_numbers:
        return frame.copy()
    keep = ~_series(frame, SOURCE_ROW_COLUMN).isin(list(row_numbers))
    return frame.loc[keep].copy()


def _split_duplicates(
    frame: pd.DataFrame,
    data_type: str,
    specs: Sequence[ValidatorSpec],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not any(spec.validator == "DUPLICATE_CHECK" for spec in specs) or frame.empty:
        return frame, empty_rejection_frame()
    duplicates = _duplicate_check(frame, data_type)
    if duplicates.empty:
        return frame, duplicates
    dup_rows = duplicates[SOURCE_ROW_COLUMN].tolist()
    return _without_rows(frame, dup_rows), duplicates


def apply_validators(
    frame: pd.DataFrame,
    data_type: str,
    specs: Sequence[ValidatorSpec],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split into valid, rejected and duplicate DataFrames."""

    if frame.empty:
        empty = frame.copy()
        return empty, empty_rejection_frame(), empty.copy()

    rejected = _collect_rejections(frame, specs)
    rejected_rows = rejected[SOURCE_ROW_COLUMN].tolist() if not rejected.empty else []
    remaining = _without_rows(frame, rejected_rows)
    valid, duplicates = _split_duplicates(remaining, data_type, specs)
    return valid, rejected, duplicates
