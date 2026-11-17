"""Unit tests for the Streamlit API client."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from streamlit_app.api.client import ApiClient, RequestOptions
from streamlit_app.api.errors import ApiError, AuthExpiredError


def _login(_: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "success": True,
            "message": "ok",
            "data": {"access": "a1", "refresh": "r1"},
            "errors": None,
        },
    )


def _me(request: httpx.Request) -> httpx.Response:
    auth = request.headers.get("Authorization", "")
    if auth not in {"Bearer a1", "Bearer a2"}:
        return httpx.Response(401, json={"success": False, "message": "unauthorized"})
    return httpx.Response(
        200,
        json={
            "success": True,
            "message": "ok",
            "data": {"id": 1, "username": "alice", "email": "a@example.com"},
            "errors": None,
        },
    )


def _refresh(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"access": "a2", "refresh": "r2"})


def _logout(_: httpx.Request) -> httpx.Response:
    return httpx.Response(205, json={"detail": "Successfully logged out."})


ROUTES: dict[str, Callable[[httpx.Request], httpx.Response]] = {
    "/auth/login/": _login,
    "/auth/me/": _me,
    "/auth/token/refresh/": _refresh,
    "/auth/logout/": _logout,
}


def _handler(request: httpx.Request) -> httpx.Response:
    for suffix, handler in ROUTES.items():
        if request.url.path.endswith(suffix):
            return handler(request)
    return httpx.Response(404, json={"success": False, "message": "missing"})


@pytest.fixture
def client() -> ApiClient:
    transport = httpx.MockTransport(_handler)
    api = ApiClient(base_url="http://testserver")
    api._client.close()
    api._client = httpx.Client(transport=transport, base_url="http://testserver")
    return api


def test_login_envelope_and_me(client: ApiClient) -> None:
    data = client.request(
        RequestOptions(
            method="POST",
            path="/api/v1/auth/login/",
            json_body={"username": "alice", "password": "x"},
            auth=False,
        )
    )
    assert isinstance(data, dict)
    assert data["access"] == "a1"
    client.set_tokens("a1", "r1")
    me = client.request(RequestOptions(method="GET", path="/api/v1/auth/me/"))
    assert isinstance(me, dict)
    assert me["username"] == "alice"


def test_refresh_on_401(client: ApiClient) -> None:
    client.set_tokens("stale", "r1")
    me = client.request(RequestOptions(method="GET", path="/api/v1/auth/me/"))
    assert isinstance(me, dict)
    assert client.access_token == "a2"


def test_logout_205(client: ApiClient) -> None:
    client.set_tokens("a1", "r1")
    result = client.request(
        RequestOptions(
            method="POST",
            path="/api/v1/auth/logout/",
            json_body={"refresh": "r1"},
        )
    )
    assert result is None


def test_backend_error_raises(client: ApiClient) -> None:
    with pytest.raises(ApiError):
        client.request(RequestOptions(method="GET", path="/api/v1/missing/", auth=False))


def test_validation_error_includes_field_details(client: ApiClient) -> None:
    def _bad(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "success": False,
                "message": "Validation failed",
                "data": None,
                "errors": {"name": ["A rule with this name already exists."]},
            },
        )

    client._client.close()
    client._client = httpx.Client(
        transport=httpx.MockTransport(_bad),
        base_url="http://testserver",
    )
    with pytest.raises(ApiError) as raised:
        client.request(
            RequestOptions(
                method="POST",
                path="/api/v1/ingestion/rules/",
                json_body={},
                auth=False,
            )
        )
    assert "already exists" in raised.value.message


def test_auth_required_without_token() -> None:
    api = ApiClient(base_url="http://testserver")
    with pytest.raises(AuthExpiredError):
        api.request(RequestOptions(method="GET", path="/api/v1/auth/me/"))
    api.close()
