"""Audit recording service.

The service is deliberately explicit: callers pass the already-authenticated
actor. No API endpoint accepts an actor identifier from request data.
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from apps.common.logging import get_request_id

from .models import AuditEvent, AuditResourceType, AuditStatus

logger = logging.getLogger("apps.audit")

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
_MAX_VALUE_LENGTH = 500
_MAX_DEPTH = 5


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _sanitize_container(key: str, value: object, depth: int) -> Any:
    if isinstance(value, Mapping):
        return {
            str(child_key): _sanitize_value(str(child_key), child_value, depth + 1)
            for child_key, child_value in value.items()
        }
    items = value if isinstance(value, list | tuple | set) else ()
    return [_sanitize_value(key, item, depth + 1) for item in items]


def _sanitize_text(value: str) -> str:
    if len(value) <= _MAX_VALUE_LENGTH:
        return value
    return value[:_MAX_VALUE_LENGTH] + "..."


def _sanitize_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    rendered = str(value)
    return rendered if len(rendered) <= _MAX_VALUE_LENGTH else rendered[:_MAX_VALUE_LENGTH]


def _blocked_value(key: str, depth: int) -> str | None:
    if _is_sensitive_key(key):
        return "[REDACTED]"
    if depth >= _MAX_DEPTH:
        return "[TRUNCATED]"
    return None


def _sanitize_value(key: str, value: Any, depth: int = 0) -> Any:
    blocked = _blocked_value(key, depth)
    if blocked is not None:
        return blocked
    if isinstance(value, Mapping | list | tuple | set):
        return _sanitize_container(key, value, depth)
    return _sanitize_scalar(value)


def sanitize_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return JSON-safe metadata with sensitive values redacted."""

    if not metadata:
        return {}
    return {key: _sanitize_value(key, value) for key, value in metadata.items()}


def _resolve_request_id(request: Any | None) -> str:
    request_id = getattr(request, "request_id", None) if request is not None else None
    if not request_id:
        request_id = get_request_id()
    if not request_id or request_id == "-":
        return ""
    return str(request_id)[:64]


def _resolve_source_ip(request: Any | None) -> str | None:
    """Use the socket peer address only; do not trust forwarding headers."""

    raw_ip = getattr(request, "META", {}).get("REMOTE_ADDR") if request is not None else None
    if not raw_ip:
        return None
    try:
        ipaddress.ip_address(raw_ip)
    except ValueError:
        return None
    return raw_ip


def record_audit_event(  # noqa: PLR0913
    *,
    action: str,
    resource_type: str,
    status: str,
    actor: Any | None = None,
    resource_id: str | int | UUID | None = None,
    request: Any | None = None,
    metadata: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    source_ip: str | None = None,
) -> AuditEvent:
    """Persist one audit record and return it.

    ``actor`` is always supplied by server-side code. Anonymous or otherwise
    unauthenticated user objects are stored as ``None`` so a client cannot
    fabricate attribution.
    """

    if actor is not None and not getattr(actor, "is_authenticated", False):
        actor = None

    event = AuditEvent(
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id="" if resource_id is None else str(resource_id)[:128],
        status=status,
        request_id=(request_id or _resolve_request_id(request))[:64],
        source_ip=source_ip if source_ip is not None else _resolve_source_ip(request),
        metadata=sanitize_metadata(metadata),
    )
    event.save()
    return event


def record_system_event(  # noqa: PLR0913
    *,
    action: str,
    resource_type: str = AuditResourceType.SYSTEM,
    status: str = AuditStatus.SUCCESS,
    resource_id: str | int | UUID | None = None,
    metadata: Mapping[str, Any] | None = None,
    request_id: str | None = None,
) -> AuditEvent:
    """Record an event with no user actor (scheduler, ingestion worker, etc.)."""

    payload = {**(metadata or {}), "system": True}
    return record_audit_event(
        actor=None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        status=status,
        metadata=payload,
        request_id=request_id,
    )
