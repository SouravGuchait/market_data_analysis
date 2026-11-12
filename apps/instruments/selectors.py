"""Read queries for the shared instrument catalog."""

from __future__ import annotations

from collections.abc import Mapping

from django.db.models import Q, QuerySet

from apps.instruments.models import Instrument


def list_instruments(filters: Mapping[str, str]) -> QuerySet[Instrument]:
    """Return shared instruments with exchange data loaded for the read serializer."""

    queryset = Instrument.objects.select_related("exchange").order_by(
        "exchange__code", "symbol", "id"
    )
    exchange_code = filters.get("exchange_code", "")
    symbol = filters.get("symbol", "")
    search = filters.get("search", "")
    if exchange_code:
        queryset = queryset.filter(exchange__code__iexact=exchange_code)
    if symbol:
        queryset = queryset.filter(symbol__iexact=symbol)
    if "asset_class" in filters:
        queryset = queryset.filter(asset_class=filters["asset_class"])
    if "currency" in filters:
        queryset = queryset.filter(currency=filters["currency"])
    if search:
        queryset = queryset.filter(Q(symbol__icontains=search) | Q(name__icontains=search))
    return queryset
