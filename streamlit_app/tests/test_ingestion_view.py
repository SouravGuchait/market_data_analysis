"""Ingestion view presentation tests."""

from __future__ import annotations

from collections.abc import Mapping

import streamlit as st
from pytest import MonkeyPatch

from streamlit_app.views.ingestion import (
    _rejection_table,
    _show_import_result,
    _status_sections,
)


def test_import_result_renders_file_tables_without_storage_keys(
    monkeypatch: MonkeyPatch,
) -> None:
    headings: list[str] = []
    tables: list[object] = []
    notices: list[str] = []

    def capture_table(data: object, **kwargs: object) -> None:
        del kwargs
        tables.append(data)

    monkeypatch.setattr(st, "subheader", headings.append)
    monkeypatch.setattr(st, "dataframe", capture_table)
    monkeypatch.setattr(st, "success", notices.append)
    _show_import_result(
        {
            "stored": [
                {
                    "original_filename": "prices.csv",
                    "storage_backend": "local",
                    "detected_format": "csv",
                    "file_size": 128,
                    "uploaded_at": "2026-11-17T10:00:00Z",
                    "storage_key": "users/1/files/prices.csv",
                }
            ],
            "skipped": [{"name": "old.csv", "reason": "Already stored."}],
            "queued": [{"name": "prices.csv", "id": "ingestion-id", "rule_id": "rule-id"}],
        }
    )

    assert notices == ["Queued 1 file(s) for conversion/ingestion."]
    assert headings == ["Stored files", "Skipped files", "Queued files"]
    assert tables == [
        [
            {
                "file": "prices.csv",
                "backend": "local",
                "format": "csv",
                "size_bytes": 128,
                "uploaded_at": "2026-11-17T10:00:00Z",
            }
        ],
        [{"file": "old.csv", "reason": "Already stored."}],
        [{"file": "prices.csv", "ingestion_id": "ingestion-id", "rule_id": "rule-id"}],
    ]


def test_import_result_warns_when_no_source_files_were_scanned(
    monkeypatch: MonkeyPatch,
) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(st, "warning", warnings.append)

    _show_import_result(
        {
            "scanned": {"local": 0, "s3": 0},
            "stored": [],
            "skipped": [],
            "queued": [],
        },
        backends=["s3"],
    )

    assert warnings == [
        "No files were found in s3. "
        "Check INGESTION_S3_BUCKET/INGESTION_S3_PREFIX, then re-run import."
    ]


def test_import_result_reports_scanned_counts_when_files_were_skipped(
    monkeypatch: MonkeyPatch,
) -> None:
    notices: list[str] = []
    monkeypatch.setattr(st, "info", notices.append)

    _show_import_result(
        {
            "scanned": {"local": 1, "s3": 0},
            "stored": [],
            "skipped": [{"name": "old.csv", "reason": "Already stored."}],
            "queued": [],
        },
        backends=["local"],
    )

    assert notices == ["Nothing new stored. Scanned 1 local file(s) and 0 S3 object(s); skipped 1."]


def test_status_sections_group_files_by_row_counts_and_state() -> None:
    accepted: Mapping[str, object] = {
        "filename": "accepted.csv",
        "status": "success",
        "accepted_rows": 12,
        "rejected_rows": 0,
    }
    partially_accepted: Mapping[str, object] = {
        "filename": "partial.csv",
        "status": "partial_success",
        "accepted_rows": 5,
        "rejected_rows": 2,
    }
    queued: Mapping[str, object] = {
        "filename": "queued.csv",
        "state": "queued",
        "accepted_rows": 0,
        "rejected_rows": 0,
    }

    sections = _status_sections([accepted, partially_accepted, queued])

    assert sections["Accepted rows"] == [accepted, partially_accepted]
    assert sections["Rejected rows"] == [partially_accepted]
    assert sections["In progress"] == [queued]
    assert sections["Failed"] == []


def test_rejection_table_omits_raw_record_payloads() -> None:
    rows: list[Mapping[str, object]] = [
        {
            "source_row_number": 9,
            "reason_code": "INVALID_PRICE",
            "field_name": "price",
            "error_message": "Price must be positive.",
            "rejected_at": "2026-11-17T10:00:00Z",
            "raw_record": {"account": "sensitive-value"},
        }
    ]

    assert _rejection_table(rows) == [
        {
            "source_row": 9,
            "reason": "INVALID_PRICE",
            "field": "price",
            "error": "Price must be positive.",
            "rejected_at": "2026-11-17T10:00:00Z",
        }
    ]
