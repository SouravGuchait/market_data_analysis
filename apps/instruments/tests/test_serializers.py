"""Serializer tests for instrument relationship input."""

from __future__ import annotations

from typing import cast

import pytest

from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from apps.instruments.serializers import InstrumentWriteSerializer

EXCHANGE_CODE = "XNYS"


@pytest.fixture
def exchange(db) -> Exchange:
    return Exchange.objects.create(
        code=EXCHANGE_CODE,
        name="New York Stock Exchange",
        timezone="America/New_York",
    )


@pytest.fixture
def inactive_exchange(db) -> Exchange:
    return Exchange.objects.create(
        code="XNAS",
        name="Nasdaq",
        timezone="America/New_York",
        is_active=False,
    )


def instrument_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "symbol": "ACME",
        "name": "Acme Industrial",
        "asset_class": Instrument.AssetClass.EQUITY,
        "currency": Instrument.Currency.USD,
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
def test_instrument_accepts_an_exchange_code(exchange):
    serializer = InstrumentWriteSerializer(
        data=instrument_payload(exchange_code=EXCHANGE_CODE.lower())
    )

    assert serializer.is_valid(), serializer.errors
    instrument = cast(Instrument, serializer.save())

    assert instrument.exchange == exchange
    assert instrument.symbol == "ACME"


@pytest.mark.django_db
def test_instrument_still_accepts_an_exchange_primary_key(exchange):
    serializer = InstrumentWriteSerializer(data=instrument_payload(exchange=exchange.pk))

    assert serializer.is_valid(), serializer.errors
    instrument = cast(Instrument, serializer.save())
    assert instrument.exchange == exchange


@pytest.mark.django_db
def test_instrument_requires_an_exchange_identifier(exchange):
    serializer = InstrumentWriteSerializer(data=instrument_payload())

    assert not serializer.is_valid()
    assert "exchange_code" in serializer.errors


@pytest.mark.django_db
def test_instrument_rejects_both_exchange_identifiers(exchange):
    serializer = InstrumentWriteSerializer(
        data=instrument_payload(exchange=exchange.pk, exchange_code=EXCHANGE_CODE)
    )

    assert not serializer.is_valid()
    assert "exchange_code" in serializer.errors


@pytest.mark.django_db
def test_instrument_rejects_unknown_exchange_code(exchange):
    serializer = InstrumentWriteSerializer(data=instrument_payload(exchange_code="UNKNOWN"))

    assert not serializer.is_valid()
    assert "exchange_code" in serializer.errors


@pytest.mark.django_db
def test_instrument_rejects_inactive_exchange_code(inactive_exchange):
    serializer = InstrumentWriteSerializer(
        data=instrument_payload(exchange_code=inactive_exchange.code)
    )

    assert not serializer.is_valid()
    assert "exchange_code" in serializer.errors
