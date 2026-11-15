"""Market data: ticks and OHLCV charts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import streamlit as st

from streamlit_app.api import endpoints
from streamlit_app.api.cache import cached_bootstrap
from streamlit_app.session import ACCESS_KEY, REFRESH_KEY, api_client
from streamlit_app.ui.charts import candle_figure
from streamlit_app.views.helpers import (
    bootstrap_instruments,
    page_results,
    run_api,
    select_symbol_and_exchange,
)

TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "1d")


def render_market() -> None:
    st.title("Market Data")
    access = st.session_state.get(ACCESS_KEY)
    refresh = st.session_state.get(REFRESH_KEY)
    if not isinstance(access, str) or not isinstance(refresh, str):
        st.error("Not authenticated.")
        return
    boot = cached_bootstrap(access, refresh)
    scope = select_symbol_and_exchange(bootstrap_instruments(boot), key_prefix="mkt")
    if scope is None:
        return
    client = api_client()
    tab_candles, tab_ticks = st.tabs(["OHLCV", "Ticks"])
    with tab_candles:
        _candles(client, scope.symbol, scope.exchange_code)
    with tab_ticks:
        _ticks(client, scope.symbol, scope.exchange_code)
    client.close()


def _candles(client: object, symbol: str, exchange: str) -> None:
    from streamlit_app.api.client import ApiClient

    assert isinstance(client, ApiClient)
    timeframe = st.selectbox("Timeframe", TIMEFRAMES, index=5, key="mkt_tf")
    days = st.slider("Window (days)", min_value=1, max_value=365, value=90)
    end = datetime.now(tz=UTC)
    start = end - timedelta(days=days)
    params = {
        "instrument_symbol": symbol,
        "exchange_code": exchange,
        "timeframe": timeframe,
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "page_size": 100,
    }
    payload = run_api(
        client,
        lambda: endpoints.get_json(client, "/api/v1/analytics/candles/", params),
    )
    rows = page_results(payload) if payload is not None else []
    if not rows:
        st.info("No candles returned.")
        return
    incomplete = sum(1 for row in rows if row.get("is_complete") is False)
    st.caption(f"Rows: {len(rows)} · Incomplete: {incomplete}")
    st.plotly_chart(
        candle_figure(rows, title=f"{symbol}/{exchange} {timeframe}"),
        use_container_width=True,
    )


def _ticks(client: object, symbol: str, exchange: str) -> None:
    from streamlit_app.api.client import ApiClient

    assert isinstance(client, ApiClient)
    hours = st.slider("Lookback (hours)", min_value=1, max_value=72, value=24)
    end = datetime.now(tz=UTC)
    start = end - timedelta(hours=hours)
    params = {
        "instrument_symbol": symbol,
        "exchange_code": exchange,
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "page_size": 50,
    }
    payload = run_api(
        client,
        lambda: endpoints.get_json(client, "/api/v1/market-data/ticks/", params),
    )
    rows = page_results(payload) if payload is not None else []
    if not rows:
        st.info("No ticks returned.")
        return
    st.dataframe(
        [
            {
                "time": row.get("event_timestamp"),
                "price": row.get("price"),
                "qty": row.get("quantity"),
                "trade_id": row.get("exchange_trade_id"),
                "seq": row.get("sequence_number"),
            }
            for row in rows
        ],
        use_container_width=True,
        hide_index=True,
    )
