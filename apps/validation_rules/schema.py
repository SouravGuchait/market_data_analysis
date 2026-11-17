"""Server-controlled schema metadata for validation-rule documents."""

from __future__ import annotations

from apps.ingestion.models import DataType
from apps.ingestion.parsers.profiles import canonical_columns

SCHEMA_VERSION = 1
MAX_DESCRIPTION_LENGTH = 5000
MAX_COMPILED_CONFIG_BYTES = 65_536
MAX_RULE_COUNT = 100
MAX_RULE_VALUES = 100
MAX_RULE_ID_LENGTH = 64
MAX_RULE_MESSAGE_LENGTH = 256
MAX_STRING_VALUE_LENGTH = 256
ALLOWED_SEVERITIES = ("error", "warning")

ASSET_TYPE_DATA_TYPES = {
    "ohlcv": DataType.DAILY_OHLCV,
    "tick": DataType.TICK_DATA,
}

# Canonical columns come from the ingestion profiles. Types and nullability
# reflect the persisted market-data fields and the parser's canonical values.
COLUMN_METADATA: dict[str, dict[str, tuple[str, bool]]] = {
    "ohlcv": {
        "exchange_identifier": ("string", False),
        "instrument_identifier": ("string", False),
        "instrument_name": ("string", False),
        "asset_class": ("string", False),
        "currency": ("string", False),
        "trading_date": ("date", False),
        "timestamp": ("datetime", False),
        "open": ("number", False),
        "high": ("number", False),
        "low": ("number", False),
        "close": ("number", False),
        "adjusted_close": ("number", True),
        "volume": ("integer", False),
    },
    "tick": {
        "exchange_identifier": ("string", False),
        "instrument_identifier": ("string", False),
        "event_timestamp": ("datetime", False),
        "price": ("number", False),
        "quantity": ("number", False),
        "exchange_trade_id": ("string", False),
        "sequence_number": ("integer", True),
        "side": ("string", False),
        "source_metadata": ("object", False),
        "deduplication_key": ("string", False),
    },
}

RULE_BASE_FIELDS = frozenset({"rule_id", "column", "operator", "severity", "message"})
OPERATOR_EXTRA_FIELDS: dict[str, frozenset[str]] = {
    "not_null": frozenset(),
    "greater_than": frozenset({"value"}),
    "greater_than_or_equal": frozenset({"value"}),
    "less_than": frozenset({"value"}),
    "greater_than_or_equal_column": frozenset({"compare_column"}),
    "less_than_or_equal_column": frozenset({"compare_column"}),
    "in_set": frozenset({"values"}),
}

OPERATOR_MEANINGS = {
    "not_null": "The value must be present; null and blank strings are missing.",
    "greater_than": "The column value must be greater than a typed constant.",
    "greater_than_or_equal": "The column value must be at least a typed constant.",
    "less_than": "The column value must be less than a typed constant.",
    "greater_than_or_equal_column": "The column must be at least another compatible column.",
    "less_than_or_equal_column": "The column must not exceed another compatible column.",
    "in_set": "The column value must match one of the typed constants.",
}


def schema_for_asset_type(asset_type: str) -> dict[str, object]:
    """Return allowed fields and operators for one canonical ingestion asset."""

    data_type = ASSET_TYPE_DATA_TYPES.get(asset_type)
    if data_type is None:
        raise ValueError(f"Unsupported asset type: {asset_type}")

    columns = [
        {
            "name": name,
            "type": COLUMN_METADATA[asset_type][name][0],
            "nullable": COLUMN_METADATA[asset_type][name][1],
        }
        for name in canonical_columns(data_type)
    ]
    operators = [
        {
            "name": name,
            "required_fields": sorted(RULE_BASE_FIELDS | extra_fields),
            "meaning": OPERATOR_MEANINGS[name],
        }
        for name, extra_fields in OPERATOR_EXTRA_FIELDS.items()
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "asset_type": asset_type,
        "columns": columns,
        "operators": operators,
    }
