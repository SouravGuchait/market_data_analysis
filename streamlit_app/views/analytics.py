"""Analytics panels: returns, drawdown, and technical indicators."""

from __future__ import annotations

from collections.abc import Mapping

import streamlit as st

from streamlit_app.api import endpoints
from streamlit_app.api.cache import cached_bootstrap
from streamlit_app.session import ACCESS_KEY, REFRESH_KEY, api_client
from streamlit_app.views.helpers import (
    as_list,
    as_mapping,
    bootstrap_instruments,
    page_results,
    run_api,
    select_symbol_and_exchange,
)

INDICATORS = ("sma", "ema", "macd", "bollinger", "atr", "rsi", "stochastic")


def render_analytics() -> None:
    st.title("Analytics")
    access = st.session_state.get(ACCESS_KEY)
    refresh = st.session_state.get(REFRESH_KEY)
    if not isinstance(access, str) or not isinstance(refresh, str):
        st.error("Not authenticated.")
        return
    boot = cached_bootstrap(access, refresh)
    scope = select_symbol_and_exchange(bootstrap_instruments(boot), key_prefix="an")
    timeframe = st.selectbox("Timeframe", ["1m", "5m", "15m", "30m", "1h", "1d"], index=5)
    metrics = st.multiselect(
        "Metrics", list(INDICATORS) + ["returns", "drawdown"], default=["sma", "returns"]
    )
    if scope is None:
        return
    client = api_client()
    if st.button("Load overview", type="primary"):
        _load_overview(client, scope.symbol, scope.exchange_code, timeframe, metrics)
    client.close()


def _load_overview(
    client: object,
    symbol: str,
    exchange: str,
    timeframe: str,
    metrics: list[str],
) -> None:
    from streamlit_app.api.client import ApiClient

    assert isinstance(client, ApiClient)
    params: dict[str, object] = {
        "instrument_symbol": symbol,
        "exchange_code": exchange,
        "metrics": ",".join(metrics),
        "timeframe": timeframe,
        "window": 20,
    }
    payload = run_api(
        client,
        lambda: endpoints.get_json(client, "/api/v1/analytics/overview/", params),
    )
    if payload is None:
        return
    data = as_mapping(payload)
    for block in as_list(data.get("results")):
        if not isinstance(block, Mapping):
            continue
        item = as_mapping(block)
        st.subheader(str(item.get("metric")))
        st.caption(
            f"source={item.get('source')} · observations={item.get('observation_count')} · "
            f"incomplete={item.get('incomplete_policy')}"
        )
        series = as_list(item.get("series"))
        if not series:
            st.info("No series data.")
            continue
        first = as_mapping(series[0])
        st.json(
            {
                "instrument": first.get("instrument"),
                "exchange": first.get("exchange"),
                "parameters": first.get("parameters"),
            }
        )
        points = page_results({"results": first.get("results")})
        st.dataframe(points[-50:], use_container_width=True, hide_index=True)
