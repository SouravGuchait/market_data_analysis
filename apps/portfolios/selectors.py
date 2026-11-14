"""Read-side queries for user-scoped portfolio data."""

from __future__ import annotations

import uuid

from django.contrib.auth.base_user import AbstractBaseUser
from django.db.models import Prefetch, QuerySet

from apps.portfolios.models import Portfolio, PortfolioInstrument


def _portfolio_queryset(owner: AbstractBaseUser) -> QuerySet[Portfolio]:
    """Return only the authenticated user's portfolios with serializer relations loaded."""

    portfolio_instruments = PortfolioInstrument.objects.select_related(
        "instrument__exchange", "exchange"
    ).order_by("created_at")
    return (
        Portfolio.objects.filter(owner=owner)
        .select_related("owner")
        .prefetch_related(Prefetch("portfolio_instruments", queryset=portfolio_instruments))
        .order_by("-created_at", "-id")
    )


def list_user_portfolios(owner: AbstractBaseUser) -> QuerySet[Portfolio]:
    return _portfolio_queryset(owner)


def get_user_portfolio(owner: AbstractBaseUser, portfolio_id: uuid.UUID) -> Portfolio | None:
    """Fetch one owned portfolio, or ``None`` for a missing or foreign portfolio."""

    return _portfolio_queryset(owner).filter(pk=portfolio_id).first()


class AmbiguousPortfolioNameError(Exception):
    """The authenticated user has more than one portfolio with this name."""


def get_user_portfolio_by_name(owner: AbstractBaseUser, name: str) -> Portfolio | None:
    """Fetch one owned portfolio by its name.

    Matching is case-insensitive and limited to the authenticated user.
    """

    matches = list(_portfolio_queryset(owner).filter(name__iexact=name)[:2])
    if len(matches) > 1:
        raise AmbiguousPortfolioNameError(name)
    if not matches:
        return None
    return matches[0]


def list_portfolio_instruments(portfolio: Portfolio) -> QuerySet[PortfolioInstrument]:
    return (
        PortfolioInstrument.objects.filter(portfolio=portfolio)
        .select_related("instrument__exchange", "exchange")
        .order_by("created_at")
    )


def get_portfolio_instrument(
    portfolio: Portfolio, portfolio_instrument_id: uuid.UUID
) -> PortfolioInstrument | None:
    return list_portfolio_instruments(portfolio).filter(pk=portfolio_instrument_id).first()
