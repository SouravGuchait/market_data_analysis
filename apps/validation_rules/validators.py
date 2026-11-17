"""Strict validation for the constrained compiled-rule JSON contract."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import NoReturn

from .schema import (
    ALLOWED_SEVERITIES,
    COLUMN_METADATA,
    MAX_COMPILED_CONFIG_BYTES,
    MAX_RULE_COUNT,
    MAX_RULE_ID_LENGTH,
    MAX_RULE_MESSAGE_LENGTH,
    MAX_RULE_VALUES,
    MAX_STRING_VALUE_LENGTH,
    OPERATOR_EXTRA_FIELDS,
    RULE_BASE_FIELDS,
    SCHEMA_VERSION,
)

_RULE_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_CONSTANT_COMPARISON_OPERATORS = frozenset({"greater_than", "greater_than_or_equal", "less_than"})
_COLUMN_COMPARISON_OPERATORS = frozenset(
    {"greater_than_or_equal_column", "less_than_or_equal_column"}
)
_NUMERIC_TYPES = frozenset({"integer", "number"})
_ORDERABLE_TYPES = frozenset({"date", "datetime"})
_SET_TYPES = frozenset({"date", "datetime", "integer", "number", "string"})


class CompiledConfigValidationError(ValueError):
    """One field-level error in a submitted compiled-rule document."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def _invalid(field: str, message: str) -> NoReturn:
    raise CompiledConfigValidationError(field, message)


def _object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _invalid(field, "Must be a JSON object.")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            _invalid(field, "Object keys must be strings.")
        result[key] = item
    return result


def _check_exact_fields(value: Mapping[str, object], expected: frozenset[str], field: str) -> None:
    missing = expected - value.keys()
    extra = value.keys() - expected
    if missing:
        _invalid(field, f"Missing fields: {', '.join(sorted(missing))}.")
    if extra:
        _invalid(field, f"Unsupported fields: {', '.join(sorted(extra))}.")


def _document_size(document: Mapping[str, object]) -> None:
    try:
        encoded = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError):
        _invalid("compiled_config", "Must contain JSON-compatible finite values.")
    if len(encoded.encode("utf-8")) > MAX_COMPILED_CONFIG_BYTES:
        _invalid("compiled_config", "Must not exceed 65536 UTF-8 bytes.")


def _validate_header(document: Mapping[str, object], expected_asset_type: str) -> list[object]:
    _check_exact_fields(
        document, frozenset({"schema_version", "asset_type", "rules"}), "compiled_config"
    )
    schema_version = document["schema_version"]
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != SCHEMA_VERSION
    ):
        _invalid("schema_version", f"Must equal {SCHEMA_VERSION}.")
    asset_type = document["asset_type"]
    if not isinstance(asset_type, str):
        _invalid("asset_type", "Must be a string.")
    if asset_type != expected_asset_type:
        _invalid("asset_type", "Must match the configuration asset type.")
    rules = document["rules"]
    if not isinstance(rules, list) or not rules:
        _invalid("rules", "Must be a non-empty JSON array.")
    if len(rules) > MAX_RULE_COUNT:
        _invalid("rules", f"Must contain at most {MAX_RULE_COUNT} rules.")
    return rules


def _validate_rule(
    raw_rule: object,
    *,
    asset_type: str,
    index: int,
    seen_rule_ids: set[str],
) -> None:
    field = f"rules[{index}]"
    rule = _object(raw_rule, field)
    operator = _rule_operator(rule, field)
    _check_exact_fields(rule, RULE_BASE_FIELDS | OPERATOR_EXTRA_FIELDS[operator], field)
    _validate_rule_metadata(rule, field, seen_rule_ids)
    _, column_type = _rule_column(rule, field, asset_type)
    _validate_operator_fields(rule, field, operator, column_type, asset_type)


def _rule_operator(rule: Mapping[str, object], field: str) -> str:
    operator = rule.get("operator")
    if not isinstance(operator, str) or operator not in OPERATOR_EXTRA_FIELDS:
        _invalid(f"{field}.operator", "Operator is not supported.")
    return operator


def _validate_rule_metadata(rule: Mapping[str, object], field: str, seen: set[str]) -> None:
    rule_id = rule["rule_id"]
    if not isinstance(rule_id, str) or len(rule_id) > MAX_RULE_ID_LENGTH:
        _invalid(f"{field}.rule_id", "Must be a valid identifier up to 64 characters.")
    if _RULE_ID_PATTERN.fullmatch(rule_id) is None:
        _invalid(
            f"{field}.rule_id",
            "Must start with a letter and contain only letters, digits, or underscores.",
        )
    if rule_id in seen:
        _invalid(f"{field}.rule_id", "Rule identifiers must be unique in one configuration.")
    seen.add(rule_id)

    severity = rule["severity"]
    if not isinstance(severity, str) or severity not in ALLOWED_SEVERITIES:
        _invalid(f"{field}.severity", "Must be 'error' or 'warning'.")
    message = rule["message"]
    if not isinstance(message, str) or not message.strip():
        _invalid(f"{field}.message", "Must be a non-empty string.")
    if len(message) > MAX_RULE_MESSAGE_LENGTH:
        _invalid(f"{field}.message", "Must not exceed 256 characters.")


def _rule_column(rule: Mapping[str, object], field: str, asset_type: str) -> tuple[str, str]:
    columns = COLUMN_METADATA[asset_type]
    column = rule["column"]
    if not isinstance(column, str) or column not in columns:
        _invalid(f"{field}.column", "Column is not allowed for this asset type.")
    return column, columns[column][0]


def _validate_operator_fields(
    rule: Mapping[str, object], field: str, operator: str, column_type: str, asset_type: str
) -> None:
    columns = COLUMN_METADATA[asset_type]
    if operator in _CONSTANT_COMPARISON_OPERATORS:
        if column_type not in _NUMERIC_TYPES | _ORDERABLE_TYPES:
            _invalid(f"{field}.value", "This column type does not support ordering.")
        _validate_scalar(rule["value"], column_type, f"{field}.value")
        return
    if operator in _COLUMN_COMPARISON_OPERATORS:
        compare_column = rule["compare_column"]
        if not isinstance(compare_column, str) or compare_column not in columns:
            _invalid(f"{field}.compare_column", "Column is not allowed for this asset type.")
        if not _compatible_types(column_type, columns[compare_column][0]):
            _invalid(f"{field}.compare_column", "Compared columns must have compatible types.")
        return
    if operator == "in_set":
        _validate_set(rule["values"], column_type, f"{field}.values")


def _compatible_types(left: str, right: str) -> bool:
    if left in _NUMERIC_TYPES and right in _NUMERIC_TYPES:
        return True
    return left == right and left in _ORDERABLE_TYPES


def _validate_set(value: object, value_type: str, field: str) -> None:
    if value_type not in _SET_TYPES:
        _invalid(field, "This column type does not support set membership.")
    if not isinstance(value, list) or not value:
        _invalid(field, "Must be a non-empty JSON array.")
    if len(value) > MAX_RULE_VALUES:
        _invalid(field, f"Must contain at most {MAX_RULE_VALUES} values.")
    for index, item in enumerate(value):
        _validate_scalar(item, value_type, f"{field}[{index}]")


def _validate_scalar(value: object, value_type: str, field: str) -> None:
    if value_type in _NUMERIC_TYPES:
        _validate_numeric(value, value_type, field)
    elif value_type == "string":
        if not isinstance(value, str) or len(value) > MAX_STRING_VALUE_LENGTH:
            _invalid(field, "Must be a string up to 256 characters.")
    elif value_type == "date":
        _validate_date(value, field)
    elif value_type == "datetime":
        _validate_datetime(value, field)
    else:
        _invalid(field, "This column type does not support a constant value.")


def _validate_numeric(value: object, value_type: str, field: str) -> None:
    if isinstance(value, bool):
        _invalid(field, "Must be numeric, not boolean.")
    if value_type == "integer":
        if not isinstance(value, int):
            _invalid(field, "Must be an integer.")
        return
    if not isinstance(value, int | float):
        _invalid(field, "Must be a JSON number.")
    if isinstance(value, float) and not math.isfinite(value):
        _invalid(field, "Must be finite.")


def _validate_date(value: object, field: str) -> None:
    if not isinstance(value, str):
        _invalid(field, "Must be an ISO 8601 date string.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _invalid(field, "Must be an ISO 8601 date string (YYYY-MM-DD).")
    if parsed.isoformat() != value:
        _invalid(field, "Must use YYYY-MM-DD format.")


def _validate_datetime(value: object, field: str) -> None:
    if not isinstance(value, str):
        _invalid(field, "Must be a timezone-aware ISO 8601 datetime string.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _invalid(field, "Must be a valid ISO 8601 datetime.")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _invalid(field, "Datetime constants must include a UTC offset.")


def validate_compiled_config(raw: object, expected_asset_type: str) -> dict[str, object]:
    """Validate and return a constrained JSON document without coercion."""

    document = _object(raw, "compiled_config")
    _document_size(document)
    rules = _validate_header(document, expected_asset_type)
    seen_rule_ids: set[str] = set()
    for index, rule in enumerate(rules):
        _validate_rule(
            rule,
            asset_type=expected_asset_type,
            index=index,
            seen_rule_ids=seen_rule_ids,
        )
    return document
