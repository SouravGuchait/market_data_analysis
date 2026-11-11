"""Business operations for user-owned portfolio configuration."""

from __future__ import annotations

from typing import Any

from django.db import IntegrityError, transaction

from apps.portfolios.models import Portfolio, PortfolioInstrument

EDITABLE_PORTFOLIO_FIELDS = ("name", "description", "status")


class DuplicatePortfolioInstrumentError(Exception):
    """Raised when the same instrument is added to a portfolio twice."""


def create_portfolio(*, owner: Any, data: dict[str, Any]) -> Portfolio:
    """Create a portfolio owned by the authenticated user."""

    editable_data = {field: data[field] for field in EDITABLE_PORTFOLIO_FIELDS if field in data}
    return Portfolio.objects.create(owner=owner, **editable_data)


def update_portfolio(portfolio: Portfolio, data: dict[str, Any]) -> Portfolio:
    """Update only explicitly editable fields; ownership is never changed here."""

    changed = False
    for field in EDITABLE_PORTFOLIO_FIELDS:
        if field in data:
            setattr(portfolio, field, data[field])
            changed = True
    if changed:
        portfolio.save()
    return portfolio


def delete_portfolio(portfolio: Portfolio) -> None:
    portfolio.delete()


def add_portfolio_instrument(portfolio: Portfolio, data: dict[str, Any]) -> PortfolioInstrument:
    """Add an instrument to a portfolio, preserving the unique membership rule."""

    try:
        with transaction.atomic():
            return PortfolioInstrument.objects.create(portfolio=portfolio, **data)
    except IntegrityError as exc:
        raise DuplicatePortfolioInstrumentError from exc


def remove_portfolio_instrument(portfolio_instrument: PortfolioInstrument) -> None:
    portfolio_instrument.delete()
