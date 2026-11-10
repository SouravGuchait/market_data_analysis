"""Health and readiness endpoint contract tests."""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.common.health import DependencyStatus, HealthState, ReadinessResult


def _result(
    *,
    database: HealthState = "ok",
    cache: HealthState = "ok",
) -> ReadinessResult:
    checks = {
        "database": DependencyStatus(status=database),
        "cache": DependencyStatus(status=cache),
    }
    errors = {name: ["unavailable"] for name, check in checks.items() if check.status != "ok"}
    return ReadinessResult(is_ready=not errors, checks=checks, errors=errors)


@pytest.mark.django_db
def test_liveness_returns_ok_without_dependency_checks(
    client: Client,
    django_assert_num_queries: Any,
) -> None:
    with django_assert_num_queries(0):
        response = client.get(reverse("health:live"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["message"] == "Service is alive"
    assert payload["data"]["status"] == "ok"
    assert payload["data"]["version"]
    assert "checks" not in payload["data"]
    assert payload["errors"] is None
    assert response["X-Request-ID"]


@pytest.mark.django_db
def test_readiness_returns_ok_when_dependencies_are_available(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "apps.common.views.run_readiness_checks",
        lambda: _result(),
    )

    response = client.get(reverse("health:ready"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["message"] == "Service is ready"
    assert payload["data"]["status"] == "ok"
    assert payload["data"]["checks"] == {
        "database": {"status": "ok"},
        "cache": {"status": "ok"},
    }
    assert payload["errors"] is None


@pytest.mark.django_db
def test_readiness_returns_503_when_database_is_unavailable(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "apps.common.views.run_readiness_checks",
        lambda: _result(database="unavailable"),
    )

    response = client.get(reverse("health:ready"))

    assert response.status_code == 503
    payload = response.json()
    assert payload["success"] is False
    assert payload["message"] == "Service is not ready"
    assert payload["data"]["status"] == "degraded"
    assert payload["data"]["checks"]["database"]["status"] == "unavailable"
    assert payload["errors"] == {"database": ["unavailable"]}


@pytest.mark.django_db
def test_readiness_returns_503_when_cache_is_unavailable(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "apps.common.views.run_readiness_checks",
        lambda: _result(cache="unavailable"),
    )

    response = client.get(reverse("health:ready"))

    assert response.status_code == 503
    payload = response.json()
    assert payload["success"] is False
    assert payload["data"]["checks"]["cache"]["status"] == "unavailable"
    assert payload["errors"] == {"cache": ["unavailable"]}


@pytest.mark.django_db
def test_valid_request_id_is_echoed(client: Client) -> None:
    response = client.get(
        reverse("health:live"),
        HTTP_X_REQUEST_ID="client-request-123",
    )

    assert response["X-Request-ID"] == "client-request-123"


@pytest.mark.django_db
def test_invalid_request_id_is_replaced(client: Client) -> None:
    response = client.get(
        reverse("health:live"),
        HTTP_X_REQUEST_ID="bad id with spaces",
    )

    request_id = response["X-Request-ID"]
    assert request_id != "bad id with spaces"
    assert len(request_id) == 36
