"""Deterministic Trade ordering for open/close selection."""

from __future__ import annotations

from django.db.models import Case, OrderBy, Value, When
from django.db.models.expressions import Expression


def trade_order_by() -> list[Expression | OrderBy | str]:
    """Order trades for open/close: time, non-null sequence, sequence, id."""

    null_sequence_last = Case(
        When(sequence_number__isnull=True, then=Value(1)),
        default=Value(0),
    )
    return ["event_timestamp", null_sequence_last, "sequence_number", "id"]
