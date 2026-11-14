"""Business operations for user-owned portfolio configuration."""

from __future__ import annotations

from collections.abc import Mapping

from django.contrib.auth.base_user import AbstractBaseUser
from django.db import IntegrityError, transaction

from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from apps.portfolios.models import Portfolio, PortfolioInstrument

EDITABLE_PORTFOLIO_FIELDS = ("name", "description", "status")


class DuplicatePortfolioInstrumentError(Exception):
    """Raised when the same instrument is added to a portfolio twice."""


def _text_field(data: Mapping[str, object], field: str) -> str | None:
    if field not in data:
        return None
    value = data[field]
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string.")
    return value


def create_portfolio(*, owner: AbstractBaseUser, data: Mapping[str, object]) -> Portfolio:
    """Create a portfolio owned by the authenticated user."""

    name = _text_field(data, "name")
    if name is None:
        raise ValueError("Portfolio name is required.")
    description = _text_field(data, "description")
    status = _text_field(data, "status")
    return Portfolio.objects.create(
        owner=owner,
        name=name,
        description="" if description is None else description,
        status="active" if status is None else status,
    )


def update_portfolio(portfolio: Portfolio, data: Mapping[str, object]) -> Portfolio:
    """Update only explicitly editable fields; ownership is never changed here."""

    changed = False
    for field in EDITABLE_PORTFOLIO_FIELDS:
        value = _text_field(data, field)
        if value is None:
            continue
        setattr(portfolio, field, value)
        changed = True
    if changed:
        portfolio.save()
    return portfolio


def delete_portfolio(portfolio: Portfolio) -> None:
    portfolio.delete()


def add_portfolio_instrument(
    portfolio: Portfolio,
    *,
    instrument: Instrument,
    exchange: Exchange,
    alias: str = "",
    is_active: bool = True,
) -> PortfolioInstrument:
    """Add an instrument to a portfolio, preserving the unique membership rule."""

    try:
        with transaction.atomic():
            return PortfolioInstrument.objects.create(
                portfolio=portfolio,
                instrument=instrument,
                exchange=exchange,
                alias=alias,
                is_active=is_active,
            )
    except IntegrityError as exc:
        raise DuplicatePortfolioInstrumentError from exc


def remove_portfolio_instrument(portfolio_instrument: PortfolioInstrument) -> None:
    portfolio_instrument.delete()
