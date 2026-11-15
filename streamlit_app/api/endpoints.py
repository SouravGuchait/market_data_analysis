"""Typed endpoint helpers. No analytics calculations here."""

from __future__ import annotations

from collections.abc import Mapping

from streamlit_app.api.client import ApiClient, RequestOptions


def login(client: ApiClient, username: str, password: str) -> Mapping[str, object]:
    data = client.request(
        RequestOptions(
            method="POST",
            path="/api/v1/auth/login/",
            json_body={"username": username, "password": password},
            auth=False,
        )
    )
    if not isinstance(data, Mapping):
        raise TypeError("Login response must be an object.")
    return {str(key): value for key, value in data.items()}


def register(
    client: ApiClient,
    *,
    username: str,
    email: str,
    password: str,
) -> Mapping[str, object]:
    data = client.request(
        RequestOptions(
            method="POST",
            path="/api/v1/auth/register/",
            json_body={
                "username": username,
                "email": email,
                "password": password,
                "password_confirm": password,
            },
            auth=False,
        )
    )
    if not isinstance(data, Mapping):
        raise TypeError("Register response must be an object.")
    return {str(key): value for key, value in data.items()}


def logout(client: ApiClient, refresh: str) -> None:
    client.request(
        RequestOptions(
            method="POST",
            path="/api/v1/auth/logout/",
            json_body={"refresh": refresh},
        )
    )


def current_user(client: ApiClient) -> Mapping[str, object]:
    data = client.request(RequestOptions(method="GET", path="/api/v1/auth/me/"))
    if not isinstance(data, Mapping):
        raise TypeError("Current user response must be an object.")
    return {str(key): value for key, value in data.items()}


def password_reset_request(client: ApiClient, email: str) -> None:
    client.request(
        RequestOptions(
            method="POST",
            path="/api/v1/auth/password-reset/",
            json_body={"email": email},
            auth=False,
        )
    )


def password_reset_confirm(client: ApiClient, token: str, password: str) -> None:
    client.request(
        RequestOptions(
            method="POST",
            path="/api/v1/auth/password-reset/confirm/",
            json_body={
                "token": token,
                "password": password,
                "password_confirm": password,
            },
            auth=False,
        )
    )


def bootstrap(client: ApiClient, portfolio_id: str | None = None) -> Mapping[str, object]:
    params: dict[str, object] = {}
    if portfolio_id:
        params["portfolio_id"] = portfolio_id
    data = client.request(
        RequestOptions(method="GET", path="/api/v1/dashboard/bootstrap/", params=params)
    )
    if not isinstance(data, Mapping):
        raise TypeError("Bootstrap response must be an object.")
    return {str(key): value for key, value in data.items()}


def get_json(
    client: ApiClient,
    path: str,
    params: Mapping[str, object] | None = None,
) -> object:
    return client.request(RequestOptions(method="GET", path=path, params=params))


def post_json(
    client: ApiClient,
    path: str,
    body: Mapping[str, object],
) -> object:
    return client.request(RequestOptions(method="POST", path=path, json_body=body))


def patch_json(
    client: ApiClient,
    path: str,
    body: Mapping[str, object],
) -> object:
    return client.request(RequestOptions(method="PATCH", path=path, json_body=body))


def delete_json(client: ApiClient, path: str) -> object:
    return client.request(RequestOptions(method="DELETE", path=path))
