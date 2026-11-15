"""Login, registration, and password-reset UI."""

from __future__ import annotations

from collections.abc import Mapping

import streamlit as st

from streamlit_app.api import endpoints
from streamlit_app.api.client import ApiClient
from streamlit_app.api.errors import ApiError
from streamlit_app.session import store_session


def _token_pair(data: Mapping[str, object]) -> tuple[str, str] | None:
    access = data.get("access")
    refresh = data.get("refresh")
    if isinstance(access, str) and isinstance(refresh, str):
        return access, refresh
    return None


def render_auth() -> None:
    st.title("Market Data Analytics")
    st.caption("Sign in to the trading dashboard")
    tab_login, tab_register, tab_reset = st.tabs(["Login", "Register", "Password reset"])
    client = ApiClient()
    with tab_login:
        _login_form(client)
    with tab_register:
        _register_form(client)
    with tab_reset:
        _reset_forms(client)
    client.close()


def _login_form(client: ApiClient) -> None:
    with st.form("login_form"):
        username = st.text_input("Username or email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login", type="primary")
    if not submitted:
        return
    try:
        data = endpoints.login(client, username.strip(), password)
        tokens = _token_pair(data)
        if tokens is None:
            st.error("Unexpected login response.")
            return
        access, refresh = tokens
        client.set_tokens(access, refresh)
        user = endpoints.current_user(client)
        store_session(access, refresh, user)
        st.rerun()
    except ApiError as exc:
        st.error(exc.message)


def _register_form(client: ApiClient) -> None:
    with st.form("register_form"):
        username = st.text_input("Username", key="reg_user")
        email = st.text_input("Email", key="reg_email")
        password = st.text_input("Password", type="password", key="reg_pass")
        confirm = st.text_input("Confirm password", type="password", key="reg_confirm")
        submitted = st.form_submit_button("Create account")
    if not submitted:
        return
    if password != confirm:
        st.error("Passwords do not match.")
        return
    try:
        endpoints.register(
            client,
            username=username.strip(),
            email=email.strip(),
            password=password,
        )
        st.success("Account created. Please log in.")
    except ApiError as exc:
        st.error(exc.message)


def _reset_forms(client: ApiClient) -> None:
    st.subheader("Request reset")
    with st.form("reset_request"):
        email = st.text_input("Account email")
        requested = st.form_submit_button("Send reset email")
    if requested:
        try:
            endpoints.password_reset_request(client, email.strip())
            st.info("If the account exists, a reset email was sent.")
        except ApiError as exc:
            st.error(exc.message)
    st.subheader("Confirm reset")
    with st.form("reset_confirm"):
        token = st.text_input("Reset token")
        password = st.text_input("New password", type="password")
        confirm = st.text_input("Confirm new password", type="password")
        confirmed = st.form_submit_button("Set new password")
    if not confirmed:
        return
    if password != confirm:
        st.error("Passwords do not match.")
        return
    try:
        endpoints.password_reset_confirm(client, token.strip(), password)
        st.success("Password updated. Please log in.")
    except ApiError as exc:
        st.error(exc.message)
