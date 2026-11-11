"""Audit recording service.

The service is deliberately explicit: callers pass the already-authenticated
actor. No API endpoint accepts an actor identifier from request data.
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Mapping
from uuid import UUID

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser

from apps.common.logging import get_request_id

from .models import AuditEvent, AuditResourceType, AuditStatus

logger = logging.getLogger("apps.audit")


type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]

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


def _sanitize_value(key: str, value: object, depth: int = 0) -> JsonValue:
    if _is_sensitive_key(key):
        result = "[REDACTED]"
    elif depth >= _MAX_DEPTH:
        result = "[TRUNCATED]"
    elif isinstance(value, Mapping):
        result = {
            str(child_key): _sanitize_value(str(child_key), child_value, depth + 1)
            for child_key, child_value in value.items()
        }
    elif isinstance(value, (list, tuple, set)):
        result = [_sanitize_value(key, item, depth + 1) for item in value]
    elif value is None or isinstance(value, (bool, int, float)):
        result = value
    elif isinstance(value, str):
        result = value if len(value) <= _MAX_VALUE_LENGTH else value[:_MAX_VALUE_LENGTH] + "..."
    else:
        result = str(value)[:_MAX_VALUE_LENGTH]
    return result


def sanitize_metadata(metadata: Mapping[str, object] | None) -> dict[str, JsonValue]:
    """Return JSON-safe metadata with sensitive values redacted."""

    if not metadata:
        return {}
    return {key: _sanitize_value(key, value) for key, value in metadata.items()}


def _resolve_request_id(request: object | None) -> str:
    request_id = getattr(request, "request_id", None) if request is not None else None
    if not request_id:
        request_id = get_request_id()
    if not request_id or request_id == "-":
        return ""
    return str(request_id)[:64]


def _resolve_source_ip(request: object | None) -> str | None:
    """Use the socket peer address only; do not trust forwarding headers."""

    metadata = getattr(request, "META", {}) if request is not None else {}
    source_ip = metadata.get("REMOTE_ADDR") if isinstance(metadata, Mapping) else None
    if not isinstance(source_ip, str):
        return None
    if source_ip:
        try:
            ipaddress.ip_address(source_ip)
        except ValueError:
            source_ip = None
    return source_ip


def record_audit_event(  # noqa: PLR0913 - named fields keep audit call sites explicit
    *,
    action: str,
    resource_type: str,
    status: str,
    actor: AbstractBaseUser | AnonymousUser | None = None,
    resource_id: str | int | UUID | None = None,
    request: object | None = None,
    metadata: Mapping[str, object] | None = None,
    request_id: str | None = None,
    source_ip: str | None = None,
) -> AuditEvent:
    """Persist one audit record and return it.

    ``actor`` is always supplied by server-side code. Anonymous or otherwise
    unauthenticated user objects are stored as ``None`` so a client cannot
    fabricate attribution.
    """
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


def record_system_event(  # noqa: PLR0913 - mirrors the audit event fields
    *,
    action: str,
    resource_type: str = AuditResourceType.SYSTEM,
    status: str = AuditStatus.SUCCESS,
    resource_id: str | int | UUID | None = None,
    metadata: Mapping[str, object] | None = None,
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
