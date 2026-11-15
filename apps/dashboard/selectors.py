"""Bounded reference reads for dashboard bootstrap."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from django.contrib.auth.base_user import AbstractBaseUser
from django.db.models import QuerySet

from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from apps.portfolios.models import Portfolio, PortfolioInstrument
from apps.portfolios.selectors import get_user_portfolio, list_user_portfolios

BOOTSTRAP_EXCHANGE_LIMIT = 100
BOOTSTRAP_INSTRUMENT_LIMIT = 200


def list_bootstrap_portfolios(owner: AbstractBaseUser) -> QuerySet[Portfolio]:
    return list_user_portfolios(owner)


def list_bootstrap_exchanges() -> QuerySet[Exchange]:
    return Exchange.objects.filter(is_active=True).order_by("code")[:BOOTSTRAP_EXCHANGE_LIMIT]


def list_bootstrap_instruments(
    *,
    owner: AbstractBaseUser,
    portfolio_id: uuid.UUID | None,
) -> QuerySet[Instrument] | list[Instrument]:
    if portfolio_id is None:
        return (
            Instrument.objects.filter(is_active=True)
            .select_related("exchange")
            .order_by("exchange__code", "symbol", "id")[:BOOTSTRAP_INSTRUMENT_LIMIT]
        )
    portfolio = get_user_portfolio(owner, portfolio_id)
    if portfolio is None:
        return []
    membership = (
        PortfolioInstrument.objects.filter(portfolio=portfolio, is_active=True)
        .select_related("instrument__exchange")
        .order_by("created_at")
    )
    return [row.instrument for row in membership]


def dashboard_defaults() -> Mapping[str, object]:
    return {
        "timeframe": "1d",
        "chart_window_days": 90,
        "tick_page_size": 50,
        "include_incomplete": False,
        "return_type": "simple",
    }


def list_instrument_choices() -> Mapping[str, list[str]]:
    """Distinct asset_class / currency values present on instrument rows."""

    asset_classes = list(
        Instrument.objects.order_by("asset_class")
        .values_list("asset_class", flat=True)
        .distinct()
    )
    currencies = list(
        Instrument.objects.order_by("currency").values_list("currency", flat=True).distinct()
    )
    if not asset_classes:
        asset_classes = [choice.value for choice in Instrument.AssetClass]
    if not currencies:
        currencies = [choice.value for choice in Instrument.Currency]
    return {
        "asset_classes": [str(value) for value in asset_classes if value],
        "currencies": [str(value) for value in currencies if value],
    }
