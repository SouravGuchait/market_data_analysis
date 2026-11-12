"""Read queries for shared exchange master data."""

from __future__ import annotations

from django.db.models import QuerySet

from apps.exchanges.models import Exchange


def list_exchanges(code: str = "") -> QuerySet[Exchange]:
    queryset = Exchange.objects.order_by("code")
    if code:
        return queryset.filter(code__iexact=code)
    return queryset
