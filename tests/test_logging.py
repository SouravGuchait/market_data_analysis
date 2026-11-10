"""Structured logging and correlation-ID tests."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from apps.common.logging import (
    RequestIDFilter,
    StructuredFormatter,
    ensure_log_directory,
    get_request_id,
    reset_request_id,
    set_request_id,
)


def _record(message: str = "hello") -> logging.LogRecord:
    return logging.LogRecord(
        name="apps.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_request_id_filter_uses_context_value() -> None:
    token = set_request_id("request-abc")
    try:
        record = _record()
        assert RequestIDFilter().filter(record) is True
        assert record.__dict__["request_id"] == "request-abc"
    finally:
        reset_request_id(token)


def test_request_id_filter_does_not_overwrite_explicit_value() -> None:
    token = set_request_id("context-id")
    try:
        record = _record()
        record.__dict__["request_id"] = "explicit-id"
        assert RequestIDFilter().filter(record) is True
        assert record.__dict__["request_id"] == "explicit-id"
    finally:
        reset_request_id(token)


def test_request_id_defaults_to_dash_outside_a_request() -> None:
    assert get_request_id() == "-"


def test_structured_formatter_emits_json_with_extra_fields() -> None:
    record = _record()
    record.__dict__["request_id"] = "request-xyz"
    record.__dict__["method"] = "GET"
    record.__dict__["status_code"] = 200

    payload = json.loads(StructuredFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "apps.test"
    assert payload["message"] == "hello"
    assert payload["request_id"] == "request-xyz"
    assert payload["method"] == "GET"
    assert payload["status_code"] == 200
    assert payload["timestamp"].endswith("+00:00")


def test_structured_formatter_redacts_sensitive_extra_fields() -> None:
    record = _record()
    record.__dict__["api_token"] = "private-token"
    record.__dict__["context"] = {"password": "private-password", "operation": "health"}

    payload = json.loads(StructuredFormatter().format(record))

    assert payload["api_token"] == "[REDACTED]"
    assert payload["context"] == {
        "password": "[REDACTED]",
        "operation": "health",
    }


def test_structured_formatter_includes_exception_text() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = _record("failed")
        record.exc_info = __import__("sys").exc_info()

    payload = json.loads(StructuredFormatter().format(record))

    assert "ValueError: boom" in payload["exception"]


def test_ensure_log_directory_creates_nested_parent(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "logs" / "app.log"

    ensure_log_directory(str(target))

    assert target.parent.is_dir()
