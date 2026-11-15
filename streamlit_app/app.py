"""Streamlit trading dashboard entrypoint (Feature 017)."""

from __future__ import annotations

import contextlib

import streamlit as st

from streamlit_app.api import endpoints
from streamlit_app.api.cache import clear_read_caches
from streamlit_app.api.errors import ApiError
from streamlit_app.session import (
    ACCESS_KEY,
    REFRESH_KEY,
    api_client,
    clear_session,
    is_authenticated,
)
from streamlit_app.ui.theme import apply_theme, brand_header
from streamlit_app.views.analytics import render_analytics
from streamlit_app.views.auth_page import render_auth
from streamlit_app.views.home import render_home
from streamlit_app.views.ingestion import render_ingestion
from streamlit_app.views.market import render_market
from streamlit_app.views.portfolio import render_portfolio

PAGES = {
    "Home": render_home,
    "Analytics": render_analytics,
    "Market Data": render_market,
    "Ingestion": render_ingestion,
    "Portfolio": render_portfolio,
}


def _top_nav() -> str:
    brand_header(authenticated=True)
    left, right = st.columns([5, 1])
    with left:
        page = st.pills(
            "Navigate",
            list(PAGES),
            default=st.session_state.get("nav_page", "Home"),
            label_visibility="collapsed",
            key="nav_pills",
        )
    with right:
        if st.button("Refresh", use_container_width=True):
            clear_read_caches()
            st.rerun()
        if st.button("Logout", use_container_width=True):
            _logout()
    selected = page if isinstance(page, str) and page in PAGES else "Home"
    st.session_state["nav_page"] = selected
    return selected


def _logout() -> None:
    client = api_client()
    refresh = st.session_state.get(REFRESH_KEY)
    if isinstance(refresh, str):
        with contextlib.suppress(ApiError):
            endpoints.logout(client, refresh)
    client.close()
    clear_read_caches()
    clear_session()
    st.rerun()


def main() -> None:
    st.set_page_config(
        page_title="MDA Trading Dashboard",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    apply_theme()
    if not is_authenticated():
        brand_header(authenticated=False)
        render_auth()
        return
    if not isinstance(st.session_state.get(ACCESS_KEY), str):
        clear_session()
        st.rerun()
        return
    page = _top_nav()
    PAGES[page]()


if __name__ == "__main__":
    main()
