"""Unit tests for the constrained compiled-rule schema."""

from __future__ import annotations

import pytest

from apps.ingestion.models import DataType
from apps.ingestion.parsers.profiles import canonical_columns
from apps.validation_rules.schema import schema_for_asset_type
from apps.validation_rules.validators import (
    CompiledConfigValidationError,
    validate_compiled_config,
)


def valid_ohlcv_config() -> dict[str, object]:
    rules: list[dict[str, object]] = [
        {
            "rule_id": "close_positive",
            "column": "close",
            "operator": "greater_than",
            "value": 0,
            "severity": "error",
            "message": "close must be greater than zero",
        },
        {
            "rule_id": "high_gte_low",
            "column": "high",
            "operator": "greater_than_or_equal_column",
            "compare_column": "low",
            "severity": "error",
            "message": "high must be greater than or equal to low",
        },
    ]
    return {"schema_version": 1, "asset_type": "ohlcv", "rules": rules}


def assert_invalid_config(config: object, expected_field: str, asset_type: str = "ohlcv") -> None:
    with pytest.raises(CompiledConfigValidationError) as error:
        validate_compiled_config(config, asset_type)
    assert error.value.field == expected_field


def test_valid_ohlcv_rule_document_is_preserved() -> None:
    document = valid_ohlcv_config()

    assert validate_compiled_config(document, "ohlcv") == document


def test_valid_tick_rules_support_typed_datetime_and_set_values() -> None:
    document = {
        "schema_version": 1,
        "asset_type": "tick",
        "rules": [
            {
                "rule_id": "price_positive",
                "column": "price",
                "operator": "greater_than",
                "value": 0,
                "severity": "error",
                "message": "price must be positive",
            },
            {
                "rule_id": "event_time_after",
                "column": "event_timestamp",
                "operator": "greater_than",
                "value": "2026-01-01T00:00:00Z",
                "severity": "warning",
                "message": "event time must be recent",
            },
            {
                "rule_id": "side_allowed",
                "column": "side",
                "operator": "in_set",
                "values": ["buy", "sell"],
                "severity": "error",
                "message": "side must be recognized",
            },
        ],
    }

    assert validate_compiled_config(document, "tick") == document


def test_schema_columns_match_canonical_ingestion_profiles() -> None:
    ohlcv_schema = schema_for_asset_type("ohlcv")
    tick_schema = schema_for_asset_type("tick")
    ohlcv_columns = ohlcv_schema["columns"]
    tick_columns = tick_schema["columns"]
    assert isinstance(ohlcv_columns, list)
    assert isinstance(tick_columns, list)
    assert [column["name"] for column in ohlcv_columns] == list(
        canonical_columns(DataType.DAILY_OHLCV)
    )
    assert [column["name"] for column in tick_columns] == list(
        canonical_columns(DataType.TICK_DATA)
    )
    assert ohlcv_schema["schema_version"] == tick_schema["schema_version"] == 1


def test_schema_rejects_unknown_asset_type() -> None:
    with pytest.raises(ValueError, match="Unsupported asset type"):
        schema_for_asset_type("portfolio")


def test_unknown_column_and_operator_are_rejected() -> None:
    unknown_column = valid_ohlcv_config()
    rules = unknown_column["rules"]
    assert isinstance(rules, list)
    first = rules[0]
    assert isinstance(first, dict)
    first["column"] = "event_timestamp"
    assert_invalid_config(unknown_column, "rules[0].column")

    unsupported_operator = valid_ohlcv_config()
    unsupported_rules = unsupported_operator["rules"]
    assert isinstance(unsupported_rules, list)
    unsupported_first = unsupported_rules[0]
    assert isinstance(unsupported_first, dict)
    unsupported_first["operator"] = "execute_python"
    assert_invalid_config(unsupported_operator, "rules[0].operator")


def test_asset_type_mismatch_and_boolean_numeric_constant_are_rejected() -> None:
    assert_invalid_config(valid_ohlcv_config(), "asset_type", asset_type="tick")

    config = valid_ohlcv_config()
    rules = config["rules"]
    assert isinstance(rules, list)
    first = rules[0]
    assert isinstance(first, dict)
    first["value"] = True
    assert_invalid_config(config, "rules[0].value")


def test_incompatible_cross_column_and_string_ordering_are_rejected() -> None:
    config = valid_ohlcv_config()
    rules = config["rules"]
    assert isinstance(rules, list)
    second = rules[1]
    assert isinstance(second, dict)
    second["compare_column"] = "trading_date"
    assert_invalid_config(config, "rules[1].compare_column")

    string_ordering = {
        "schema_version": 1,
        "asset_type": "ohlcv",
        "rules": [
            {
                "rule_id": "symbol_order",
                "column": "instrument_identifier",
                "operator": "greater_than",
                "value": "A",
                "severity": "error",
                "message": "symbol ordering is unsupported",
            }
        ],
    }
    assert_invalid_config(string_ordering, "rules[0].value")


def test_duplicate_rule_ids_and_extra_fields_are_rejected() -> None:
    duplicate = valid_ohlcv_config()
    rules = duplicate["rules"]
    assert isinstance(rules, list)
    first = rules[0]
    second = rules[1]
    assert isinstance(first, dict)
    assert isinstance(second, dict)
    second["rule_id"] = first["rule_id"]
    assert_invalid_config(duplicate, "rules[1].rule_id")

    extra = valid_ohlcv_config()
    extra["python"] = "open('/tmp/file')"
    assert_invalid_config(extra, "compiled_config")


def test_datetime_requires_timezone_and_schema_version_requires_integer() -> None:
    naive_datetime = {
        "schema_version": 1,
        "asset_type": "tick",
        "rules": [
            {
                "rule_id": "timestamp_after",
                "column": "event_timestamp",
                "operator": "greater_than",
                "value": "2026-01-01T00:00:00",
                "severity": "error",
                "message": "timestamp must include a timezone",
            }
        ],
    }
    assert_invalid_config(naive_datetime, "rules[0].value", asset_type="tick")

    wrong_version = valid_ohlcv_config()
    wrong_version["schema_version"] = 1.0
    assert_invalid_config(wrong_version, "schema_version")


def test_rule_count_and_json_payload_limits_are_enforced() -> None:
    too_many = {
        "schema_version": 1,
        "asset_type": "ohlcv",
        "rules": [
            {
                "rule_id": f"rule_{index}",
                "column": "close",
                "operator": "greater_than",
                "value": index,
                "severity": "error",
                "message": "valid close",
            }
            for index in range(101)
        ],
    }
    assert_invalid_config(too_many, "rules")

    oversized = {
        "schema_version": 1,
        "asset_type": "ohlcv",
        "rules": [
            {
                "rule_id": f"set_{rule_index}",
                "column": "instrument_identifier",
                "operator": "in_set",
                "values": ["x" * 256 for _ in range(100)],
                "severity": "error",
                "message": "symbols are allowlisted",
            }
            for rule_index in range(3)
        ],
    }
    assert_invalid_config(oversized, "compiled_config")
