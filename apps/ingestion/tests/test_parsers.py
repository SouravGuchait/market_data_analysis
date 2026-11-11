"""Bounded CSV, JSON and XLSX parsing behaviour."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid4

import pytest

from apps.ingestion.parser_types import FileParseError, ParserRequest
from apps.ingestion.parsers import iter_file_batches, iter_file_records

INGESTION_ID = uuid4()
INGESTION_TIME = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
COLUMNS = {
    "instrument_identifier": "symbol",
    "exchange_identifier": "venue",
    "event_timestamp": "time",
    "price": "trade_price",
    "quantity": "size",
    "exchange_trade_id": "trade_id",
}
EXPECTED_ROWS = 3
INVALID_ROW_NUMBER = 3
MALFORMED_JSON_ROW_NUMBER = 2
TEST_BATCH_SIZE = 13
TEST_ROW_COUNT = 205


def _mapping(**overrides: object) -> dict[str, object]:
    return {"columns": dict(COLUMNS), **overrides}


def _parse(name: str, content: bytes, mapping: dict[str, object]):
    request = ParserRequest(
        file_name=name,
        stream=BytesIO(content),
        schema_mapping=mapping,
        ingestion_id=INGESTION_ID,
        source_file_reference=f"inbox/{name}",
        ingestion_timestamp=INGESTION_TIME,
    )
    return list(iter_file_records(request))


def test_csv_normalizes_valid_rows_and_rejects_bad_rows_individually() -> None:
    content = (
        b"symbol,venue,time,trade_price,size,trade_id\n"
        b"ACME,XNYS,2026-01-02T03:04:05.123456+02:00,10.250,2,one\n"
        b"ACME,XNYS,not-a-time,11,3,two\n"
        b"ACME,XNYS,2026-01-02T03:04:07Z,10.5,1,one\n"
    )

    results = _parse("ticks.csv", content, _mapping(csv={"chunk_size": 1}))

    assert len(results) == EXPECTED_ROWS
    record = results[0].record
    assert record is not None
    assert record.instrument_identifier == "ACME"
    assert record.exchange_identifier == "XNYS"
    assert record.event_timestamp == datetime(2026, 1, 2, 1, 4, 5, 123456, tzinfo=UTC)
    assert str(record.price) == "10.250"
    assert record.ingestion_id == INGESTION_ID
    assert record.source_file_reference == "inbox/ticks.csv"
    assert record.ingestion_timestamp == INGESTION_TIME
    assert results[1].rejection is not None
    assert results[1].rejection.row_number == INVALID_ROW_NUMBER
    assert results[1].rejection.reason_code == "invalid_row"
    assert results[2].rejection is not None
    assert "duplicate" in results[2].rejection.message


def test_csv_missing_mapped_header_is_a_file_level_error() -> None:
    with pytest.raises(FileParseError, match="missing mapped columns"):
        _parse("ticks.csv", b"symbol,venue,time\nACME,XNYS,2026-01-01T00:00:00Z\n", _mapping())


def test_empty_csv_is_a_file_level_error() -> None:
    with pytest.raises(FileParseError, match="empty|no data rows"):
        _parse("ticks.csv", b"symbol,venue,time,trade_price,size,trade_id\n", _mapping())


def test_json_lines_requires_explicit_mode_and_rejects_bad_lines() -> None:
    rows = [
        {
            "symbol": "ACME",
            "venue": "XNYS",
            "time": "2026-01-02T03:04:05Z",
            "trade_price": "2",
            "size": "0",
        },
        {
            "symbol": "ACME",
            "venue": "XNYS",
            "time": "2026-01-02T03:04:06Z",
            "trade_price": "-1",
            "size": "1",
        },
    ]
    content = (json.dumps(rows[0]) + "\nnot-json\n" + json.dumps(rows[1]) + "\n").encode()

    results = _parse("ticks.json", content, _mapping(json={"mode": "jsonl"}))

    assert results[0].record is not None
    assert results[0].record.exchange_trade_id is None
    assert results[0].record.deduplication_key is None
    assert results[1].rejection is not None
    assert results[1].rejection.row_number == MALFORMED_JSON_ROW_NUMBER
    assert results[2].rejection is not None
    assert "price" in results[2].rejection.message


def test_json_array_streams_records_with_ijson() -> None:
    rows = [
        {
            "symbol": "ACME",
            "venue": "XNYS",
            "time": "2026-01-02T03:04:05Z",
            "trade_price": "2",
            "size": "0",
        },
        {
            "symbol": "ACME",
            "venue": "XNYS",
            "time": "2026-01-02T03:04:06Z",
            "trade_price": "3",
            "size": "4",
        },
    ]
    results = _parse("ticks.json", json.dumps(rows).encode(), _mapping(json={"mode": "array"}))
    assert all(result.record is not None for result in results)
    assert [result.record.exchange_trade_id for result in results if result.record] == [None, None]


def test_json_array_rejects_a_non_array_root() -> None:
    with pytest.raises(FileParseError, match="root array"):
        _parse("ticks.json", b'{"item": []}', _mapping(json={"mode": "array"}))


def test_invalid_json_array_is_a_file_level_error() -> None:
    with pytest.raises(FileParseError, match="invalid structure"):
        _parse("ticks.json", b"[{invalid]", _mapping(json={"mode": "array"}))


def test_naive_and_ambiguous_timestamps_are_rejected_unless_configured() -> None:
    base = {
        "symbol": "ACME",
        "venue": "XNYS",
        "time": "2026-11-01T01:30:00",
        "trade_price": "2",
        "size": "1",
    }
    jsonl = (json.dumps(base) + "\n").encode()
    missing_zone = _parse("ticks.json", jsonl, _mapping(json={"mode": "jsonl"}))[0]
    assert missing_zone.rejection is not None
    assert "timezone" in missing_zone.rejection.message

    ambiguous = _parse(
        "ticks.json",
        jsonl,
        _mapping(json={"mode": "jsonl"}, source_timezone="America/New_York"),
    )[0]
    assert ambiguous.rejection is not None
    assert "ambiguous" in ambiguous.rejection.message

    configured = _parse(
        "ticks.json",
        jsonl,
        _mapping(
            json={"mode": "jsonl"},
            source_timezone="America/New_York",
            ambiguous_time_fold=1,
        ),
    )[0]
    assert configured.record is not None
    assert configured.record.event_timestamp == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)


def test_fallback_dedup_identity_is_used_only_when_configured() -> None:
    row = {
        "symbol": "ACME",
        "venue": "XNYS",
        "time": "2026-01-02T03:04:05Z",
        "trade_price": "2",
        "size": "1",
    }
    mapping = _mapping(json={"mode": "jsonl"}, deduplication_fields=["event_timestamp", "price"])
    content = (json.dumps(row) + "\n" + json.dumps(row) + "\n").encode()
    results = _parse("ticks.json", content, mapping)
    assert results[0].record is not None
    assert results[0].record.deduplication_key is not None
    assert results[1].rejection is not None
    assert "duplicate" in results[1].rejection.message


def test_file_batches_never_exceed_requested_size() -> None:
    lines = ["symbol,venue,time,trade_price,size,trade_id"]
    for index in range(TEST_ROW_COUNT):
        lines.append(f"ACME,XNYS,2026-01-02T03:04:{index % 60:02d}Z,{index + 1},1,id-{index}")
    batches = list(
        iter_file_batches(
            ParserRequest(
                file_name="ticks.csv",
                stream=BytesIO(("\n".join(lines) + "\n").encode()),
                schema_mapping=_mapping(csv={"chunk_size": 17}),
                ingestion_id=INGESTION_ID,
                source_file_reference="inbox/ticks.csv",
                ingestion_timestamp=INGESTION_TIME,
            ),
            batch_size=TEST_BATCH_SIZE,
        )
    )
    assert max(map(len, batches)) == TEST_BATCH_SIZE
    assert sum(map(len, batches)) == TEST_ROW_COUNT


def test_xlsx_uses_configured_worksheet_and_read_only_rows() -> None:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "Trades"
    worksheet.append(list(COLUMNS.values()))
    worksheet.append(["ACME", "XNYS", "2026-01-02T03:04:05Z", "10.25", "1", "one"])
    payload = BytesIO()
    workbook.save(payload)

    results = _parse(
        "ticks.xlsx",
        payload.getvalue(),
        _mapping(xlsx={"worksheet": "Trades", "header_row": 1}),
    )
    assert len(results) == 1
    assert results[0].record is not None
    assert results[0].record.exchange_trade_id == "one"


def test_invalid_xlsx_structure_is_a_file_level_error() -> None:
    pytest.importorskip("openpyxl")
    with pytest.raises(FileParseError, match="unreadable or structurally invalid"):
        _parse(
            "ticks.xlsx",
            b"not an xlsx workbook",
            _mapping(xlsx={"worksheet": "Trades"}),
        )


def test_configured_decimal_precision_rejects_excess_places() -> None:
    row = {
        "symbol": "ACME",
        "venue": "XNYS",
        "time": "2026-01-02T03:04:05Z",
        "trade_price": "1.234",
        "size": "1",
    }
    results = _parse(
        "ticks.json",
        (json.dumps(row) + "\n").encode(),
        _mapping(json={"mode": "jsonl"}, price_decimal_places=2),
    )
    assert results[0].rejection is not None
    assert "decimal precision" in results[0].rejection.message


def test_unsupported_extension_is_a_file_level_error() -> None:
    with pytest.raises(FileParseError, match="Unsupported"):
        _parse("ticks.parquet", b"", _mapping())
