"""Unit tests for streaming format converters and staging manifests."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from openpyxl import Workbook

from apps.ingestion.converters.detect import detect_format, verify_format
from apps.ingestion.converters.factory import convert_source
from apps.ingestion.converters.types import (
    ConversionError,
    ConvertRequest,
    FormatMismatchError,
    UnsupportedFileFormatError,
)


def _request(source: Path, staging: Path, chunk_size: int = 2) -> ConvertRequest:
    return ConvertRequest(
        source_file=source,
        staging_dir=staging,
        chunk_size=chunk_size,
        parser_config={"format": source.suffix.lstrip(".").lower()},
    )


def test_csv_to_chunks_preserves_source_rows(tmp_path: Path) -> None:
    source = tmp_path / "rows.csv"
    source.write_text("a,b\n1,2\n3,4\n5,6\n7,8\n", encoding="utf-8")
    staging = tmp_path / "staging"
    manifest = convert_source(_request(source, staging), ingestion_id="ing-1")

    assert manifest.total_chunks == 2
    assert manifest.total_rows == 4
    first = pd.read_csv(staging / "chunk_000001.csv")
    assert list(first["_source_row"]) == [2, 3]
    second = pd.read_csv(staging / "chunk_000002.csv")
    assert list(second["_source_row"]) == [4, 5]


def test_json_array_to_chunks(tmp_path: Path) -> None:
    source = tmp_path / "rows.json"
    source.write_text(
        json.dumps([{"a": 1}, {"a": 2}, {"a": 3}]),
        encoding="utf-8",
    )
    staging = tmp_path / "staging"
    manifest = convert_source(_request(source, staging, chunk_size=2), ingestion_id="ing-2")
    assert manifest.source_format == "JSON"
    assert manifest.total_chunks == 2
    assert manifest.total_rows == 3


def test_jsonl_malformed_line_is_conversion_rejection(tmp_path: Path) -> None:
    source = tmp_path / "rows.jsonl"
    source.write_text('{"a": 1}\nnot-json\n{"a": 2}\n', encoding="utf-8")
    staging = tmp_path / "staging"
    manifest = convert_source(_request(source, staging, chunk_size=10), ingestion_id="ing-3")
    assert manifest.total_rows == 2
    assert len(manifest.rejections) == 1
    assert manifest.rejections[0].reason_code == "MALFORMED_JSONL"
    assert manifest.rejections[0].source_row == 2


def test_xlsx_to_chunks(tmp_path: Path) -> None:
    source = tmp_path / "rows.xlsx"
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["a", "b"])
    sheet.append([1, 2])
    sheet.append([3, 4])
    sheet.append([5, 6])
    book.save(source)
    staging = tmp_path / "staging"
    manifest = convert_source(_request(source, staging, chunk_size=2), ingestion_id="ing-4")
    assert manifest.source_format == "XLSX"
    assert manifest.total_chunks == 2
    assert manifest.total_rows == 3


def test_unsupported_extension(tmp_path: Path) -> None:
    source = tmp_path / "rows.parquet"
    source.write_text("x", encoding="utf-8")
    with pytest.raises(UnsupportedFileFormatError):
        detect_format(source)


def test_extension_content_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "rows.csv"
    source.write_bytes(b"PK\x03\x04not-really-xlsx")
    with pytest.raises(FormatMismatchError):
        verify_format(source, "CSV")


def test_invalid_json_structure_fails(tmp_path: Path) -> None:
    source = tmp_path / "rows.json"
    source.write_text('{"not": "an array"}', encoding="utf-8")
    staging = tmp_path / "staging"
    with pytest.raises((ConversionError, FormatMismatchError)):
        convert_source(_request(source, staging), ingestion_id="ing-5")


def test_corrupted_xlsx_fails(tmp_path: Path) -> None:
    source = tmp_path / "rows.xlsx"
    source.write_bytes(b"PK\x03\x04corrupt")
    staging = tmp_path / "staging"
    with pytest.raises(ConversionError):
        convert_source(_request(source, staging), ingestion_id="ing-6")
