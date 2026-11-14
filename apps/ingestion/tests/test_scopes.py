"""Feature 006 scope coverage: two tests per scope (happy path + negative).

These tests exercise the conversion/validation pipeline directly because Feature
006 does not expose a dedicated HTTP API for staging and Pandas processing.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest
from django.utils import timezone
from openpyxl import Workbook

from apps.audit.models import AuditAction, AuditEvent, AuditStatus
from apps.exchanges.models import Exchange
from apps.ingestion.converters.detect import detect_format, verify_format
from apps.ingestion.converters.factory import convert_source, get_converter
from apps.ingestion.converters.types import (
    ConversionError,
    ConvertRequest,
    FormatMismatchError,
    UnsupportedFileFormatError,
)
from apps.ingestion.models import (
    DataType,
    Ingestion,
    IngestionState,
    StorageBackend,
    ValidationRule,
    ValidationRuleSet,
    ValidatorType,
)
from apps.ingestion.parsers.column_mapping import (
    apply_column_map,
    normalize_columns,
    resolve_column_map,
)
from apps.ingestion.parsers.dataframe_processor import process_staged_chunk
from apps.ingestion.parsers.normalization import normalize_empty_values
from apps.ingestion.parsers.validation import apply_validators, load_validators
from apps.ingestion.pipeline import run_conversion_pipeline
from apps.ingestion.staging import (
    cleanup_staging_for_ingestion,
    ensure_staging_dir,
    list_chunk_paths,
    read_manifest,
    staging_dir_for,
    write_manifest,
)
from apps.instruments.models import Instrument


def _convert_request(
    source: Path,
    staging: Path,
    *,
    chunk_size: int = 2,
    parser_config: dict[str, object] | None = None,
) -> ConvertRequest:
    config: dict[str, object] = {"format": source.suffix.lstrip(".").lower()}
    if parser_config is not None:
        config = {**config, **parser_config}
    return ConvertRequest(
        source_file=source,
        staging_dir=staging,
        chunk_size=chunk_size,
        parser_config=config,
    )


def _write_xlsx(path: Path, rows: list[list[object]], *, sheet_name: str = "Sheet") -> None:
    book = Workbook()
    default = book.active
    assert default is not None
    default.title = sheet_name
    for row in rows:
        default.append(row)
    book.save(path)


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


def _queued_ingestion(
    rule,
    inbox: Path,
    *,
    filename: str,
    data_type: str,
    chunk_size: int = 100,
) -> Ingestion:
    path = inbox / filename
    rule.data_type = data_type
    rule.chunk_size = chunk_size
    rule.supported_formats = [path.suffix.lstrip(".").lower()]
    rule.file_pattern = f"*{path.suffix.lower()}"
    rule.save()
    return Ingestion.objects.create(
        rule=rule,
        storage_backend=StorageBackend.LOCAL,
        object_key=filename,
        file_name=filename,
        file_size_bytes=path.stat().st_size,
        file_modified_at=timezone.now(),
        content_hash="f" * 64,
        data_type=data_type,
        chunk_size=chunk_size,
        state=IngestionState.QUEUED,
    )


# ---------------------------------------------------------------------------
# Scope: CSV conversion
# ---------------------------------------------------------------------------


def test_csv_happy_splits_into_bounded_chunks(tmp_path: Path) -> None:
    source = tmp_path / "market.csv"
    source.write_text("a,b\n1,2\n3,4\n5,6\n", encoding="utf-8")
    staging = tmp_path / "staging"
    manifest = convert_source(
        _convert_request(source, staging, chunk_size=2),
        ingestion_id="csv-happy",
    )
    assert manifest.conversion_status == "COMPLETED"
    assert manifest.total_chunks == 2
    assert manifest.total_rows == 3
    assert (staging / "chunk_000001.csv").is_file()


def test_csv_negative_rejects_unreadable_source(tmp_path: Path) -> None:
    source = tmp_path / "broken.csv"
    source.write_bytes(b"\xff\xfe\x00\x01not,csv\n")
    staging = tmp_path / "staging"
    with pytest.raises(ConversionError):
        convert_source(
            _convert_request(
                source,
                staging,
                parser_config={"format": "csv", "encoding": "utf-8"},
            ),
            ingestion_id="csv-neg",
        )


# ---------------------------------------------------------------------------
# Scope: JSON conversion
# ---------------------------------------------------------------------------


def test_json_happy_streams_array_into_chunks(tmp_path: Path) -> None:
    source = tmp_path / "rows.json"
    source.write_text(json.dumps([{"x": n} for n in range(5)]), encoding="utf-8")
    staging = tmp_path / "staging"
    manifest = convert_source(
        _convert_request(source, staging, chunk_size=2),
        ingestion_id="json-happy",
    )
    assert manifest.source_format == "JSON"
    assert manifest.total_chunks == 3
    assert manifest.total_rows == 5


def test_json_negative_rejects_non_object_records(tmp_path: Path) -> None:
    source = tmp_path / "rows.json"
    source.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    staging = tmp_path / "staging"
    with pytest.raises(ConversionError):
        convert_source(_convert_request(source, staging), ingestion_id="json-neg")


# ---------------------------------------------------------------------------
# Scope: JSONL conversion
# ---------------------------------------------------------------------------


def test_jsonl_happy_preserves_exact_line_numbers(tmp_path: Path) -> None:
    source = tmp_path / "rows.jsonl"
    source.write_text('{"a": 1}\n{"a": 2}\n{"a": 3}\n', encoding="utf-8")
    staging = tmp_path / "staging"
    manifest = convert_source(
        _convert_request(source, staging, chunk_size=2),
        ingestion_id="jsonl-happy",
    )
    assert manifest.total_chunks == 2
    assert manifest.rejections == []
    first = pd.read_csv(staging / "chunk_000001.csv")
    assert list(first["_source_row"]) == [1, 2]


def test_jsonl_negative_non_object_line_is_conversion_rejection(tmp_path: Path) -> None:
    source = tmp_path / "rows.jsonl"
    source.write_text('{"a": 1}\n[1, 2]\n{"a": 3}\n', encoding="utf-8")
    staging = tmp_path / "staging"
    manifest = convert_source(
        _convert_request(source, staging, chunk_size=10),
        ingestion_id="jsonl-neg",
    )
    assert manifest.total_rows == 2
    assert len(manifest.rejections) == 1
    assert manifest.rejections[0].reason_code == "INVALID_JSONL_RECORD"
    assert manifest.rejections[0].stage == "CONVERSION"


# ---------------------------------------------------------------------------
# Scope: XLSX conversion
# ---------------------------------------------------------------------------


def test_xlsx_happy_uses_configured_worksheet(tmp_path: Path) -> None:
    source = tmp_path / "rows.xlsx"
    book = Workbook()
    default = book.active
    assert default is not None
    default.title = "Ignore"
    default.append(["wrong"])
    ticks = book.create_sheet("Ticks")
    ticks.append(["a", "b"])
    ticks.append([1, 2])
    ticks.append([3, 4])
    book.save(source)
    staging = tmp_path / "staging"
    manifest = convert_source(
        _convert_request(
            source,
            staging,
            chunk_size=10,
            parser_config={"format": "xlsx", "worksheet": "Ticks", "header_row": 1},
        ),
        ingestion_id="xlsx-happy",
    )
    assert manifest.total_rows == 2
    frame = pd.read_csv(staging / "chunk_000001.csv")
    assert list(frame.columns)[:2] == ["a", "b"]


def test_xlsx_negative_missing_worksheet_fails(tmp_path: Path) -> None:
    source = tmp_path / "rows.xlsx"
    _write_xlsx(source, [["a"], [1]])
    staging = tmp_path / "staging"
    with pytest.raises(ConversionError):
        convert_source(
            _convert_request(
                source,
                staging,
                parser_config={"format": "xlsx", "worksheet": "Missing"},
            ),
            ingestion_id="xlsx-neg",
        )


# ---------------------------------------------------------------------------
# Scope: Format verification
# ---------------------------------------------------------------------------


def test_format_happy_detects_jsonl_extension(tmp_path: Path) -> None:
    source = tmp_path / "rows.jsonl"
    source.write_text('{"a":1}\n', encoding="utf-8")
    assert detect_format(source) == "JSONL"
    verify_format(source, "JSONL")


def test_format_negative_json_extension_with_csv_content(tmp_path: Path) -> None:
    source = tmp_path / "rows.json"
    source.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(FormatMismatchError):
        verify_format(source, "JSON")


# ---------------------------------------------------------------------------
# Scope: Converter factory / unsupported formats
# ---------------------------------------------------------------------------


def test_factory_happy_selects_csv_converter() -> None:
    converter = get_converter(".csv")
    assert callable(converter)


def test_factory_negative_unknown_extension() -> None:
    with pytest.raises(UnsupportedFileFormatError):
        get_converter(".parquet")


# ---------------------------------------------------------------------------
# Scope: Chunk sizing
# ---------------------------------------------------------------------------


def test_chunk_size_happy_final_partial_chunk(tmp_path: Path) -> None:
    source = tmp_path / "rows.csv"
    source.write_text("a,b\n1,2\n3,4\n5,6\n7,8\n9,10\n", encoding="utf-8")
    staging = tmp_path / "staging"
    manifest = convert_source(
        _convert_request(source, staging, chunk_size=2),
        ingestion_id="chunk-happy",
    )
    assert [chunk.row_count for chunk in manifest.chunks] == [2, 2, 1]


def test_chunk_size_negative_oversized_request_still_bounded_by_caller(
    tmp_path: Path,
) -> None:
    """Converters trust the validated ingestion chunk_size; empty batches stay empty."""

    source = tmp_path / "empty.csv"
    source.write_text("a,b\n", encoding="utf-8")
    staging = tmp_path / "staging"
    manifest = convert_source(
        _convert_request(source, staging, chunk_size=1),
        ingestion_id="chunk-neg",
    )
    assert manifest.total_chunks == 0
    assert manifest.total_rows == 0


# ---------------------------------------------------------------------------
# Scope: Staging manifest
# ---------------------------------------------------------------------------


def test_manifest_happy_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "rows.csv"
    source.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    staging = tmp_path / "staging"
    manifest = convert_source(
        _convert_request(source, staging, chunk_size=10),
        ingestion_id="manifest-happy",
    )
    write_manifest(staging, manifest)
    loaded = read_manifest(staging)
    chunks = loaded["chunks"]
    assert loaded["ingestion_id"] == "manifest-happy"
    assert loaded["total_rows"] == 2
    assert loaded["staging_format"] == "CSV"
    assert isinstance(chunks, list)
    assert len(chunks) == 1


def test_manifest_negative_invalid_json_raises(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "manifest.json").write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError):
        read_manifest(staging)


# ---------------------------------------------------------------------------
# Scope: Column mapping and normalization
# ---------------------------------------------------------------------------


def test_mapping_happy_maps_generator_daily_columns() -> None:
    frame = pd.DataFrame(
        {
            "Exchange": ["XNYS"],
            "Symbol": ["ACME"],
            "Date": ["2024-01-02"],
            "Open": ["10"],
            "High": ["11"],
            "Low": ["9"],
            "Close": ["10.5"],
            "Volume": ["100"],
            "_source_row": [2],
        }
    )
    named = normalize_columns(frame)
    cleaned = normalize_empty_values(named)
    mapped = apply_column_map(
        cleaned,
        DataType.DAILY_OHLCV,
        resolve_column_map(DataType.DAILY_OHLCV, None),
    )
    assert "exchange_identifier" in mapped.columns
    assert "instrument_identifier" in mapped.columns
    assert "trading_date" in mapped.columns
    assert mapped.loc[0, "instrument_identifier"] == "ACME"


def test_mapping_negative_empty_tokens_become_missing() -> None:
    frame = pd.DataFrame({"price": ["10", "NA", "", "null"], "_source_row": [1, 2, 3, 4]})
    cleaned = normalize_empty_values(normalize_columns(frame))
    assert pd.isna(cleaned.loc[1, "price"])
    assert pd.isna(cleaned.loc[2, "price"])
    assert pd.isna(cleaned.loc[3, "price"])
    assert cleaned.loc[0, "price"] == "10"


# ---------------------------------------------------------------------------
# Scope: Daily OHLCV validation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_daily_validation_happy_accepts_valid_row(instrument: Instrument) -> None:
    _ = instrument
    frame = pd.DataFrame(
        {
            "_source_row": [2],
            "exchange_identifier": ["XNYS"],
            "instrument_identifier": ["ACME"],
            "trading_date": ["2024-01-02"],
            "open": [10],
            "high": [12],
            "low": [9],
            "close": [11],
            "volume": [100],
        }
    )
    valid, rejected, duplicates = apply_validators(
        frame,
        DataType.DAILY_OHLCV,
        load_validators(DataType.DAILY_OHLCV, None),
    )
    assert len(valid) == 1
    assert rejected.empty
    assert duplicates.empty


@pytest.mark.django_db
def test_daily_validation_negative_rejects_broken_ohlc(instrument: Instrument) -> None:
    _ = instrument
    frame = pd.DataFrame(
        {
            "_source_row": [2],
            "exchange_identifier": ["XNYS"],
            "instrument_identifier": ["ACME"],
            "trading_date": ["2024-01-02"],
            "open": [10],
            "high": [8],
            "low": [9],
            "close": [11],
            "volume": [100],
        }
    )
    valid, rejected, duplicates = apply_validators(
        frame,
        DataType.DAILY_OHLCV,
        load_validators(DataType.DAILY_OHLCV, None),
    )
    assert valid.empty
    assert duplicates.empty
    assert "OHLC_RELATIONSHIP" in set(rejected["reason_code"])


# ---------------------------------------------------------------------------
# Scope: Tick validation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_tick_validation_happy_accepts_positive_trade(instrument: Instrument) -> None:
    _ = instrument
    frame = pd.DataFrame(
        {
            "_source_row": [1],
            "exchange_identifier": ["XNYS"],
            "instrument_identifier": ["ACME"],
            "event_timestamp": ["2024-01-02T09:30:00Z"],
            "price": [10.5],
            "quantity": [100],
            "exchange_trade_id": ["T-1"],
        }
    )
    valid, rejected, duplicates = apply_validators(
        frame,
        DataType.TICK_DATA,
        load_validators(DataType.TICK_DATA, None),
    )
    assert len(valid) == 1
    assert rejected.empty
    assert duplicates.empty


@pytest.mark.django_db
def test_tick_validation_negative_rejects_non_positive_price(
    instrument: Instrument,
) -> None:
    _ = instrument
    frame = pd.DataFrame(
        {
            "_source_row": [1],
            "exchange_identifier": ["XNYS"],
            "instrument_identifier": ["ACME"],
            "event_timestamp": ["2024-01-02T09:30:00Z"],
            "price": [0],
            "quantity": [100],
            "exchange_trade_id": ["T-1"],
        }
    )
    valid, rejected, _duplicates = apply_validators(
        frame,
        DataType.TICK_DATA,
        load_validators(DataType.TICK_DATA, None),
    )
    assert valid.empty
    assert "POSITIVE" in set(rejected["reason_code"])


# ---------------------------------------------------------------------------
# Scope: Conversion vs validation rejection stages
# ---------------------------------------------------------------------------


def test_rejection_stage_happy_jsonl_marks_conversion(tmp_path: Path) -> None:
    source = tmp_path / "rows.jsonl"
    source.write_text("{bad\n", encoding="utf-8")
    staging = tmp_path / "staging"
    manifest = convert_source(
        _convert_request(source, staging, chunk_size=10),
        ingestion_id="stage-happy",
    )
    assert manifest.rejections[0].stage == "CONVERSION"


@pytest.mark.django_db
def test_rejection_stage_negative_validation_marks_validation(
    instrument: Instrument,
    tmp_path: Path,
) -> None:
    _ = instrument
    chunk = tmp_path / "chunk_000001.csv"
    chunk.write_text(
        "_source_row,exchange,symbol,timestamp,price,size\n"
        "1,XNYS,ACME,2024-01-02T09:30:00Z,-1,100\n",
        encoding="utf-8",
    )
    processed = process_staged_chunk(
        chunk,
        data_type=DataType.TICK_DATA,
        schema_mapping=None,
        chunk_size=10,
    )
    assert processed.valid_df.empty
    assert not processed.rejected_df.empty
    assert set(processed.rejected_df["stage"]) == {"VALIDATION"}


# ---------------------------------------------------------------------------
# Scope: Database-driven validation rule set
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_rule_set_happy_loads_enabled_validators_from_db() -> None:
    rule_set = ValidationRuleSet.objects.create(
        name="tick-minimal",
        data_type=DataType.TICK_DATA,
        version="1",
        is_active=True,
    )
    ValidationRule.objects.create(
        rule_set=rule_set,
        field_name="price",
        validator=ValidatorType.REQUIRED,
        sort_order=1,
    )
    ValidationRule.objects.create(
        rule_set=rule_set,
        field_name="price",
        validator=ValidatorType.POSITIVE,
        is_enabled=False,
        sort_order=2,
    )
    rows = ValidationRule.objects.filter(rule_set=rule_set).order_by("sort_order")
    specs = load_validators(
        DataType.TICK_DATA,
        [
            {
                "field_name": rule.field_name,
                "validator": rule.validator,
                "parameters": {},
                "is_enabled": rule.is_enabled,
            }
            for rule in rows
        ],
    )
    assert len(specs) == 1
    assert specs[0].validator == "REQUIRED"


@pytest.mark.django_db
def test_rule_set_negative_disabled_only_falls_back_to_defaults() -> None:
    specs = load_validators(
        DataType.TICK_DATA,
        [
            {
                "field_name": "price",
                "validator": "POSITIVE",
                "parameters": {},
                "is_enabled": False,
            }
        ],
    )
    assert any(spec.validator == "REQUIRED" for spec in specs)
    assert any(spec.validator == "POSITIVE" for spec in specs)


# ---------------------------------------------------------------------------
# Scope: Staging cleanup / retention
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_cleanup_happy_removes_expired_success_staging(rule, settings) -> None:
    settings.INGESTION_STAGING_RETENTION_SUCCESS_SECONDS = 0
    ingestion = Ingestion.objects.create(
        rule=rule,
        storage_backend=StorageBackend.LOCAL,
        object_key="done.csv",
        file_name="done.csv",
        file_size_bytes=1,
        file_modified_at=timezone.now(),
        content_hash="1" * 64,
        data_type=DataType.DAILY_OHLCV,
        state=IngestionState.SUCCESS,
        finished_at=timezone.now() - timedelta(hours=1),
    )
    staging = ensure_staging_dir(ingestion)
    (staging / "chunk_000001.csv").write_text("a\n1\n", encoding="utf-8")
    assert cleanup_staging_for_ingestion(ingestion) is True
    assert not staging.exists()


@pytest.mark.django_db
def test_cleanup_negative_keeps_staging_while_queued(rule) -> None:
    ingestion = Ingestion.objects.create(
        rule=rule,
        storage_backend=StorageBackend.LOCAL,
        object_key="active.csv",
        file_name="active.csv",
        file_size_bytes=1,
        file_modified_at=timezone.now(),
        content_hash="2" * 64,
        data_type=DataType.DAILY_OHLCV,
        state=IngestionState.QUEUED,
    )
    staging = ensure_staging_dir(ingestion)
    (staging / "chunk_000001.csv").write_text("a\n1\n", encoding="utf-8")
    assert cleanup_staging_for_ingestion(ingestion) is False
    assert staging.exists()


# ---------------------------------------------------------------------------
# Scope: End-to-end pipeline orchestration
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_pipeline_happy_tick_csv(
    rule,
    inbox: Path,
    write_file,
    instrument: Instrument,
) -> None:
    _ = instrument
    write_file(
        inbox,
        "ticks.csv",
        "exchange,symbol,timestamp,price,size,trade_id\n"
        "XNYS,ACME,2024-01-02T09:30:00Z,10.5,100,T-1\n"
        "XNYS,ACME,2024-01-02T09:31:00Z,10.6,50,T-2\n",
    )
    ingestion = _queued_ingestion(
        rule,
        inbox,
        filename="ticks.csv",
        data_type=DataType.TICK_DATA,
        chunk_size=10,
    )
    result = run_conversion_pipeline(str(ingestion.pk))
    assert result.accepted_rows == 2
    assert result.rejected_rows == 0
    assert result.manifest.total_chunks == 1
    assert len(list_chunk_paths(staging_dir_for(ingestion))) == 1
    ingestion.refresh_from_db()
    assert ingestion.state == IngestionState.QUEUED
    assert ingestion.accepted_rows == 2


@pytest.mark.django_db
def test_pipeline_negative_missing_source_marks_failed(rule, inbox: Path) -> None:
    ingestion = Ingestion.objects.create(
        rule=rule,
        storage_backend=StorageBackend.LOCAL,
        object_key="missing.csv",
        file_name="missing.csv",
        file_size_bytes=1,
        file_modified_at=timezone.now(),
        content_hash="3" * 64,
        data_type=DataType.DAILY_OHLCV,
        chunk_size=10,
        state=IngestionState.QUEUED,
    )
    with pytest.raises(FileNotFoundError):
        run_conversion_pipeline(str(ingestion.pk))
    ingestion.refresh_from_db()
    assert ingestion.state == IngestionState.FAILED
    assert ingestion.error_summary
    assert AuditEvent.objects.filter(
        action=AuditAction.INGESTION_FAILED,
        status=AuditStatus.FAILURE,
        resource_id=str(ingestion.pk),
    ).exists()


# ---------------------------------------------------------------------------
# Scope: Processed staged chunk reading
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_process_chunk_happy_reads_staged_csv(instrument: Instrument, tmp_path: Path) -> None:
    _ = instrument
    chunk = tmp_path / "chunk_000001.csv"
    chunk.write_text(
        "exchange,symbol,date,timestamp,open,high,low,close,volume,_source_row\n"
        "XNYS,ACME,2024-01-02,2024-01-02T16:00:00Z,10,12,9,11,1000,2\n",
        encoding="utf-8",
    )
    processed = process_staged_chunk(
        chunk,
        data_type=DataType.DAILY_OHLCV,
        schema_mapping=None,
        chunk_size=50,
    )
    assert len(processed.valid_df) == 1
    assert processed.rejected_df.empty


@pytest.mark.django_db
def test_process_chunk_negative_unknown_instrument(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_000001.csv"
    chunk.write_text(
        "exchange,symbol,date,timestamp,open,high,low,close,volume,_source_row\n"
        "XNYS,ZZZZ,2024-01-02,2024-01-02T16:00:00Z,10,12,9,11,1000,2\n",
        encoding="utf-8",
    )
    processed = process_staged_chunk(
        chunk,
        data_type=DataType.DAILY_OHLCV,
        schema_mapping=None,
        chunk_size=50,
    )
    assert processed.valid_df.empty
    assert "EXCHANGE_INSTRUMENT_MATCH" in set(processed.rejected_df["reason_code"])
