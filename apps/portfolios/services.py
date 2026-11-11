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


def create_portfolio(*, owner: AbstractBaseUser, data: Mapping[str, object]) -> Portfolio:
    """Create a portfolio owned by the authenticated user."""

    return Portfolio.objects.create(
        owner=owner,
        name=_string_value(data, "name", ""),
        description=_string_value(data, "description", ""),
        status=_string_value(data, "status", "active"),
    )


def update_portfolio(portfolio: Portfolio, data: Mapping[str, object]) -> Portfolio:
    """Update only explicitly editable fields; ownership is never changed here."""

    changed = False
    for field in EDITABLE_PORTFOLIO_FIELDS:
        if field in data:
            value = data[field]
            if isinstance(value, str):
                setattr(portfolio, field, value)
                changed = True
    if changed:
        portfolio.save()
    return portfolio


def delete_portfolio(portfolio: Portfolio) -> None:
    portfolio.delete()


def add_portfolio_instrument(
    portfolio: Portfolio, data: Mapping[str, object]
) -> PortfolioInstrument:
    """Add an instrument to a portfolio, preserving the unique membership rule."""

    try:
        with transaction.atomic():
            instrument = data.get("instrument")
            exchange = data.get("exchange")
            if not isinstance(instrument, Instrument) or not isinstance(exchange, Exchange):
                raise ValueError("Validated instrument and exchange are required.")
            alias = data.get("alias", "")
            is_active = data.get("is_active", True)
            return PortfolioInstrument.objects.create(
                portfolio=portfolio,
                instrument=instrument,
                exchange=exchange,
                alias=alias if isinstance(alias, str) else "",
                is_active=is_active if isinstance(is_active, bool) else True,
            )
    except IntegrityError as exc:
        raise DuplicatePortfolioInstrumentError from exc


def remove_portfolio_instrument(portfolio_instrument: PortfolioInstrument) -> None:
    portfolio_instrument.delete()


def _string_value(data: Mapping[str, object], field: str, default: str) -> str:
    value = data.get(field, default)
    return value if isinstance(value, str) else default
