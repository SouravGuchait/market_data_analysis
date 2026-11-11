"""API tests for portfolio instrument membership and exchange validation."""

from __future__ import annotations

import uuid

import pytest

from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from apps.portfolios.models import PortfolioInstrument

PORTFOLIOS_URL = "/api/v1/portfolios/"


def instruments_url(portfolio_id) -> str:
    return f"{PORTFOLIOS_URL}{portfolio_id}/instruments/"


def instrument_detail_url(portfolio_id, link_id) -> str:
    return f"{instruments_url(portfolio_id)}{link_id}/"


@pytest.mark.django_db
def test_list_portfolio_instruments_is_scoped(
    auth_client, other_auth_client, portfolio, instrument
):
    PortfolioInstrument.objects.create(portfolio=portfolio, instrument=instrument)

    response = auth_client.get(instruments_url(portfolio.pk))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["count"] == 1
    assert data["results"][0]["instrument"]["symbol"] == instrument.symbol
    assert data["results"][0]["exchange"]["code"] == instrument.exchange.code

    assert other_auth_client.get(instruments_url(portfolio.pk)).status_code == 404


@pytest.mark.django_db
def test_add_instrument_accepts_ids_and_returns_nested_data(auth_client, portfolio, instrument):
    response = auth_client.post(
        instruments_url(portfolio.pk),
        {
            "instrument_id": instrument.pk,
            "exchange_id": instrument.exchange_id,
            "alias": "Core holding",
        },
        format="json",
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["instrument"]["symbol"] == "ACME"
    assert data["exchange"]["code"] == "XNYS"
    assert "id" not in data["instrument"]
    assert "id" not in data["exchange"]
    assert data["alias"] == "Core holding"
    assert data["is_active"] is True


@pytest.mark.django_db
def test_add_instrument_accepts_symbol_and_exchange_code(auth_client, portfolio, instrument):
    response = auth_client.post(
        instruments_url(portfolio.pk),
        {
            "instrument_symbol": "acme",
            "exchange_code": "xnys",
            "alias": "Core holding",
        },
        format="json",
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["instrument"]["symbol"] == instrument.symbol
    assert data["exchange"]["code"] == instrument.exchange.code
    assert data["alias"] == "Core holding"


@pytest.mark.django_db
def test_add_instrument_accepts_unique_symbol_without_exchange(auth_client, portfolio, instrument):
    response = auth_client.post(
        instruments_url(portfolio.pk),
        {"instrument_symbol": instrument.symbol},
        format="json",
    )

    assert response.status_code == 201
    assert response.json()["data"]["exchange"]["code"] == instrument.exchange.code


@pytest.mark.django_db
def test_add_instrument_accepts_instrument_id_with_exchange_code(
    auth_client, portfolio, instrument
):
    response = auth_client.post(
        instruments_url(portfolio.pk),
        {
            "instrument_id": instrument.pk,
            "exchange_code": instrument.exchange.code,
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.json()["data"]["instrument"]["symbol"] == instrument.symbol


@pytest.mark.django_db
def test_add_instrument_rejects_unknown_symbol(auth_client, portfolio):
    response = auth_client.post(
        instruments_url(portfolio.pk),
        {"instrument_symbol": "UNKNOWN"},
        format="json",
    )

    assert response.status_code == 400
    assert "instrument_symbol" in response.json()["errors"]
    assert PortfolioInstrument.objects.count() == 0


@pytest.mark.django_db
def test_add_instrument_rejects_inactive_symbol(auth_client, portfolio, instrument):
    Instrument.objects.filter(pk=instrument.pk).update(is_active=False)

    response = auth_client.post(
        instruments_url(portfolio.pk),
        {"instrument_symbol": instrument.symbol, "exchange_code": instrument.exchange.code},
        format="json",
    )

    assert response.status_code == 400
    assert "instrument_symbol" in response.json()["errors"]


@pytest.mark.django_db
def test_add_instrument_rejects_unknown_exchange_code(auth_client, portfolio, instrument):
    response = auth_client.post(
        instruments_url(portfolio.pk),
        {"instrument_symbol": instrument.symbol, "exchange_code": "UNKNOWN"},
        format="json",
    )

    assert response.status_code == 400
    assert "exchange_code" in response.json()["errors"]


@pytest.mark.django_db
def test_add_instrument_rejects_inactive_exchange_code(auth_client, portfolio, instrument):
    Exchange.objects.filter(pk=instrument.exchange_id).update(is_active=False)

    response = auth_client.post(
        instruments_url(portfolio.pk),
        {"instrument_symbol": instrument.symbol, "exchange_code": instrument.exchange.code},
        format="json",
    )

    assert response.status_code == 400
    assert "exchange_code" in response.json()["errors"]


@pytest.mark.django_db
def test_add_instrument_rejects_both_instrument_identifiers(auth_client, portfolio, instrument):
    response = auth_client.post(
        instruments_url(portfolio.pk),
        {"instrument_id": instrument.pk, "instrument_symbol": instrument.symbol},
        format="json",
    )

    assert response.status_code == 400
    assert "instrument_symbol" in response.json()["errors"]


@pytest.mark.django_db
def test_add_instrument_rejects_both_exchange_identifiers(auth_client, portfolio, instrument):
    response = auth_client.post(
        instruments_url(portfolio.pk),
        {
            "instrument_id": instrument.pk,
            "exchange_id": instrument.exchange_id,
            "exchange_code": instrument.exchange.code,
        },
        format="json",
    )

    assert response.status_code == 400
    assert "exchange_code" in response.json()["errors"]


@pytest.mark.django_db
def test_add_instrument_rejects_exchange_code_mismatch(
    auth_client, portfolio, instrument, other_exchange
):
    response = auth_client.post(
        instruments_url(portfolio.pk),
        {
            "instrument_symbol": instrument.symbol,
            "exchange_code": other_exchange.code,
        },
        format="json",
    )

    assert response.status_code == 400
    assert "exchange_code" in response.json()["errors"]
    assert PortfolioInstrument.objects.count() == 0


@pytest.mark.django_db
def test_add_instrument_requires_exchange_for_ambiguous_symbol(
    auth_client, portfolio, instrument, other_exchange
):
    Instrument.objects.create(
        exchange=other_exchange,
        symbol=instrument.symbol,
        name="Acme on Nasdaq",
        asset_class=instrument.asset_class,
        currency=instrument.currency,
    )

    ambiguous = auth_client.post(
        instruments_url(portfolio.pk),
        {"instrument_symbol": instrument.symbol},
        format="json",
    )

    assert ambiguous.status_code == 400
    assert "instrument_symbol" in ambiguous.json()["errors"]

    resolved = auth_client.post(
        instruments_url(portfolio.pk),
        {
            "instrument_symbol": instrument.symbol,
            "exchange_code": other_exchange.code,
        },
        format="json",
    )

    assert resolved.status_code == 201
    assert resolved.json()["data"]["exchange"]["code"] == other_exchange.code


@pytest.mark.django_db
def test_duplicate_natural_key_returns_conflict(auth_client, portfolio, instrument):
    PortfolioInstrument.objects.create(portfolio=portfolio, instrument=instrument)

    response = auth_client.post(
        instruments_url(portfolio.pk),
        {
            "instrument_symbol": instrument.symbol,
            "exchange_code": instrument.exchange.code,
        },
        format="json",
    )

    assert response.status_code == 409
    assert "instrument_id" in response.json()["errors"]


@pytest.mark.django_db
def test_add_instrument_derives_exchange_when_omitted(auth_client, portfolio, instrument):
    response = auth_client.post(
        instruments_url(portfolio.pk),
        {"instrument_id": instrument.pk},
        format="json",
    )

    assert response.status_code == 201
    assert response.json()["data"]["exchange"]["code"] == instrument.exchange.code


@pytest.mark.django_db
def test_add_instrument_rejects_invalid_instrument(auth_client, portfolio):
    response = auth_client.post(
        instruments_url(portfolio.pk),
        {"instrument_id": 999999},
        format="json",
    )

    assert response.status_code == 400
    assert "instrument_id" in response.json()["errors"]


@pytest.mark.django_db
def test_add_instrument_rejects_inactive_instrument(auth_client, portfolio, instrument):
    Instrument.objects.filter(pk=instrument.pk).update(is_active=False)

    response = auth_client.post(
        instruments_url(portfolio.pk),
        {"instrument_id": instrument.pk},
        format="json",
    )

    assert response.status_code == 400
    assert "instrument_id" in response.json()["errors"]


@pytest.mark.django_db
def test_add_instrument_rejects_inactive_exchange(auth_client, portfolio, instrument):
    Exchange.objects.filter(pk=instrument.exchange_id).update(is_active=False)

    response = auth_client.post(
        instruments_url(portfolio.pk),
        {"instrument_id": instrument.pk, "exchange_id": instrument.exchange_id},
        format="json",
    )

    assert response.status_code == 400
    assert "exchange_id" in response.json()["errors"]


@pytest.mark.django_db
def test_add_instrument_rejects_inactive_derived_exchange(auth_client, portfolio, instrument):
    Exchange.objects.filter(pk=instrument.exchange_id).update(is_active=False)

    response = auth_client.post(
        instruments_url(portfolio.pk),
        {"instrument_id": instrument.pk},
        format="json",
    )

    assert response.status_code == 400
    assert "exchange_id" in response.json()["errors"]


@pytest.mark.django_db
def test_add_instrument_rejects_exchange_mismatch(
    auth_client, portfolio, instrument, other_exchange
):
    response = auth_client.post(
        instruments_url(portfolio.pk),
        {"instrument_id": instrument.pk, "exchange_id": other_exchange.pk},
        format="json",
    )

    assert response.status_code == 400
    assert "exchange_id" in response.json()["errors"]
    assert PortfolioInstrument.objects.count() == 0


@pytest.mark.django_db
def test_duplicate_portfolio_instrument_returns_conflict(auth_client, portfolio, instrument):
    PortfolioInstrument.objects.create(portfolio=portfolio, instrument=instrument)

    response = auth_client.post(
        instruments_url(portfolio.pk),
        {"instrument_id": instrument.pk},
        format="json",
    )

    assert response.status_code == 409
    assert response.json()["success"] is False
    assert "instrument_id" in response.json()["errors"]


@pytest.mark.django_db
def test_other_user_cannot_add_to_portfolio(other_auth_client, portfolio, instrument):
    response = other_auth_client.post(
        instruments_url(portfolio.pk),
        {"instrument_id": instrument.pk},
        format="json",
    )

    assert response.status_code == 404
    assert PortfolioInstrument.objects.count() == 0


@pytest.mark.django_db
def test_delete_portfolio_instrument(auth_client, portfolio, instrument):
    link = PortfolioInstrument.objects.create(portfolio=portfolio, instrument=instrument)

    response = auth_client.delete(instrument_detail_url(portfolio.pk, link.pk))

    assert response.status_code == 204
    assert not PortfolioInstrument.objects.filter(pk=link.pk).exists()


@pytest.mark.django_db
def test_other_user_cannot_delete_portfolio_instrument(other_auth_client, portfolio, instrument):
    link = PortfolioInstrument.objects.create(portfolio=portfolio, instrument=instrument)

    response = other_auth_client.delete(instrument_detail_url(portfolio.pk, link.pk))

    assert response.status_code == 404
    assert PortfolioInstrument.objects.filter(pk=link.pk).exists()


@pytest.mark.django_db
def test_delete_unknown_portfolio_instrument_returns_not_found(auth_client, portfolio):
    response = auth_client.delete(instrument_detail_url(portfolio.pk, uuid.uuid4()))

    assert response.status_code == 404
    assert response.json()["success"] is False


@pytest.mark.django_db
def test_own_portfolio_id_cannot_address_another_portfolios_link(
    auth_client, portfolio, other_portfolio, instrument
):
    foreign_link = PortfolioInstrument.objects.create(
        portfolio=other_portfolio, instrument=instrument
    )

    response = auth_client.delete(instrument_detail_url(portfolio.pk, foreign_link.pk))

    assert response.status_code == 404
    assert PortfolioInstrument.objects.filter(pk=foreign_link.pk).exists()
