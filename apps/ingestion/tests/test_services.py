"""Discovery, stability, deduplication and recovery behaviour."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.audit.models import AuditAction, AuditEvent, AuditStatus
from apps.ingestion.models import (
    Ingestion,
    IngestionObjectState,
    IngestionRule,
    IngestionState,
    IngestionStateTransition,
    ScanStatus,
    StorageBackend,
)
from apps.ingestion.services import (
    RuleScanResult,
    rule_is_due,
    scan_enabled_rules,
    scan_ingestion_rule,
)
from apps.ingestion.storage import LocalStorageAdapter


def _make_rule(**overrides) -> IngestionRule:
    values = {
        "name": "test-inbox",
        "storage_backend": StorageBackend.LOCAL,
        "location_reference": "inbox",
        "file_pattern": "*.csv",
        "supported_formats": ["csv"],
        "scan_interval_seconds": 60,
        "stability_policy": {
            "required_stable_scans": 2,
            "min_file_age_seconds": 0,
            "overlap_seconds": 5,
        },
        "initial_watermark": timezone.now() - timedelta(days=1),
    }
    values.update(overrides)
    return IngestionRule.objects.create(**values)


def _set_mtime(path: Path, value) -> None:
    timestamp = value.timestamp()
    os.utime(path, (timestamp, timestamp))


@pytest.mark.django_db
def test_stable_file_is_queued_on_the_second_scan(
    rule,
    inbox,
    write_file,
    django_capture_on_commit_callbacks,
) -> None:
    path = write_file(inbox, "ticks.csv")
    base = timezone.now() - timedelta(minutes=10)
    _set_mtime(path, base)

    first = scan_ingestion_rule(rule, now=base + timedelta(seconds=1))

    assert first.status == ScanStatus.SUCCESS.value
    assert first.listed == 1
    assert first.unstable == 1
    assert first.queued == 0
    assert Ingestion.objects.count() == 0
    state = IngestionObjectState.objects.get(rule=rule)
    assert state.stable_scan_count == 1
    assert state.is_historical is False

    with django_capture_on_commit_callbacks(execute=True):
        second = scan_ingestion_rule(rule, now=base + timedelta(seconds=2))

    assert second.queued == 1
    ingestion = Ingestion.objects.get()
    assert ingestion.state == IngestionState.QUEUED
    assert ingestion.rule == rule
    assert ingestion.file_name == "ticks.csv"
    assert ingestion.content_hash == hashlib.sha256(path.read_bytes()).hexdigest()
    assert ingestion.celery_task_id
    assert list(
        IngestionStateTransition.objects.filter(ingestion=ingestion)
        .order_by("created_at", "id")
        .values_list("to_state", flat=True)
    ) == [IngestionState.DISCOVERED, IngestionState.QUEUED]

    state.refresh_from_db()
    assert state.last_processed_content_hash == ingestion.content_hash

    scan_started = AuditEvent.objects.filter(
        action=AuditAction.INGESTION_STARTED,
        resource_id=str(rule.pk),
    ).first()
    assert scan_started is not None
    assert scan_started.actor is None
    assert scan_started.metadata["scope"] == "scan"
    assert scan_started.metadata["system"] is True
    assert AuditEvent.objects.filter(
        action=AuditAction.INGESTION_COMPLETED,
        resource_id=str(rule.pk),
        status=AuditStatus.SUCCESS,
    ).exists()


@pytest.mark.django_db
def test_multiple_formats_are_listed_stabilized_and_dispatched_independently(
    rule,
    inbox,
    write_file,
    django_capture_on_commit_callbacks,
) -> None:
    rule.file_pattern = "*"
    rule.supported_formats = ["csv", "json", "xlsx"]
    rule.save(update_fields=["file_pattern", "supported_formats", "updated_at"])

    names = [
        f"market-{index:02}.{extension}"
        for index, extension in enumerate(("csv", "json", "xlsx") * 4)
    ]
    base = timezone.now() - timedelta(minutes=10)
    for name in names:
        path = write_file(inbox, name)
        _set_mtime(path, base)

    first = scan_ingestion_rule(rule, now=base + timedelta(seconds=1))

    assert first.status == ScanStatus.SUCCESS.value
    assert first.listed == 12
    assert first.unstable == 12
    assert first.queued == 0
    assert IngestionObjectState.objects.filter(rule=rule).count() == 12
    assert set(
        IngestionObjectState.objects.filter(rule=rule).values_list(
            "stable_scan_count", flat=True
        )
    ) == {1}
    rule.refresh_from_db()
    assert rule.last_scan_watermark == base + timedelta(seconds=1)
    assert Ingestion.objects.filter(rule=rule).count() == 0

    dispatched: list[str] = []

    def dispatch(ingestion_id: str) -> SimpleNamespace:
        ingestion = Ingestion.objects.get(pk=ingestion_id)
        dispatched.append(ingestion.file_name)
        return SimpleNamespace(id=f"task-{len(dispatched)}")

    with (
        patch("apps.ingestion.tasks.process_ingestion_file.delay", side_effect=dispatch),
        django_capture_on_commit_callbacks(execute=True),
    ):
        second = scan_ingestion_rule(rule, now=base + timedelta(seconds=2))

    assert second.status == ScanStatus.SUCCESS.value
    assert second.listed == 12
    assert second.queued == 12
    assert second.unstable == 0
    ingestions = list(Ingestion.objects.filter(rule=rule).order_by("file_name"))
    assert len(ingestions) == 12
    assert {ingestion.file_name.rsplit(".", 1)[1] for ingestion in ingestions} == {
        "csv",
        "json",
        "xlsx",
    }
    assert all(ingestion.state == IngestionState.QUEUED for ingestion in ingestions)
    assert all(ingestion.celery_task_id for ingestion in ingestions)
    assert dispatched == sorted(names, key=str.casefold)
    assert IngestionStateTransition.objects.filter(
        ingestion__rule=rule,
        from_state="",
        to_state=IngestionState.DISCOVERED,
    ).count() == 12
    assert IngestionStateTransition.objects.filter(
        ingestion__rule=rule,
        from_state=IngestionState.DISCOVERED,
        to_state=IngestionState.QUEUED,
    ).count() == 12
    assert AuditEvent.objects.filter(
        action=AuditAction.INGESTION_STARTED,
        status=AuditStatus.ACCEPTED,
        resource_id__in=[str(ingestion.pk) for ingestion in ingestions],
        metadata__system=True,
    ).count() == 12


@pytest.mark.django_db
def test_hash_revalidation_resets_stability_when_file_changes_during_hash(
    rule,
    inbox,
    write_file,
    monkeypatch,
    django_capture_on_commit_callbacks,
) -> None:
    path = write_file(inbox, "ticks.csv")
    base = timezone.now() - timedelta(minutes=10)
    _set_mtime(path, base)
    scan_ingestion_rule(rule, now=base + timedelta(seconds=1))

    original_hash = LocalStorageAdapter.content_hash
    changed = False

    def _hash_then_mutate(self, key: str) -> str:
        nonlocal changed
        digest = original_hash(self, key)
        if not changed:
            changed = True
            path.write_text("symbol,price,quantity\\nACME,11.00,100\\n", encoding="utf-8")
            _set_mtime(path, base + timedelta(seconds=2))
        return digest

    monkeypatch.setattr(LocalStorageAdapter, "content_hash", _hash_then_mutate)
    with django_capture_on_commit_callbacks(execute=True):
        result = scan_ingestion_rule(rule, now=base + timedelta(seconds=3))

    assert result.unstable == 1
    assert result.queued == 0
    assert Ingestion.objects.filter(rule=rule).count() == 0
    state = IngestionObjectState.objects.get(rule=rule)
    assert state.stable_scan_count == 1
    assert state.observed_size_bytes == path.stat().st_size
    expected_mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    assert state.observed_modified_at == expected_mtime


@pytest.mark.django_db
def test_same_content_is_not_ingested_twice(
    rule,
    inbox,
    write_file,
    django_capture_on_commit_callbacks,
) -> None:
    path = write_file(inbox, "ticks.csv")
    base = timezone.now() - timedelta(minutes=10)
    _set_mtime(path, base)

    scan_ingestion_rule(rule, now=base + timedelta(seconds=1))
    with django_capture_on_commit_callbacks(execute=True):
        scan_ingestion_rule(rule, now=base + timedelta(seconds=2))

    with django_capture_on_commit_callbacks(execute=True):
        third = scan_ingestion_rule(rule, now=base + timedelta(seconds=3))

    assert third.duplicates == 1
    assert Ingestion.objects.count() == 1


@pytest.mark.django_db
def test_changed_content_creates_a_new_ingestion(
    rule,
    inbox,
    write_file,
    django_capture_on_commit_callbacks,
) -> None:
    path = write_file(inbox, "ticks.csv")
    base = timezone.now() - timedelta(minutes=10)
    _set_mtime(path, base)

    scan_ingestion_rule(rule, now=base + timedelta(seconds=1))
    with django_capture_on_commit_callbacks(execute=True):
        scan_ingestion_rule(rule, now=base + timedelta(seconds=2))

    path.write_text("symbol,price,quantity\nACME,11.25,200\n", encoding="utf-8")
    _set_mtime(path, base + timedelta(seconds=3))

    unstable = scan_ingestion_rule(rule, now=base + timedelta(seconds=4))
    assert unstable.unstable == 1

    with django_capture_on_commit_callbacks(execute=True):
        queued = scan_ingestion_rule(rule, now=base + timedelta(seconds=5))

    assert queued.queued == 1
    assert Ingestion.objects.count() == 2
    assert Ingestion.objects.values("content_hash").distinct().count() == 2


@pytest.mark.django_db
def test_file_still_being_copied_resets_stability(
    rule,
    inbox,
    write_file,
    django_capture_on_commit_callbacks,
) -> None:
    path = write_file(inbox, "ticks.csv")
    base = timezone.now() - timedelta(minutes=10)
    _set_mtime(path, base)

    scan_ingestion_rule(rule, now=base + timedelta(seconds=1))

    path.write_text(
        "symbol,price,quantity\nACME,10.50,100\nACME,10.55,50\n",
        encoding="utf-8",
    )
    _set_mtime(path, base + timedelta(seconds=2))

    changed = scan_ingestion_rule(rule, now=base + timedelta(seconds=3))
    assert changed.unstable == 1
    assert Ingestion.objects.count() == 0
    assert IngestionObjectState.objects.get(rule=rule).stable_scan_count == 1

    with django_capture_on_commit_callbacks(execute=True):
        stable = scan_ingestion_rule(rule, now=base + timedelta(seconds=4))
    assert stable.queued == 1


@pytest.mark.django_db
def test_historical_file_is_not_ingested(
    rule,
    inbox,
    write_file,
) -> None:
    base = timezone.now() - timedelta(hours=2)
    rule.initial_watermark = base + timedelta(minutes=30)
    rule.save(update_fields=["initial_watermark", "updated_at"])
    path = write_file(inbox, "ticks.csv")
    _set_mtime(path, base)

    first = scan_ingestion_rule(rule, now=base + timedelta(hours=1))
    second = scan_ingestion_rule(rule, now=base + timedelta(hours=1, seconds=1))

    assert first.historical == 1
    assert second.historical == 1
    assert Ingestion.objects.count() == 0
    assert IngestionObjectState.objects.get(rule=rule).is_historical is True


@pytest.mark.django_db
def test_rule_creation_time_is_the_default_historical_boundary(
    inbox,
    write_file,
) -> None:
    rule = _make_rule(initial_watermark=None)
    path = write_file(inbox, "ticks.csv")
    _set_mtime(path, timezone.now() - timedelta(hours=1))

    result = scan_ingestion_rule(rule)

    assert result.historical == 1
    assert Ingestion.objects.count() == 0


@pytest.mark.django_db
def test_scan_failure_does_not_advance_the_watermark_and_can_recover(
    ingestion_root: Path,
    write_file,
    django_capture_on_commit_callbacks,
) -> None:
    rule = _make_rule(location_reference="missing")
    base = timezone.now() - timedelta(minutes=10)

    failed = scan_ingestion_rule(rule, now=base + timedelta(seconds=1))

    assert failed.status == ScanStatus.FAILED.value
    assert failed.errors == 1
    rule.refresh_from_db()
    assert rule.last_scan_status == ScanStatus.FAILED
    assert rule.last_scan_watermark is None
    assert AuditEvent.objects.filter(
        action=AuditAction.INGESTION_FAILED,
        resource_id=str(rule.pk),
        status=AuditStatus.FAILURE,
    ).exists()

    location = ingestion_root / "missing"
    location.mkdir()
    path = write_file(location, "ticks.csv")
    _set_mtime(path, base)

    scan_ingestion_rule(rule, now=base + timedelta(seconds=2))
    with django_capture_on_commit_callbacks(execute=True):
        recovered = scan_ingestion_rule(rule, now=base + timedelta(seconds=3))

    assert recovered.status == ScanStatus.SUCCESS.value
    assert recovered.queued == 1
    rule.refresh_from_db()
    assert rule.last_scan_watermark is not None


@pytest.mark.django_db
def test_not_due_and_disabled_rules_are_skipped(
    rule,
    inbox,
    write_file,
) -> None:
    now = timezone.now()
    rule.last_scan_completed_at = now
    rule.save(update_fields=["last_scan_completed_at", "updated_at"])

    assert rule_is_due(rule, now) is False
    not_due = scan_enabled_rules(now=now)
    assert [result.status for result in not_due] == ["not_due"]

    rule.is_enabled = False
    rule.save(update_fields=["is_enabled", "updated_at"])
    assert scan_enabled_rules(now=now, force=True) == []


@pytest.mark.django_db
def test_forced_scan_bypasses_the_rule_interval(rule, inbox, write_file) -> None:
    now = timezone.now()
    rule.last_scan_completed_at = now
    rule.save(update_fields=["last_scan_completed_at", "updated_at"])
    write_file(inbox, "ticks.csv")

    results = scan_enabled_rules(now=now, force=True)

    assert len(results) == 1
    assert results[0].status == ScanStatus.SUCCESS.value


@pytest.mark.django_db
def test_broker_failure_is_recorded_and_the_next_scan_requeues(
    rule,
    inbox,
    write_file,
    django_capture_on_commit_callbacks,
) -> None:
    path = write_file(inbox, "ticks.csv")
    base = timezone.now() - timedelta(minutes=10)
    _set_mtime(path, base)
    scan_ingestion_rule(rule, now=base + timedelta(seconds=1))

    with (
        patch(
            "apps.ingestion.tasks.process_ingestion_file.delay",
            side_effect=RuntimeError("broker unavailable"),
        ),
        django_capture_on_commit_callbacks(execute=True),
    ):
        scan_ingestion_rule(rule, now=base + timedelta(seconds=2))

    ingestion = Ingestion.objects.get()
    assert ingestion.state == IngestionState.FAILED
    assert "dispatch failed" in ingestion.error_summary
    assert IngestionObjectState.objects.get(rule=rule).last_processed_content_hash == ""
    assert AuditEvent.objects.filter(
        action=AuditAction.INGESTION_FAILED,
        resource_id=str(ingestion.pk),
    ).exists()

    with (
        patch(
            "apps.ingestion.tasks.process_ingestion_file.delay",
            return_value=SimpleNamespace(id="task-123"),
        ),
        django_capture_on_commit_callbacks(execute=True),
    ):
        recovered = scan_ingestion_rule(rule, now=base + timedelta(seconds=3))

    ingestion.refresh_from_db()
    assert recovered.requeued == 1
    assert ingestion.state == IngestionState.QUEUED
    assert ingestion.celery_task_id == "task-123"
    assert list(
        IngestionStateTransition.objects.filter(ingestion=ingestion)
        .order_by("created_at", "id")
        .values_list("to_state", flat=True)
    )[-2:] == [IngestionState.FAILED, IngestionState.QUEUED]
    assert IngestionObjectState.objects.get(rule=rule).last_processed_content_hash


@pytest.mark.django_db
def test_one_unreadable_object_does_not_stop_the_scan(
    rule,
    inbox,
    write_file,
    monkeypatch,
    django_capture_on_commit_callbacks,
) -> None:
    base = timezone.now() - timedelta(minutes=10)
    bad = write_file(inbox, "a-bad.csv")
    good = write_file(inbox, "b-good.csv")
    _set_mtime(bad, base)
    _set_mtime(good, base)
    scan_ingestion_rule(rule, now=base + timedelta(seconds=1))

    original_hash = LocalStorageAdapter.content_hash

    def _hash(self, key: str) -> str:
        if key == "a-bad.csv":
            raise OSError("cannot read object")
        return original_hash(self, key)

    monkeypatch.setattr(LocalStorageAdapter, "content_hash", _hash)

    with django_capture_on_commit_callbacks(execute=True):
        result = scan_ingestion_rule(rule, now=base + timedelta(seconds=2))

    assert result.errors == 1
    assert result.queued == 1
    assert result.failed_objects == ["a-bad.csv"]
    assert result.status == ScanStatus.FAILED.value
    assert Ingestion.objects.count() == 1
    rule.refresh_from_db()
    assert rule.last_scan_status == ScanStatus.FAILED


@pytest.mark.django_db
def test_one_failing_rule_does_not_cancel_other_rules(
    inbox,
    write_file,
) -> None:
    _make_rule(name="failing-rule", location_reference="missing")
    _make_rule(name="healthy-rule")
    write_file(inbox, "ticks.csv")

    results: list[RuleScanResult] = scan_enabled_rules(force=True)

    statuses = {result.rule_name: result.status for result in results}
    assert statuses["failing-rule"] == ScanStatus.FAILED.value
    assert statuses["healthy-rule"] == ScanStatus.SUCCESS.value
