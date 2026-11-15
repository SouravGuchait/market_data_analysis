"""Streamlit-cached read helpers (Feature 017E)."""

from __future__ import annotations

from collections.abc import Mapping

import streamlit as st

from streamlit_app.api import endpoints
from streamlit_app.api.client import ApiClient
from streamlit_app.config import market_cache_ttl_seconds, reference_cache_ttl_seconds
from streamlit_app.session import sync_client_tokens


def _client(access_token: str, refresh_token: str) -> ApiClient:
    return ApiClient(access_token=access_token, refresh_token=refresh_token)


@st.cache_data(ttl=reference_cache_ttl_seconds(), show_spinner=False)
def cached_bootstrap(
    access_token: str,
    refresh_token: str,
    portfolio_id: str | None = None,
) -> Mapping[str, object]:
    client = _client(access_token, refresh_token)
    try:
        data = endpoints.bootstrap(client, portfolio_id)
        sync_client_tokens(client)
        return data
    finally:
        client.close()


@st.cache_data(ttl=market_cache_ttl_seconds(), show_spinner=False)
def cached_get(
    access_token: str,
    refresh_token: str,
    path: str,
    params_items: tuple[tuple[str, str], ...] = (),
) -> object:
    params = {key: value for key, value in params_items}
    client = _client(access_token, refresh_token)
    try:
        data = endpoints.get_json(client, path, params)
        sync_client_tokens(client)
        return data
    finally:
        client.close()


def params_key(params: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), str(value)) for key, value in params.items()))


def clear_read_caches() -> None:
    cached_bootstrap.clear()
    cached_get.clear()
