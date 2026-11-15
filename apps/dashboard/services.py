"""Assemble the dashboard bootstrap payload."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from django.contrib.auth.base_user import AbstractBaseUser

from apps.accounts.serializers import UserSerializer
from apps.dashboard.selectors import (
    dashboard_defaults,
    list_bootstrap_exchanges,
    list_bootstrap_instruments,
    list_bootstrap_portfolios,
    list_instrument_choices,
)
from apps.dashboard.serializers import BootstrapPortfolioSerializer
from apps.exchanges.serializers import ExchangeSerializer
from apps.instruments.serializers import InstrumentReadSerializer
from apps.portfolios.selectors import get_user_portfolio


def build_bootstrap(
    owner: AbstractBaseUser,
    *,
    portfolio_id: uuid.UUID | None = None,
) -> Mapping[str, object] | None:
    if portfolio_id is not None and get_user_portfolio(owner, portfolio_id) is None:
        return None
    portfolios = list(list_bootstrap_portfolios(owner))
    instruments = list(list_bootstrap_instruments(owner=owner, portfolio_id=portfolio_id))
    return {
        "user": UserSerializer(owner).data,
        "portfolios": BootstrapPortfolioSerializer(portfolios, many=True).data,
        "exchanges": ExchangeSerializer(list_bootstrap_exchanges(), many=True).data,
        "instruments": InstrumentReadSerializer(instruments, many=True).data,
        "defaults": dict(dashboard_defaults()),
        "instrument_choices": dict(list_instrument_choices()),
    }
