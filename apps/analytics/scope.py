"""Shared filter resolution for analytics endpoints."""

from __future__ import annotations

from uuid import UUID

from rest_framework import status
from rest_framework.response import Response

from apps.analytics.selectors import (
    active_instrument_ids,
    owned_portfolio_instrument_ids,
    resolve_exchange_ids,
    resolve_instrument_ids_by_keys,
)
from apps.instruments.models import Instrument


def failure(
    message: str,
    errors: object,
    status_code: int = status.HTTP_400_BAD_REQUEST,
) -> Response:
    return Response(
        {"success": False, "message": message, "data": None, "errors": errors},
        status=status_code,
    )


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _as_str(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _as_uuid(value: object) -> UUID | None:
    return value if isinstance(value, UUID) else None


def merge_instrument_ids(
    *,
    instrument_id: int | None,
    named_ids: list[int] | None,
    portfolio_ids: list[int] | None,
    exchange_ids: list[int] | None,
) -> list[int] | None:
    selected = active_instrument_ids(instrument_id)
    if named_ids is not None:
        selected = named_ids if selected is None else sorted(set(selected) & set(named_ids))
    if portfolio_ids is not None:
        selected = portfolio_ids if selected is None else sorted(set(selected) & set(portfolio_ids))
    if exchange_ids is not None:
        qs = Instrument.objects.filter(exchange_id__in=exchange_ids, is_active=True)
        if selected is not None:
            qs = qs.filter(pk__in=selected)
        return list(qs.values_list("pk", flat=True))
    return selected


def _identity_conflict(filters: dict[str, object]) -> Response | None:
    has_id = filters.get("instrument_id") is not None
    has_symbol = bool(_as_str(filters.get("instrument_symbol")))
    has_name = bool(_as_str(filters.get("instrument_name")))
    if has_id and (has_symbol or has_name):
        return failure(
            "Invalid filters",
            {
                "instrument_id": [
                    "Provide instrument_id or instrument_symbol/instrument_name, not both."
                ]
            },
        )
    return None


def _portfolio_ids(user: object, filters: dict[str, object]) -> list[int] | None | Response:
    portfolio_id = _as_uuid(filters.get("portfolio_id"))
    if portfolio_id is None:
        return None
    portfolio_ids = owned_portfolio_instrument_ids(user, portfolio_id)
    if portfolio_ids is None:
        return failure(
            "Portfolio not found",
            {"detail": ["Not found."]},
            status.HTTP_404_NOT_FOUND,
        )
    return portfolio_ids


def _named_instrument_ids(
    filters: dict[str, object],
    exchange_ids: list[int] | None,
) -> list[int] | None | Response:
    named_ids = resolve_instrument_ids_by_keys(
        instrument_symbol=_as_str(filters.get("instrument_symbol")),
        instrument_name=_as_str(filters.get("instrument_name")),
        exchange_ids=exchange_ids,
    )
    if named_ids is None:
        return None
    if len(named_ids) > 1 and exchange_ids is None:
        return failure(
            "Ambiguous instrument",
            {"detail": ["Multiple instruments matched; provide exchange_code or exchange_id."]},
        )
    return named_ids


def _scope_parts(
    user: object,
    filters: dict[str, object],
) -> tuple[list[int] | None, list[int] | None, Response | None]:
    portfolio_ids = _portfolio_ids(user, filters)
    if isinstance(portfolio_ids, Response):
        return None, None, portfolio_ids
    exchange_ids = resolve_exchange_ids(
        exchange_id=_as_int(filters.get("exchange_id")),
        exchange_code=_as_str(filters.get("exchange_code")),
    )
    empty_exchange = exchange_ids is not None and not exchange_ids
    named_ids = None if empty_exchange else _named_instrument_ids(filters, exchange_ids)
    if isinstance(named_ids, Response):
        return None, None, named_ids
    instrument_ids = (
        []
        if empty_exchange
        else merge_instrument_ids(
            instrument_id=_as_int(filters.get("instrument_id")),
            named_ids=named_ids,
            portfolio_ids=portfolio_ids,
            exchange_ids=exchange_ids,
        )
    )
    return instrument_ids, ([] if empty_exchange else exchange_ids), None


def resolve_scope(
    user: object,
    filters: dict[str, object],
) -> tuple[list[int] | None, list[int] | None, Response | None]:
    conflict = _identity_conflict(filters)
    if conflict is not None:
        return None, None, conflict
    return _scope_parts(user, filters)
