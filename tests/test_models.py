"""Tests for the project foundation's master-data and user models."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from apps.accounts.models import User
from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument


@pytest.mark.django_db
def test_exchange_code_is_unique(exchange: Exchange) -> None:
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Exchange.objects.create(code=exchange.code, name="Duplicate")


@pytest.mark.django_db
def test_instrument_is_unique_per_exchange(
    exchange: Exchange,
    instrument: Instrument,
) -> None:
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Instrument.objects.create(
                exchange=exchange,
                symbol=instrument.symbol,
                name="Duplicate",
            )


@pytest.mark.django_db
def test_user_email_is_unique(user: User) -> None:
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            User.objects.create_user(
                username="another",
                email=user.email,
                password="StrongPass123!",
            )
