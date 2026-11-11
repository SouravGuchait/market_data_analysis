"""Read-side queries for audit records."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime

from django.contrib.auth.models import AbstractUser
from django.db.models import QuerySet

from .models import AuditEvent


def list_visible_audit_events(user: AbstractUser) -> QuerySet[AuditEvent]:
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
    filters: object,
) -> QuerySet[AuditEvent]:
    """Apply validated filter values to an already scoped queryset."""

    if not isinstance(filters, Mapping):
        return queryset
    for field in ("action", "resource_type", "status"):
        value = filters.get(field)
        if isinstance(value, str) and value:
            queryset = queryset.filter(**{field: value})
    start_date = filters.get("start_date")
    if isinstance(start_date, datetime):
        queryset = queryset.filter(created_at__gte=start_date)
    end_date = filters.get("end_date")
    if isinstance(end_date, datetime):
        queryset = queryset.filter(created_at__lte=end_date)
    return queryset


def get_visible_audit_event(user: AbstractUser, audit_id: uuid.UUID) -> AuditEvent | None:
    """Fetch one visible record, or ``None`` for missing and foreign records."""

    return list_visible_audit_events(user).filter(pk=audit_id).first()
