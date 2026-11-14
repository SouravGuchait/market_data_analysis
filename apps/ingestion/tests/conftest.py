"""Fixtures for ingestion discovery tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
from django.core.cache import caches
from django.utils import timezone

from apps.ingestion.models import IngestionRule, StorageBackend


@pytest.fixture(autouse=True)
def clear_throttle_cache() -> None:
    """Rate-limit counters live in the cache, which is not rolled back with the database."""

    caches["default"].clear()


@pytest.fixture
def ingestion_root(tmp_path: Path, settings) -> Path:
    root = tmp_path / "ingestion-root"
    root.mkdir()
    settings.INGESTION_LOCAL_ROOT = str(root)
    return root


@pytest.fixture
def inbox(ingestion_root: Path) -> Path:
    directory = ingestion_root / "inbox"
    directory.mkdir()
    return directory


@pytest.fixture
def write_file() -> Callable[[Path, str, str], Path]:
    def _write(
        directory: Path,
        name: str = "ticks.csv",
        content: str = "symbol,price,quantity\nACME,10.50,100\n",
    ) -> Path:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    return _write


@pytest.fixture
def rule(db, inbox: Path) -> IngestionRule:
    return IngestionRule.objects.create(
        name="local-inbox",
        storage_backend=StorageBackend.LOCAL,
        location_reference="inbox",
        file_pattern="*.csv",
        supported_formats=["csv"],
        scan_interval_seconds=1,
        stability_policy={
            "required_stable_scans": 2,
            "min_file_age_seconds": 0,
            "overlap_seconds": 5,
        },
        initial_watermark=timezone.now() - timedelta(days=1),
    )
