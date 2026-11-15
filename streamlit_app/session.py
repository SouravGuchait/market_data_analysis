"""Streamlit session helpers for auth tokens and shared UI state."""

from __future__ import annotations

from collections.abc import Mapping

import streamlit as st

from streamlit_app.api.client import ApiClient

ACCESS_KEY = "access_token"
REFRESH_KEY = "refresh_token"
USER_KEY = "user"
PAGE_KEY = "nav_page"


def is_authenticated() -> bool:
    return bool(st.session_state.get(ACCESS_KEY) and st.session_state.get(REFRESH_KEY))


def store_session(access: str, refresh: str, user: Mapping[str, object]) -> None:
    st.session_state[ACCESS_KEY] = access
    st.session_state[REFRESH_KEY] = refresh
    st.session_state[USER_KEY] = dict(user)


def clear_session() -> None:
    for key in (ACCESS_KEY, REFRESH_KEY, USER_KEY):
        st.session_state.pop(key, None)


def current_user() -> Mapping[str, object] | None:
    user = st.session_state.get(USER_KEY)
    return user if isinstance(user, Mapping) else None


def api_client() -> ApiClient:
    access = st.session_state.get(ACCESS_KEY)
    refresh = st.session_state.get(REFRESH_KEY)
    return ApiClient(
        access_token=access if isinstance(access, str) else None,
        refresh_token=refresh if isinstance(refresh, str) else None,
    )


def sync_client_tokens(client: ApiClient) -> None:
    if client.access_token and client.refresh_token:
        st.session_state[ACCESS_KEY] = client.access_token
        st.session_state[REFRESH_KEY] = client.refresh_token
    else:
        clear_session()
