"""API tests for portfolio ownership, validation and response contracts."""

from __future__ import annotations

import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework_simplejwt.tokens import RefreshToken

from apps.portfolios.models import Portfolio, PortfolioInstrument

PORTFOLIOS_URL = "/api/v1/portfolios/"


def portfolio_url(portfolio_id) -> str:
    return f"{PORTFOLIOS_URL}{portfolio_id}/"


def instruments_url(portfolio_id) -> str:
    return f"{PORTFOLIOS_URL}{portfolio_id}/instruments/"


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "post"])
def test_portfolio_collection_requires_authentication(api_client, method):
    response = getattr(api_client, method)(PORTFOLIOS_URL, {}, format="json")

    assert response.status_code == 401


@pytest.mark.django_db
def test_portfolio_collection_accepts_a_jwt_access_token(api_client, user, portfolio):
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")

    response = api_client.get(PORTFOLIOS_URL)

    assert response.status_code == 200
    assert response.json()["data"]["count"] == 1
    assert response.json()["data"]["results"][0]["id"] == str(portfolio.pk)


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_portfolio_detail_requires_authentication(api_client, portfolio, method):
    response = getattr(api_client, method)(portfolio_url(portfolio.pk), {}, format="json")

    assert response.status_code == 401


@pytest.mark.django_db
def test_portfolio_instrument_collection_requires_authentication(api_client, portfolio):
    assert api_client.get(instruments_url(portfolio.pk)).status_code == 401
    assert api_client.post(instruments_url(portfolio.pk), {}, format="json").status_code == 401


@pytest.mark.django_db
def test_create_portfolio_derives_owner_from_request(auth_client, user, other_user):
    response = auth_client.post(
        PORTFOLIOS_URL,
        {
            "name": "Trading",
            "description": "Short-term",
            "status": "active",
            "owner": other_user.pk,
        },
        format="json",
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["success"] is True
    assert payload["errors"] is None
    assert payload["data"]["owner"] == {"username": user.username}
    assert payload["data"]["status"] == "active"
    assert payload["data"]["instruments"] == []

    created = Portfolio.objects.get(pk=payload["data"]["id"])
    assert created.owner == user
    assert created.name == "Trading"


@pytest.mark.django_db
def test_create_portfolio_uses_default_status_and_empty_description(auth_client):
    response = auth_client.post(PORTFOLIOS_URL, {"name": "Defaulted"}, format="json")

    assert response.status_code == 201
    assert response.json()["data"]["status"] == "active"
    assert response.json()["data"]["description"] == ""


@pytest.mark.django_db
def test_create_portfolio_rejects_blank_name(auth_client):
    response = auth_client.post(PORTFOLIOS_URL, {"name": ""}, format="json")

    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    assert payload["data"] is None
    assert "name" in payload["errors"]
    assert Portfolio.objects.count() == 0


@pytest.mark.django_db
def test_list_portfolios_is_scoped_to_the_authenticated_user(
    auth_client, user, portfolio, other_portfolio
):
    Portfolio.objects.create(owner=user, name="Second")

    response = auth_client.get(PORTFOLIOS_URL)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["count"] == 2
    assert {item["id"] for item in data["results"]} == {
        str(portfolio.pk),
        str(Portfolio.objects.get(owner=user, name="Second").pk),
    }
    assert all(item["owner"]["username"] == user.username for item in data["results"])
    assert str(other_portfolio.pk) not in {item["id"] for item in data["results"]}


@pytest.mark.django_db
def test_list_portfolios_paginates(auth_client, user):
    for index in range(21):
        Portfolio.objects.create(owner=user, name=f"Portfolio {index}")

    first_page = auth_client.get(PORTFOLIOS_URL)
    second_page = auth_client.get(f"{PORTFOLIOS_URL}?page=2")

    assert first_page.status_code == 200
    assert first_page.json()["data"]["count"] == 21
    assert len(first_page.json()["data"]["results"]) == 20
    assert first_page.json()["data"]["next"] is not None
    assert len(second_page.json()["data"]["results"]) == 1


@pytest.mark.django_db
def test_list_portfolios_avoids_n_plus_one_queries(
    auth_client, portfolio, instrument, other_instrument
):
    second = Portfolio.objects.create(owner=portfolio.owner, name="Second")
    PortfolioInstrument.objects.create(portfolio=portfolio, instrument=instrument)
    PortfolioInstrument.objects.create(portfolio=portfolio, instrument=other_instrument)
    PortfolioInstrument.objects.create(portfolio=second, instrument=instrument)

    with CaptureQueriesContext(connection) as captured:
        response = auth_client.get(PORTFOLIOS_URL)

    assert response.status_code == 200
    # count + page + one prefetch that joins instruments and exchanges
    assert len(captured.captured_queries) == 3


@pytest.mark.django_db
def test_retrieve_portfolio_returns_nested_relationships(auth_client, portfolio, instrument):
    PortfolioInstrument.objects.create(portfolio=portfolio, instrument=instrument)

    response = auth_client.get(portfolio_url(portfolio.pk))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["id"] == str(portfolio.pk)
    assert data["owner"] == {"username": portfolio.owner.username}
    assert data["instruments"][0]["instrument"]["symbol"] == instrument.symbol
    assert data["instruments"][0]["exchange"]["code"] == instrument.exchange.code
    assert "id" not in data["instruments"][0]["instrument"]
    assert "id" not in data["instruments"][0]["exchange"]


@pytest.mark.django_db
def test_other_user_cannot_retrieve_update_or_delete_portfolio(other_auth_client, portfolio):
    detail_url = portfolio_url(portfolio.pk)

    assert other_auth_client.get(detail_url).status_code == 404
    assert (
        other_auth_client.patch(detail_url, {"name": "Hijacked"}, format="json").status_code == 404
    )
    assert other_auth_client.delete(detail_url).status_code == 404

    portfolio.refresh_from_db()
    assert portfolio.name == "Retirement"


@pytest.mark.django_db
def test_unknown_portfolio_id_returns_the_same_not_found_contract(auth_client):
    response = auth_client.get(portfolio_url(uuid.uuid4()))

    assert response.status_code == 404
    assert response.json() == {
        "success": False,
        "message": "Portfolio not found",
        "data": None,
        "errors": {"detail": ["Not found."]},
    }


@pytest.mark.django_db
def test_patch_updates_only_user_editable_fields(auth_client, portfolio, user, other_user):
    response = auth_client.patch(
        portfolio_url(portfolio.pk),
        {
            "name": "Updated",
            "status": "archived",
            "owner": other_user.pk,
        },
        format="json",
    )

    assert response.status_code == 200
    portfolio.refresh_from_db()
    assert portfolio.name == "Updated"
    assert portfolio.status == "archived"
    assert portfolio.owner == user


@pytest.mark.django_db
def test_patch_rejects_invalid_payload(auth_client, portfolio):
    response = auth_client.patch(portfolio_url(portfolio.pk), {"name": ""}, format="json")

    assert response.status_code == 400
    assert "name" in response.json()["errors"]


@pytest.mark.django_db
def test_delete_removes_only_the_owned_portfolio(auth_client, portfolio, other_portfolio):
    response = auth_client.delete(portfolio_url(portfolio.pk))

    assert response.status_code == 204
    assert response.content == b""
    assert not Portfolio.objects.filter(pk=portfolio.pk).exists()
    assert Portfolio.objects.filter(pk=other_portfolio.pk).exists()


@pytest.mark.django_db
def test_portfolio_api_status_matrix_with_generated_values(
    api_client, auth_client, user, instrument
):
    """Exercise each portfolio route with fresh values and assert HTTP statuses."""

    import secrets

    from rest_framework_simplejwt.tokens import RefreshToken

    assert api_client.get(PORTFOLIOS_URL).status_code == 401

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    assert api_client.get(PORTFOLIOS_URL).status_code == 200

    portfolio_name = f"smoke-{secrets.token_hex(6)}"
    created = api_client.post(PORTFOLIOS_URL, {"name": portfolio_name}, format="json")
    assert created.status_code == 201
    portfolio_id = created.json()["data"]["id"]
    detail = portfolio_url(portfolio_id)
    instruments = instruments_url(portfolio_id)

    assert api_client.get(detail).status_code == 200
    assert api_client.patch(detail, {"description": "randomized"}, format="json").status_code == 200
    assert api_client.get(instruments).status_code == 200

    membership = api_client.post(
        instruments,
        {"instrument_id": instrument.pk, "alias": f"alias-{secrets.token_hex(4)}"},
        format="json",
    )
    assert membership.status_code == 201
    membership_id = membership.json()["data"]["id"]
    assert membership.json()["data"]["instrument"]["symbol"] == instrument.symbol
    assert (
        api_client.post(instruments, {"instrument_id": instrument.pk}, format="json").status_code
        == 409
    )
    assert api_client.delete(f"{instruments}{membership_id}/").status_code == 204
    assert api_client.post(PORTFOLIOS_URL, {}, format="json").status_code == 400
    assert api_client.get(portfolio_url(uuid.uuid4())).status_code == 404
    assert api_client.delete(detail).status_code == 204
