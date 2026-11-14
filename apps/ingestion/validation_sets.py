"""Create a named validation rule set and its field rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .models import ValidationRule, ValidationRuleSet
from .user_services import IngestionRequestError, _request_error


def create_validation_rule_set(data: Mapping[str, object]) -> ValidationRuleSet:
    """Persist one rule set. Name and version together must be unique."""

    name = str(data.get("name", "")).strip()
    version = str(data.get("version") or "1").strip() or "1"
    if ValidationRuleSet.objects.filter(name=name, version=version).exists():
        raise _request_error("name", "A validation rule set with this name and version exists.")
    rule_set = ValidationRuleSet.objects.create(
        name=name,
        data_type=str(data.get("data_type", "")),
        version=version,
    )
    _create_rules(rule_set, data.get("rules"))
    return rule_set


def _create_rules(rule_set: ValidationRuleSet, rules: object) -> None:
    if not isinstance(rules, Sequence):
        raise _request_error("rules", "Rules must be a list.")
    for index, row in enumerate(rules):
        if not isinstance(row, Mapping):
            raise IngestionRequestError(
                "Validation failed", {"rules": ["Each rule must be an object."]}
            )
        _create_rule(rule_set, row, index)


def _create_rule(rule_set: ValidationRuleSet, row: Mapping[str, object], index: int) -> None:
    parameters = row.get("parameters", {})
    ValidationRule.objects.create(
        rule_set=rule_set,
        field_name=str(row.get("field_name", "")),
        validator=str(row.get("validator", "")),
        parameters=parameters if isinstance(parameters, dict) else {},
        is_enabled=bool(row.get("is_enabled", True)),
        sort_order=_sort_order(row.get("sort_order"), index),
    )


def _sort_order(value: object, index: int) -> int:
    if isinstance(value, int) and value >= 0:
        return value
    return index
