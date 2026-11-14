"""Celery task boundaries and Beat configuration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.utils import timezone

from apps.ingestion.models import Ingestion, IngestionState
from apps.ingestion.services import source_storage_key
from apps.ingestion.tasks import process_ingestion_file, scan_enabled_ingestion_rules

# Celery's decorator return type is opaque to the type checker in this project.
worker_task: Any = process_ingestion_file


def test_beat_schedule_points_to_the_discovery_task() -> None:
    entry = settings.CELERY_BEAT_SCHEDULE["scan-enabled-ingestion-rules"]

    assert entry["task"] == "apps.ingestion.tasks.scan_enabled_ingestion_rules"
    assert entry["schedule"] == timedelta(seconds=settings.INGESTION_SCAN_INTERVAL_SECONDS)


@pytest.mark.django_db
def test_discovery_task_returns_per_rule_summaries(rule, inbox, write_file) -> None:
    write_file(inbox, "ticks.csv")

    results = scan_enabled_ingestion_rules()

    assert len(results) == 1
    assert results[0]["rule_name"] == rule.name
    assert results[0]["status"] == "success"


@pytest.mark.django_db
def test_worker_boundary_accepts_a_known_ingestion(rule) -> None:
    ingestion = Ingestion.objects.create(
        rule=rule,
        storage_backend=rule.storage_backend,
        object_key="ticks.csv",
        file_name="ticks.csv",
        file_size_bytes=10,
        file_modified_at=timezone.now(),
        content_hash="b" * 64,
        state=IngestionState.QUEUED,
    )

    result = worker_task.apply(args=[str(ingestion.pk)], task_id="task-abc")
    result.get()

    ingestion.refresh_from_db()
    assert ingestion.state == IngestionState.QUEUED
    assert ingestion.celery_task_id == "task-abc"


@pytest.mark.django_db
def test_source_key_uses_the_rule_location_when_no_storage_file_exists(rule) -> None:
    ingestion = Ingestion(
        rule=rule,
        storage_backend=rule.storage_backend,
        object_key="ticks.csv",
        file_name="ticks.csv",
    )

    assert source_storage_key(ingestion) == "inbox/ticks.csv"


@pytest.mark.django_db
def test_worker_boundary_ignores_unknown_ingestion() -> None:
    result = worker_task.apply(args=["00000000-0000-0000-0000-000000000000"])
    result.get()
