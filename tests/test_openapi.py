"""OpenAPI schema and documentation endpoint tests."""

from __future__ import annotations

import pytest
import yaml
from django.conf import settings
from django.test import Client
from django.urls import resolve


@pytest.mark.django_db
def test_openapi_schema_generates_and_contains_health_paths(client: Client) -> None:
    response = client.get("/api/schema/")

    assert response.status_code == 200
    schema = yaml.safe_load(response.content)
    assert schema["info"]["title"] == "Market Data Analytics API"
    assert f"/{settings.API_PREFIX}/health/" in schema["paths"]
    assert f"/{settings.API_PREFIX}/health/ready/" in schema["paths"]


def test_versioned_routes_use_the_central_api_prefix() -> None:
    match = resolve(f"/{settings.API_PREFIX}/health/")

    assert match.view_name == "health:live"


@pytest.mark.django_db
def test_swagger_documentation_is_available(client: Client) -> None:
    response = client.get("/api/docs/")

    assert response.status_code == 200
    assert b"swagger" in response.content.lower()


@pytest.mark.django_db
def test_redoc_documentation_is_available(client: Client) -> None:
    response = client.get("/api/docs/redoc/")

    assert response.status_code == 200
    assert b"redoc" in response.content.lower()
