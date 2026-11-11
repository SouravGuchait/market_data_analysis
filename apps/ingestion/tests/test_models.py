"""Model invariants for ingestion configuration and records."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.ingestion.models import (
    Ingestion,
    IngestionConfigurationError,
    IngestionRule,
    IngestionState,
    StorageBackend,
    resolve_stability_policy,
)


def _rule(**overrides):
    values = {
        "name": "validation-rule",
        "storage_backend": StorageBackend.LOCAL,
        "location_reference": "inbox",
        "file_pattern": "*.csv",
        "supported_formats": ["csv"],
        "scan_interval_seconds": 60,
        "stability_policy": {"required_stable_scans": 2},
    }
    values.update(overrides)
    return IngestionRule(**values)


@pytest.mark.django_db
def test_valid_rule_passes_full_clean() -> None:
    _rule().full_clean()


@pytest.mark.parametrize(
    "overrides",
    [
        {"supported_formats": ["csv", "parquet"]},
        {"supported_formats": []},
        {"location_reference": "/absolute/path"},
        {"location_reference": "../escape"},
        {"stability_policy": {"required_stable_scans": 0}},
        {"stability_policy": {"required_stable_scans": "many"}},
        {"file_pattern": "  "},
        {"file_pattern": "../*.csv"},
        {"file_pattern": "/etc/*.csv"},
    ],
)
@pytest.mark.django_db
def test_invalid_rule_configuration_is_rejected(overrides) -> None:
    with pytest.raises(ValidationError):
        _rule(**overrides).full_clean()


def test_stability_policy_defaults_and_overrides() -> None:
    defaults = resolve_stability_policy(None)
    assert defaults.required_stable_scans == 2
    assert defaults.min_file_age_seconds == 0
    assert defaults.overlap_seconds == 0

    configured = resolve_stability_policy(
        {"required_stable_scans": 3, "min_file_age_seconds": 10, "overlap_seconds": 30}
    )
    assert configured.required_stable_scans == 3
    assert configured.min_file_age_seconds == 10
    assert configured.overlap_seconds == 30


def test_non_object_stability_policy_is_rejected() -> None:
    with pytest.raises(IngestionConfigurationError):
        resolve_stability_policy(["bad"])  # type: ignore[arg-type]


@pytest.mark.django_db
def test_ingestion_uniqueness_blocks_duplicate_content(rule) -> None:
    now = timezone.now()
    common = {
        "rule": rule,
        "storage_backend": rule.storage_backend,
        "object_key": "ticks.csv",
        "file_name": "ticks.csv",
        "file_size_bytes": 10,
        "file_modified_at": now,
        "content_hash": "a" * 64,
        "state": IngestionState.QUEUED,
    }
    Ingestion.objects.create(**common)

    with pytest.raises(IntegrityError), transaction.atomic():
        Ingestion.objects.create(**common)


@pytest.mark.django_db
def test_rule_scan_watermark_and_historical_boundary_can_be_configured(rule) -> None:
    watermark = timezone.now() - timedelta(hours=2)
    rule.initial_watermark = watermark
    rule.save(update_fields=["initial_watermark", "updated_at"])

    assert rule.initial_watermark == watermark
