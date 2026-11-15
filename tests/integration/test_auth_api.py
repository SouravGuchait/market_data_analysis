"""JWT auth against a real MySQL-backed API."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]

AUTH_URL = "/api/v1/auth/"
User = get_user_model()


def test_register_and_login_against_mysql(
    api_client: APIClient,
    unique_username: str,
    unique_email: str,
    strong_password: str,
) -> None:
    register = api_client.post(
        f"{AUTH_URL}register/",
        {
            "username": unique_username,
            "email": unique_email,
            "password": strong_password,
            "password_confirm": strong_password,
        },
        format="json",
    )
    assert register.status_code == 201
    assert register.json()["success"] is True
    assert User.objects.filter(username=unique_username).exists()

    login = api_client.post(
        f"{AUTH_URL}login/",
        {"username": unique_username, "password": strong_password},
        format="json",
    )
    assert login.status_code == 200
    body = login.json()
    assert body["success"] is True
    data = body["data"]
    assert isinstance(data, dict)
    assert isinstance(data.get("access"), str)
    assert isinstance(data.get("refresh"), str)

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {data['access']}")
    me = api_client.get(f"{AUTH_URL}me/")
    assert me.status_code == 200
    assert me.json()["data"]["username"] == unique_username
