"""Bulk create-and-attach for portfolio instruments."""

from __future__ import annotations

import pytest
from django.db.models import QuerySet
from rest_framework import status

from apps.audit.models import AuditAction, AuditEvent
from apps.instruments.models import Instrument
from apps.portfolios.models import Portfolio, PortfolioInstrument

PORTFOLIOS_URL = "/api/v1/portfolios/"


def bulk_url(portfolio_id: object) -> str:
    return f"{PORTFOLIOS_URL}{portfolio_id}/instruments/bulk/"


BY_NAME_URL = f"{PORTFOLIOS_URL}by-name/"
BY_NAME_BULK_URL = f"{BY_NAME_URL}instruments/bulk/"


def item(exchange_code: str, symbol: str, name: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "exchange_code": exchange_code,
        "symbol": symbol,
        "name": name,
        "asset_class": "equity",
        "currency": "USD",
        "alias": symbol.title(),
    }
    payload.update(overrides)
    return payload


def memberships() -> QuerySet[PortfolioInstrument]:
    return PortfolioInstrument.objects.all()


@pytest.mark.django_db
def test_bulk_creates_and_attaches_missing_instruments(
    auth_client, portfolio, exchange, other_exchange
):
    response = auth_client.post(
        bulk_url(portfolio.pk),
        {
            "create_if_missing": True,
            "instruments": [
                item(exchange.code, "ACME", "Acme Industrial", alias="Acme"),
                item(
                    other_exchange.code, "BETA", "Beta Materials", asset_class="etf", currency="EUR"
                ),
            ],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()["data"]
    assert data["portfolio_id"] == str(portfolio.pk)
    assert data["summary"] == {
        "instruments_created": 2,
        "instruments_reused": 0,
        "instruments_attached": 2,
        "already_attached": 0,
    }
    assert data["instruments"][0]["status"] == "CREATED_AND_ATTACHED"
    assert data["instruments"][1]["exchange"] == "XNAS"
    attached = len(data["instruments"])
    assert memberships().count() == attached
    assert AuditEvent.objects.filter(action=AuditAction.INSTRUMENT_CREATED).count() == attached
    created_events = AuditEvent.objects.filter(action=AuditAction.PORTFOLIO_INSTRUMENT_ADDED)
    assert created_events.count() == attached


@pytest.mark.django_db
def test_bulk_reuses_existing_and_reports_already_attached(auth_client, portfolio, instrument):
    first = auth_client.post(
        bulk_url(portfolio.pk),
        {
            "create_if_missing": False,
            "instruments": [
                item(instrument.exchange.code, instrument.symbol, instrument.name, alias="Acme")
            ],
        },
        format="json",
    )
    assert first.status_code == status.HTTP_201_CREATED
    assert first.json()["data"]["instruments"][0]["status"] == "REUSED_AND_ATTACHED"
    assert Instrument.objects.count() == 1

    second = auth_client.post(
        bulk_url(portfolio.pk),
        {
            "create_if_missing": True,
            "instruments": [item(instrument.exchange.code, "acme", instrument.name, alias="Again")],
        },
        format="json",
    )
    assert second.status_code == status.HTTP_200_OK
    body = second.json()["data"]
    assert body["summary"]["already_attached"] == 1
    assert body["summary"]["instruments_created"] == 0
    assert body["instruments"][0]["status"] == "ALREADY_ATTACHED"
    assert memberships().count() == 1
    assert memberships().get().alias == "Acme"


@pytest.mark.django_db
def test_bulk_rejects_missing_instruments_when_creation_is_disabled(
    auth_client, portfolio, exchange
):
    response = auth_client.post(
        bulk_url(portfolio.pk),
        {
            "create_if_missing": False,
            "instruments": [item(exchange.code, "ACME", "Acme Industrial")],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "instruments" in response.json()["errors"]
    assert Instrument.objects.count() == 0
    assert memberships().count() == 0


@pytest.mark.django_db
def test_bulk_rejects_unknown_exchange_and_rolls_back(auth_client, portfolio, exchange):
    response = auth_client.post(
        bulk_url(portfolio.pk),
        {
            "create_if_missing": True,
            "instruments": [
                item(exchange.code, "ACME", "Acme Industrial"),
                item("ZZZZ", "BETA", "Beta Materials"),
            ],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert Instrument.objects.count() == 0
    assert memberships().count() == 0


@pytest.mark.django_db
def test_bulk_rejects_metadata_conflict_without_writing(auth_client, portfolio, instrument):
    response = auth_client.post(
        bulk_url(portfolio.pk),
        {
            "create_if_missing": True,
            "instruments": [item(instrument.exchange.code, instrument.symbol, "Different Name")],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    instrument.refresh_from_db()
    assert instrument.name == "Acme Industrial"
    assert memberships().count() == 0


@pytest.mark.django_db
def test_bulk_is_limited_to_the_portfolio_owner(auth_client, other_portfolio, exchange):
    response = auth_client.post(
        bulk_url(other_portfolio.pk),
        {
            "create_if_missing": True,
            "instruments": [item(exchange.code, "ACME", "Acme Industrial")],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert Instrument.objects.count() == 0


@pytest.mark.django_db
def test_bulk_rolls_back_when_a_later_insert_fails(
    auth_client, portfolio, exchange, other_exchange, monkeypatch
):
    from django.db import IntegrityError

    original = Instrument.objects.create
    calls = {"count": 0}

    def failing_create(*args: object, **kwargs: object) -> Instrument:
        calls["count"] += 1
        if calls["count"] > 1:
            raise IntegrityError("forced conflict")
        return original(*args, **kwargs)

    monkeypatch.setattr(Instrument.objects, "create", failing_create)
    response = auth_client.post(
        bulk_url(portfolio.pk),
        {
            "create_if_missing": True,
            "instruments": [
                item(exchange.code, "ACME", "Acme Industrial"),
                item(other_exchange.code, "BETA", "Beta Materials"),
            ],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert Instrument.objects.count() == 0
    assert memberships().count() == 0


@pytest.mark.django_db
def test_fetch_portfolio_by_name(auth_client, portfolio):
    response = auth_client.get(BY_NAME_URL, {"name": "retirement"})

    assert response.status_code == status.HTTP_200_OK
    data = response.json()["data"]
    assert data["id"] == str(portfolio.pk)
    assert data["name"] == portfolio.name


@pytest.mark.django_db
def test_fetch_portfolio_by_name_is_limited_to_the_owner(auth_client, other_portfolio):
    response = auth_client.get(BY_NAME_URL, {"name": other_portfolio.name})

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_fetch_portfolio_by_name_rejects_duplicates(auth_client, user, portfolio):
    Portfolio.objects.create(owner=user, name=portfolio.name)

    response = auth_client.get(BY_NAME_URL, {"name": portfolio.name})

    assert response.status_code == status.HTTP_409_CONFLICT
    assert "name" in response.json()["errors"]


@pytest.mark.django_db
def test_bulk_attach_by_portfolio_name(auth_client, portfolio, instrument):
    response = auth_client.post(
        BY_NAME_BULK_URL,
        {
            "name": portfolio.name,
            "create_if_missing": False,
            "instruments": [
                item(instrument.exchange.code, instrument.symbol, instrument.name, alias="Acme")
            ],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()["data"]
    assert data["name"] == portfolio.name
    assert data["portfolio_id"] == str(portfolio.pk)
    assert data["instruments"][0]["symbol"] == instrument.symbol
    assert data["instruments"][0]["exchange"] == instrument.exchange.code
    assert memberships().count() == 1
