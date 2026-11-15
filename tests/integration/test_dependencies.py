"""Real MySQL and Redis readiness checks."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]


def test_readiness_reports_mysql_and_redis_ok(api_client: APIClient) -> None:
    response = api_client.get("/api/v1/health/ready/")
    payload = response.json()

    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["data"]["status"] == "ok"
    assert payload["data"]["checks"]["database"]["status"] == "ok"
    assert payload["data"]["checks"]["cache"]["status"] == "ok"
