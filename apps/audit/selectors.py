"""Read-side queries for audit records."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from django.db.models import QuerySet

from .models import AuditEvent


def list_visible_audit_events(user: Any) -> QuerySet[AuditEvent]:
    """Return records the authenticated user may inspect.

    Ordinary users see only events attributed to them. Staff users are the
    restricted administrative audience and may inspect all events.
    """

    queryset = AuditEvent.objects.select_related("actor")
    if not user.is_staff:
        queryset = queryset.filter(actor=user)
    return queryset.order_by("-created_at", "-id")


def filter_audit_events(
    queryset: QuerySet[AuditEvent],
    *,
    action: str = "",
    resource_type: str = "",    
    status: str = "",
    start_date: datetime  = datetime.now(),
    end_date: datetime | None = None,
) -> QuerySet[AuditEvent]:
    """Apply validated filter values to an already scoped queryset."""

    if action:
        queryset = queryset.filter(action=action)
    if resource_type:
        queryset = queryset.filter(resource_type=resource_type)
    if status:
        queryset = queryset.filter(status=status)
    if start_date is not None:
        queryset = queryset.filter(created_at__gte=start_date)
    if end_date is not None:
        queryset = queryset.filter(created_at__lte=end_date)
    return queryset


def get_visible_audit_event(user: Any, audit_id: uuid.UUID) -> AuditEvent | None:
    """Fetch one visible record, or ``None`` for missing and foreign records."""

    return list_visible_audit_events(user).filter(pk=audit_id).first()
