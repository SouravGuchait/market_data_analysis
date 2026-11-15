"""Authenticated HTTP client for the Django REST API."""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import Literal

import httpx

from streamlit_app.api.errors import ApiError, AuthExpiredError
from streamlit_app.config import api_base_url, request_timeout_seconds

logger = logging.getLogger(__name__)

Method = Literal["GET", "POST", "PATCH", "DELETE"]


@dataclass(frozen=True)
class RequestOptions:
    method: Method
    path: str
    json_body: Mapping[str, object] | None = None
    params: Mapping[str, object] | None = None
    auth: bool = True
    retry: bool = True


def _envelope_data(payload: Mapping[str, object]) -> object:
    if payload.get("success") is True:
        return payload.get("data")
    message = payload.get("message")
    text = message if isinstance(message, str) else "Request failed"
    raise ApiError(text)


def _json_mapping(response: httpx.Response) -> dict[str, object]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ApiError("Invalid JSON response from API", status_code=response.status_code) from exc
    if not isinstance(payload, dict):
        raise ApiError("Unexpected API response shape", status_code=response.status_code)
    return {str(key): value for key, value in payload.items()}


def _format_errors(errors: object) -> str:
    if isinstance(errors, Mapping):
        parts: list[str] = []
        for key, value in errors.items():
            if isinstance(value, list):
                parts.append(f"{key}: {', '.join(str(item) for item in value)}")
            elif isinstance(value, Mapping):
                nested = _format_errors(value)
                parts.append(f"{key}: {nested}" if nested else str(key))
            else:
                parts.append(f"{key}: {value}")
        return "; ".join(parts)
    if isinstance(errors, list):
        return ", ".join(str(item) for item in errors)
    return ""


def _error_message(payload: Mapping[str, object], status_code: int) -> str:
    message = payload.get("message") or payload.get("detail")
    detail = _format_errors(payload.get("errors"))
    text = message if isinstance(message, str) else ""
    if text and detail:
        return f"{text}: {detail}"
    return text or detail or f"HTTP {status_code}"


class ApiClient:
    """Thin REST client with JWT attach/refresh. Tokens are never logged."""

    def __init__(
        self,
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.base_url = (base_url or api_base_url()).rstrip("/")
        self.access_token = access_token
        self.refresh_token = refresh_token
        self._client = httpx.Client(timeout=request_timeout_seconds())

    def close(self) -> None:
        self._client.close()

    def set_tokens(self, access: str, refresh: str) -> None:
        self.access_token = access
        self.refresh_token = refresh

    def clear_tokens(self) -> None:
        self.access_token = None
        self.refresh_token = None

    def request(self, options: RequestOptions) -> object:
        response = self._send(options)
        unauthorized = response.status_code == HTTPStatus.UNAUTHORIZED
        if unauthorized and options.auth and options.retry:
            self._refresh()
            response = self._send(options)
        return self._parse(response)

    def _send(self, options: RequestOptions) -> httpx.Response:
        headers: MutableMapping[str, str] = {"Accept": "application/json"}
        if options.auth:
            if not self.access_token:
                raise AuthExpiredError("Not authenticated", status_code=HTTPStatus.UNAUTHORIZED)
            headers["Authorization"] = f"Bearer {self.access_token}"
        url = f"{self.base_url}{options.path}"
        params: dict[str, str | int | float | bool | None] | None = None
        if options.params is not None:
            params = {
                key: value
                if isinstance(value, str | int | float | bool) or value is None
                else str(value)
                for key, value in options.params.items()
            }
        try:
            return self._client.request(
                options.method,
                url,
                headers=headers,
                json=dict(options.json_body) if options.json_body is not None else None,
                params=params,
            )
        except httpx.HTTPError as exc:
            logger.warning("API transport error for %s %s", options.method, options.path)
            raise ApiError("Backend unavailable") from exc

    def _parse(self, response: httpx.Response) -> object:
        if response.status_code in {HTTPStatus.NO_CONTENT, HTTPStatus.RESET_CONTENT}:
            return None
        payload = _json_mapping(response)
        if response.status_code >= HTTPStatus.BAD_REQUEST:
            text = _error_message(payload, response.status_code)
            if response.status_code == HTTPStatus.UNAUTHORIZED:
                raise AuthExpiredError(text, status_code=HTTPStatus.UNAUTHORIZED)
            raise ApiError(text, status_code=response.status_code)
        if "success" in payload:
            return _envelope_data(payload)
        return payload

    def _refresh(self) -> None:
        if not self.refresh_token:
            raise AuthExpiredError("Session expired", status_code=HTTPStatus.UNAUTHORIZED)
        response = self._send(
            RequestOptions(
                method="POST",
                path="/api/v1/auth/token/refresh/",
                json_body={"refresh": self.refresh_token},
                auth=False,
                retry=False,
            )
        )
        if response.status_code >= HTTPStatus.BAD_REQUEST:
            self.clear_tokens()
            raise AuthExpiredError("Session expired", status_code=HTTPStatus.UNAUTHORIZED)
        payload = _json_mapping(response)
        data = payload.get("data")
        token_map = data if isinstance(data, Mapping) else payload
        access = token_map.get("access")
        refresh = token_map.get("refresh", self.refresh_token)
        if not isinstance(access, str) or not isinstance(refresh, str):
            self.clear_tokens()
            raise AuthExpiredError("Session expired", status_code=HTTPStatus.UNAUTHORIZED)
        self.set_tokens(access, refresh)
