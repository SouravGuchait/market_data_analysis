"""Structured logging helpers.

The formatter emits one JSON object per line so that console output and the
rotating file can be consumed by the same tooling.  Request correlation IDs
are stored in a context variable and injected into every record.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="-")

_RESERVED_RECORD_ATTRIBUTES = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "message",
    "asctime",
    "request_id",
}

_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "api_key",
    "apikey",
)


def _safe_extra_value(key: str, value: Any) -> Any:
    """Redact sensitive structured fields, including nested mappings."""

    normalized_key = key.lower().replace("-", "_")
    if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(child_key): _safe_extra_value(str(child_key), child_value)
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_extra_value(key, item) for item in value]
    return value


def get_request_id() -> str:
    """Return the request correlation ID for the current execution context."""

    return _REQUEST_ID.get()


def set_request_id(request_id: str) -> Token[str]:
    """Bind a request correlation ID to the current context."""

    return _REQUEST_ID.set(request_id)


def reset_request_id(token: Token[str]) -> None:
    """Restore the previous request correlation ID."""

    _REQUEST_ID.reset(token)


class RequestIDFilter(logging.Filter):
    """Attach the active request correlation ID to a log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.__dict__["request_id"] = get_request_id()
        return True


class StructuredFormatter(logging.Formatter):
    """Render a log record as a single structured JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", get_request_id()),
            "module": record.module,
            "process": record.process,
            "thread": record.thread,
        }

        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_ATTRIBUTES and not key.startswith("_"):
                payload[key] = _safe_extra_value(key, value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def ensure_log_directory(file_path: str) -> None:
    """Create the parent directory for a configured log file path."""

    Path(file_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
