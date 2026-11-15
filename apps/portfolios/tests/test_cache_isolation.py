"""Feature 015: portfolio cache isolation and invalidation."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.portfolios.models import Portfolio

PORTFOLIOS_URL = "/api/v1/portfolios/"


@pytest.mark.django_db
def test_portfolio_list_cache_does_not_leak_across_users(
    auth_client, other_auth_client, user, other_user
) -> None:
    auth_client.post(PORTFOLIOS_URL, {"name": "Alice book"}, format="json")
    other_auth_client.post(PORTFOLIOS_URL, {"name": "Bob book"}, format="json")

    alice_first = auth_client.get(PORTFOLIOS_URL)
    bob_first = other_auth_client.get(PORTFOLIOS_URL)
    alice_cached = auth_client.get(PORTFOLIOS_URL)
    bob_cached = other_auth_client.get(PORTFOLIOS_URL)

    assert alice_first.status_code == 200
    assert bob_first.status_code == 200
    alice_names = {row["name"] for row in alice_cached.json()["data"]["results"]}
    bob_names = {row["name"] for row in bob_cached.json()["data"]["results"]}
    assert alice_names == {"Alice book"}
    assert bob_names == {"Bob book"}
    assert Portfolio.objects.filter(owner=user).count() == 1
    assert Portfolio.objects.filter(owner=other_user).count() == 1


@pytest.mark.django_db
def test_portfolio_detail_404_for_other_user_not_served_from_cache(
    auth_client, other_auth_client
) -> None:
    created = auth_client.post(PORTFOLIOS_URL, {"name": "Private"}, format="json")
    portfolio_id = created.json()["data"]["id"]
    assert auth_client.get(f"{PORTFOLIOS_URL}{portfolio_id}/").status_code == 200
    assert other_auth_client.get(f"{PORTFOLIOS_URL}{portfolio_id}/").status_code == 404


@pytest.mark.django_db
def test_portfolio_list_invalidates_after_create(auth_client) -> None:
    auth_client.post(PORTFOLIOS_URL, {"name": "One"}, format="json")
    first = auth_client.get(PORTFOLIOS_URL)
    assert first.json()["data"]["count"] == 1
    auth_client.post(PORTFOLIOS_URL, {"name": "Two"}, format="json")
    second = auth_client.get(PORTFOLIOS_URL)
    assert second.json()["data"]["count"] == 2
    names = {row["name"] for row in second.json()["data"]["results"]}
    assert names == {"One", "Two"}


@pytest.mark.django_db
def test_portfolio_list_second_get_avoids_extra_queries(auth_client, portfolio) -> None:
    auth_client.get(PORTFOLIOS_URL)
    with CaptureQueriesContext(connection) as ctx:
        response = auth_client.get(PORTFOLIOS_URL)
    assert response.status_code == 200
    assert response.json()["data"]["count"] >= 1
    # Cached response should not re-query the portfolios table.
    portfolio_queries = [q for q in ctx.captured_queries if "mda_portfolios" in q["sql"].lower()]
    assert portfolio_queries == []
