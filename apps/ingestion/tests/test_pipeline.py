"""Tests for staging lifecycle and Pandas validation over staged chunks."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from apps.audit.models import AuditAction, AuditEvent
from apps.exchanges.models import Exchange
from apps.ingestion.models import (
    DataType,
    Ingestion,
    IngestionState,
    StorageBackend,
)
from apps.ingestion.pipeline import run_conversion_pipeline
from apps.ingestion.staging import (
    cleanup_staging_for_ingestion,
    delete_staging,
    list_chunk_paths,
    staging_dir_for,
)
from apps.instruments.models import Instrument


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


@pytest.mark.django_db
def test_pipeline_stages_and_validates_daily_csv(
    rule,
    inbox: Path,
    write_file,
    instrument: Instrument,
) -> None:
    _ = instrument
    write_file(
        inbox,
        "daily.csv",
        (
            "exchange,symbol,name,asset_class,currency,date,timestamp,"
            "open,high,low,close,adjusted_close,volume\n"
            "XNYS,ACME,Acme,equity,USD,2024-01-02,2024-01-02T16:00:00-05:00,"
            "10,12,9,11,11,1000\n"
            "XNYS,ACME,Acme,equity,USD,2024-01-02,2024-01-02T16:00:00-05:00,"
            "10,12,9,11,11,1000\n"
            "XNYS,ZZZZ,Missing,equity,USD,2024-01-03,2024-01-03T16:00:00-05:00,"
            "10,12,9,11,11,1000\n"
        ),
    )
    rule.data_type = DataType.DAILY_OHLCV
    rule.chunk_size = 10
    rule.supported_formats = ["csv"]
    rule.save()
    path = inbox / "daily.csv"
    ingestion = Ingestion.objects.create(
        rule=rule,
        storage_backend=StorageBackend.LOCAL,
        object_key="daily.csv",
        file_name="daily.csv",
        file_size_bytes=path.stat().st_size,
        file_modified_at=timezone.now(),
        content_hash="c" * 64,
        data_type=DataType.DAILY_OHLCV,
        chunk_size=10,
        state=IngestionState.QUEUED,
    )

    result = run_conversion_pipeline(str(ingestion.pk))
    staging = staging_dir_for(ingestion)

    assert (staging / "manifest.json").is_file()
    assert len(list_chunk_paths(staging)) == 1
    assert result.accepted_rows == 1
    assert result.duplicate_rows == 1
    assert result.rejected_rows == 1
    ingestion.refresh_from_db()
    assert ingestion.state == IngestionState.QUEUED
    assert AuditEvent.objects.filter(
        action=AuditAction.INGESTION_COMPLETED,
        resource_id=str(ingestion.pk),
    ).exists()


@pytest.mark.django_db
def test_staging_cleanup_respects_retention(rule, settings) -> None:
    settings.INGESTION_STAGING_RETENTION_SUCCESS_SECONDS = 0
    ingestion = Ingestion.objects.create(
        rule=rule,
        storage_backend=StorageBackend.LOCAL,
        object_key="daily.csv",
        file_name="daily.csv",
        file_size_bytes=1,
        file_modified_at=timezone.now(),
        content_hash="d" * 64,
        data_type=DataType.DAILY_OHLCV,
        state=IngestionState.SUCCESS,
        finished_at=timezone.now() - timedelta(seconds=5),
    )
    staging = staging_dir_for(ingestion)
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "chunk_000001.csv").write_text("a\n1\n", encoding="utf-8")

    assert cleanup_staging_for_ingestion(ingestion) is True
    assert not staging.exists()


@pytest.mark.django_db
def test_delete_staging_never_removes_source(rule, inbox: Path, write_file) -> None:
    source = write_file(inbox, "keep.csv", "a\n1\n")
    ingestion = Ingestion.objects.create(
        rule=rule,
        storage_backend=StorageBackend.LOCAL,
        object_key="keep.csv",
        file_name="keep.csv",
        file_size_bytes=3,
        file_modified_at=timezone.now(),
        content_hash="e" * 64,
        state=IngestionState.FAILED,
        finished_at=timezone.now(),
    )
    staging = staging_dir_for(ingestion)
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "chunk_000001.csv").write_text("a\n1\n", encoding="utf-8")

    delete_staging(ingestion)
    assert source.is_file()
    assert not staging.exists()
