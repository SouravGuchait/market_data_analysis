"""Feature 007 claim, persistence, checkpoints, and final status tests."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from apps.audit.models import AuditAction, AuditEvent, AuditStatus
from apps.exchanges.models import Exchange
from apps.ingestion.claim import claim_ingestion
from apps.ingestion.models import (
    BatchStatus,
    DataType,
    Ingestion,
    IngestionBatch,
    IngestionState,
    RejectedRow,
    StorageBackend,
)
from apps.ingestion.tasks import process_ingestion_file
from apps.ingestion.worker import run_ingestion_worker
from apps.instruments.models import Instrument
from apps.market_data.models import DailyOHLCV, Trade

worker_task: Any = process_ingestion_file

DAILY_HEADER = (
    "exchange,symbol,name,asset_class,currency,date,timestamp,"
    "open,high,low,close,adjusted_close,volume\n"
)


@pytest.fixture
def exchange(db) -> Exchange:
    return Exchange.objects.create(
        code="XNYS",
        name="NYSE",
        timezone="America/New_York",
        is_active=True,
    )


@pytest.fixture
def instrument(db, exchange: Exchange) -> Instrument:
    return Instrument.objects.create(
        exchange=exchange,
        symbol="ACME",
        name="Acme Industrial",
        asset_class="equity",
        currency="USD",
        is_active=True,
    )


@pytest.fixture
def owner(db):
    return User.objects.create_user(username="ingester", password="pass-word-123")


def _queued_ingestion(
    rule, path: Path, *, data_type: str, owner=None, chunk_size: int = 10
) -> Ingestion:
    rule.data_type = data_type
    rule.chunk_size = chunk_size
    rule.supported_formats = ["csv"]
    rule.save()
    return Ingestion.objects.create(
        rule=rule,
        owner=owner,
        storage_backend=StorageBackend.LOCAL,
        object_key=path.name,
        file_name=path.name,
        file_size_bytes=path.stat().st_size,
        file_modified_at=timezone.now(),
        content_hash="a" * 64,
        data_type=data_type,
        chunk_size=chunk_size,
        state=IngestionState.QUEUED,
    )


@pytest.mark.django_db
def test_claim_is_atomic_and_skips_second_worker(rule) -> None:
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
    first = claim_ingestion(str(ingestion.pk), task_id="task-1")
    second = claim_ingestion(str(ingestion.pk), task_id="task-2")

    assert first is not None
    assert first.state == IngestionState.PROCESSING
    assert second is None


@pytest.mark.django_db
def test_daily_ohlcv_persists_and_marks_partial_success(
    rule,
    inbox: Path,
    write_file,
    instrument: Instrument,
    owner,
) -> None:
    _ = instrument
    path = write_file(
        inbox,
        "daily.csv",
        (
            DAILY_HEADER
            + "XNYS,ACME,Acme,equity,USD,2024-01-02,2024-01-02T16:00:00-05:00,"
            + "10,12,9,11,11,1000\n"
            + "XNYS,ZZZZ,Missing,equity,USD,2024-01-03,2024-01-03T16:00:00-05:00,"
            + "10,12,9,11,11,1000\n"
        ),
    )
    ingestion = _queued_ingestion(rule, path, data_type=DataType.DAILY_OHLCV, owner=owner)

    state = run_ingestion_worker(str(ingestion.pk), task_id="task-daily")

    ingestion.refresh_from_db()
    assert state == IngestionState.PARTIAL_SUCCESS
    assert ingestion.state == IngestionState.PARTIAL_SUCCESS
    assert ingestion.accepted_rows == 1
    assert ingestion.rejected_rows == 1
    assert DailyOHLCV.objects.filter(instrument=instrument).count() == 1
    assert RejectedRow.objects.filter(ingestion=ingestion).count() == 1
    assert IngestionBatch.objects.filter(ingestion=ingestion, status=BatchStatus.COMPLETED).exists()
    assert AuditEvent.objects.filter(
        action=AuditAction.INGESTION_COMPLETED,
        resource_id=str(ingestion.pk),
        status=AuditStatus.PARTIAL,
    ).exists()


@pytest.mark.django_db
def test_ohlcv_duplicate_and_conflict_policy(
    rule,
    inbox: Path,
    write_file,
    instrument: Instrument,
) -> None:
    DailyOHLCV.objects.create(
        instrument=instrument,
        trading_date=date(2024, 1, 2),
        timestamp=timezone.now(),
        open=Decimal(10),
        high=Decimal(12),
        low=Decimal(9),
        close=Decimal(11),
        adjusted_close=Decimal(11),
        volume=1000,
    )
    DailyOHLCV.objects.create(
        instrument=instrument,
        trading_date=date(2024, 1, 3),
        timestamp=timezone.now(),
        open=Decimal(10),
        high=Decimal(12),
        low=Decimal(9),
        close=Decimal(11),
        adjusted_close=Decimal(11),
        volume=1000,
    )
    path = write_file(
        inbox,
        "daily_conflict.csv",
        (
            DAILY_HEADER
            + "XNYS,ACME,Acme,equity,USD,2024-01-02,2024-01-02T16:00:00-05:00,"
            + "10,12,9,11,11,1000\n"
            + "XNYS,ACME,Acme,equity,USD,2024-01-03,2024-01-03T16:00:00-05:00,"
            + "10,12,9,11,11,2000\n"
        ),
    )
    ingestion = _queued_ingestion(rule, path, data_type=DataType.DAILY_OHLCV)

    run_ingestion_worker(str(ingestion.pk), task_id="task-conflict")
    ingestion.refresh_from_db()

    assert ingestion.duplicate_rows == 1
    assert ingestion.rejected_rows == 1
    assert ingestion.accepted_rows == 0
    assert ingestion.state == IngestionState.FAILED
    assert (
        DailyOHLCV.objects.filter(instrument=instrument, trading_date=date(2024, 1, 3)).get().volume
        == 1000
    )


@pytest.mark.django_db
def test_tick_persistence_and_dedupe(
    rule,
    inbox: Path,
    write_file,
    instrument: Instrument,
) -> None:
    _ = instrument
    path = write_file(
        inbox,
        "ticks.csv",
        (
            "exchange,symbol,timestamp,price,size,trade_id\n"
            "XNYS,ACME,2024-01-02T15:30:00-05:00,10.5,100,T1\n"
            "XNYS,ACME,2024-01-02T15:31:00-05:00,10.6,50,T1\n"
        ),
    )
    ingestion = _queued_ingestion(rule, path, data_type=DataType.TICK_DATA)

    run_ingestion_worker(str(ingestion.pk), task_id="task-ticks")
    ingestion.refresh_from_db()

    assert ingestion.state == IngestionState.SUCCESS
    assert ingestion.accepted_rows == 1
    assert ingestion.duplicate_rows == 1
    assert Trade.objects.filter(instrument=instrument).count() == 1


@pytest.mark.django_db
def test_retry_skips_completed_batches(
    rule,
    inbox: Path,
    write_file,
    instrument: Instrument,
) -> None:
    _ = instrument
    path = write_file(
        inbox,
        "daily_retry.csv",
        (
            DAILY_HEADER
            + "XNYS,ACME,Acme,equity,USD,2024-01-02,2024-01-02T16:00:00-05:00,"
            + "10,12,9,11,11,1000\n"
        ),
    )
    ingestion = _queued_ingestion(rule, path, data_type=DataType.DAILY_OHLCV, chunk_size=1)
    run_ingestion_worker(str(ingestion.pk), task_id="task-first")
    assert DailyOHLCV.objects.filter(instrument=instrument).count() == 1

    ingestion.state = IngestionState.QUEUED
    ingestion.celery_task_id = ""
    ingestion.finished_at = None
    ingestion.save(update_fields=["state", "celery_task_id", "finished_at", "updated_at"])

    run_ingestion_worker(str(ingestion.pk), task_id="task-second")
    assert DailyOHLCV.objects.filter(instrument=instrument).count() == 1
    assert IngestionBatch.objects.filter(ingestion=ingestion, batch_number=1).count() == 1


@pytest.mark.django_db
def test_multi_chunk_counters(
    rule,
    inbox: Path,
    write_file,
    instrument: Instrument,
) -> None:
    _ = instrument
    rows = "".join(
        f"XNYS,ACME,Acme,equity,USD,2024-01-0{day},2024-01-0{day}T16:00:00-05:00,"
        f"10,12,9,11,11,{1000 + day}\n"
        for day in (2, 3, 4)
    )
    path = write_file(inbox, "multi.csv", DAILY_HEADER + rows)
    ingestion = _queued_ingestion(rule, path, data_type=DataType.DAILY_OHLCV, chunk_size=1)

    run_ingestion_worker(str(ingestion.pk), task_id="task-multi")
    ingestion.refresh_from_db()

    assert ingestion.chunks_processed == 3
    assert ingestion.accepted_rows == 3
    assert ingestion.state == IngestionState.SUCCESS


@pytest.mark.django_db
def test_empty_file_is_success(rule, inbox: Path, write_file) -> None:
    path = write_file(inbox, "empty.csv", DAILY_HEADER)
    ingestion = _queued_ingestion(rule, path, data_type=DataType.DAILY_OHLCV)

    state = run_ingestion_worker(str(ingestion.pk), task_id="task-empty")
    ingestion.refresh_from_db()

    assert state == IngestionState.SUCCESS
    assert ingestion.accepted_rows == 0
    assert ingestion.rejected_rows == 0


@pytest.mark.django_db
def test_celery_task_processes_queued_file(
    rule,
    inbox: Path,
    write_file,
    instrument: Instrument,
) -> None:
    _ = instrument
    path = write_file(
        inbox,
        "via_celery.csv",
        (
            DAILY_HEADER
            + "XNYS,ACME,Acme,equity,USD,2024-01-02,2024-01-02T16:00:00-05:00,"
            + "10,12,9,11,11,1000\n"
        ),
    )
    ingestion = _queued_ingestion(rule, path, data_type=DataType.DAILY_OHLCV)

    result = worker_task.apply(args=[str(ingestion.pk)], task_id="celery-1")
    assert result.get() == IngestionState.SUCCESS
    ingestion.refresh_from_db()
    assert ingestion.state == IngestionState.SUCCESS
    assert ingestion.celery_task_id == "celery-1"


@pytest.mark.django_db
def test_missing_source_marks_failed(rule) -> None:
    ingestion = Ingestion.objects.create(
        rule=rule,
        storage_backend=rule.storage_backend,
        object_key="missing.csv",
        file_name="missing.csv",
        file_size_bytes=1,
        file_modified_at=timezone.now() - timedelta(minutes=1),
        content_hash="c" * 64,
        data_type=DataType.DAILY_OHLCV,
        state=IngestionState.QUEUED,
    )
    state = run_ingestion_worker(str(ingestion.pk), task_id="task-missing")
    ingestion.refresh_from_db()
    assert state == IngestionState.FAILED
    assert ingestion.state == IngestionState.FAILED
