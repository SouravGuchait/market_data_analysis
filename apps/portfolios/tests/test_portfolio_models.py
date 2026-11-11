"""Model-level ownership and association invariants for Feature 003."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.instruments.models import Instrument
from apps.portfolios.models import Portfolio, PortfolioInstrument


@pytest.mark.django_db
def test_portfolio_instrument_derives_exchange_from_instrument(portfolio, instrument):
    link = PortfolioInstrument.objects.create(portfolio=portfolio, instrument=instrument)

    assert link.exchange is not None
    assert link.exchange == instrument.exchange


@pytest.mark.django_db
def test_portfolio_instrument_rejects_exchange_mismatch(portfolio, instrument, other_exchange):
    with pytest.raises(ValidationError, match="must match"):
        PortfolioInstrument.objects.create(
            portfolio=portfolio,
            instrument=instrument,
            exchange=other_exchange,
        )

    assert PortfolioInstrument.objects.count() == 0


@pytest.mark.django_db
def test_clean_rejects_exchange_mismatch(portfolio, instrument, other_exchange):
    link = PortfolioInstrument(
        portfolio=portfolio,
        instrument=instrument,
        exchange=other_exchange,
    )

    with pytest.raises(ValidationError, match="must match"):
        link.clean()


@pytest.mark.django_db
def test_duplicate_portfolio_membership_is_rejected(portfolio, instrument):
    PortfolioInstrument.objects.create(portfolio=portfolio, instrument=instrument)

    with pytest.raises(IntegrityError), transaction.atomic():
        PortfolioInstrument.objects.create(portfolio=portfolio, instrument=instrument)


@pytest.mark.django_db
def test_two_users_can_reference_the_same_instrument(portfolio, other_portfolio, instrument):
    first = PortfolioInstrument.objects.create(portfolio=portfolio, instrument=instrument)
    second = PortfolioInstrument.objects.create(portfolio=other_portfolio, instrument=instrument)

    assert first.pk != second.pk
    assert first.instrument == second.instrument == instrument


@pytest.mark.django_db
def test_deleting_portfolio_cascades_associations_but_keeps_instrument(portfolio, instrument):
    PortfolioInstrument.objects.create(portfolio=portfolio, instrument=instrument)

    Portfolio.objects.filter(pk=portfolio.pk).delete()

    assert PortfolioInstrument.objects.count() == 0
    assert Instrument.objects.filter(pk=instrument.pk).exists()
