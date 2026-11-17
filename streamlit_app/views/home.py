"""Home trading dashboard: selectors, KPIs, candles, trades, daily OHLCV."""

from __future__ import annotations

import contextlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import streamlit as st

from streamlit_app.api import endpoints
from streamlit_app.api.cache import cached_bootstrap, cached_get, clear_read_caches, params_key
from streamlit_app.session import ACCESS_KEY, REFRESH_KEY, api_client
from streamlit_app.ui.charts import candle_figure
from streamlit_app.ui.theme import kpi_card
from streamlit_app.views.helpers import (
    SymbolExchange,
    as_list,
    as_mapping,
    bootstrap_instruments,
    page_results,
    run_api,
    select_symbol_and_exchange,
    symbol_exchange_from_instrument,
)

TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "1d")
VOLUME_THOUSAND = 1000


@dataclass(frozen=True)
class MarketScope:
    access: str
    refresh: str
    symbol: str
    exchange_code: str


def render_home() -> None:
    access, refresh = _tokens()
    boot = cached_bootstrap(access, refresh)
    portfolios = [as_mapping(item) for item in as_list(boot.get("portfolios"))]
    defaults = as_mapping(boot.get("defaults"))
    selected = _portfolio_select(portfolios)
    instruments = _instruments_for_portfolio(access, refresh, boot, selected)
    instrument = _instrument_select(instruments)
    timeframe = st.pills("Timeframe", TIMEFRAMES, default="1d", key="home_tf")
    tf = timeframe if isinstance(timeframe, str) else "1d"
    raw_days = defaults.get("chart_window_days")
    days = raw_days if isinstance(raw_days, int) else 90
    if instrument is None:
        st.info("Select a portfolio instrument to load market data.")
        return
    start_date, end_date = _market_date_range(days)
    if end_date < start_date:
        st.warning("End date must be on or after the start date.")
        return
    daily_rows = _load_daily(access, refresh, instrument, start_date, end_date)
    symbol, code = _symbol_code(instrument)
    scope = MarketScope(access=access, refresh=refresh, symbol=symbol, exchange_code=code)
    chart_col, side_col = st.columns([3.1, 1.15], gap="medium")
    with chart_col:
        _render_market_block(scope, instrument, tf, (start_date, end_date), daily_rows)
        _render_daily_table(daily_rows)
        _render_trades_table(scope, start_date, end_date)
    with side_col:
        _render_ticks(scope, daily_rows)
        _render_ingestion_preview(access, refresh)
        _build_candles_panel(boot, instrument)


def _tokens() -> tuple[str, str]:
    access = st.session_state.get(ACCESS_KEY)
    refresh = st.session_state.get(REFRESH_KEY)
    if not isinstance(access, str) or not isinstance(refresh, str):
        raise RuntimeError("Missing session tokens.")
    return access, refresh


def _instruments_for_portfolio(
    access: str,
    refresh: str,
    boot: Mapping[str, object],
    portfolio_id: str | None,
) -> list[Mapping[str, object]]:
    if portfolio_id is None:
        return [as_mapping(item) for item in as_list(boot.get("instruments"))]
    scoped = cached_bootstrap(access, refresh, portfolio_id)
    return [as_mapping(item) for item in as_list(scoped.get("instruments"))]


def _portfolio_select(portfolios: Sequence[Mapping[str, object]]) -> str | None:
    left, right = st.columns(2)
    if not portfolios:
        left.warning("No portfolios yet. Create one on the Portfolio page.")
        return None
    labels = {str(item.get("name")): str(item.get("id")) for item in portfolios}
    with left:
        choice = st.selectbox("Portfolio selector", list(labels), key="home_portfolio")
    with right:
        st.caption("Selected portfolio")
        st.markdown(f"**{choice}**")
    return labels.get(choice)


def _instrument_select(instruments: Sequence[Mapping[str, object]]) -> Mapping[str, object] | None:
    if not instruments:
        st.warning("No instruments in this portfolio.")
        return None
    labels: dict[str, Mapping[str, object]] = {}
    for item in instruments:
        exchange = as_mapping(item.get("exchange"))
        label = f"{item.get('symbol')} · {exchange.get('code', '')}"
        labels[label] = item
    left, right = st.columns(2)
    with left:
        choice = st.selectbox("Instrument selector", list(labels), key="home_instrument")
    with right:
        st.caption("Selected instrument")
        st.markdown(f"**{choice}**")
    return labels.get(choice)


def _symbol_code(instrument: Mapping[str, object]) -> tuple[str, str]:
    exchange = as_mapping(instrument.get("exchange"))
    return str(instrument.get("symbol") or ""), str(exchange.get("code") or "")


def _load_daily(
    access: str,
    refresh: str,
    instrument: Mapping[str, object],
    start_date: date,
    end_date: date,
) -> list[Mapping[str, object]]:
    symbol, code = _symbol_code(instrument)
    payload = cached_get(
        access,
        refresh,
        "/api/v1/market-data/daily-ohlcv/",
        params_key(
            {
                "instrument_symbol": symbol,
                "exchange_code": code,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "page_size": "100",
            }
        ),
    )
    return page_results(payload)


def _render_market_block(
    scope: MarketScope,
    instrument: Mapping[str, object],
    timeframe: str,
    date_range: tuple[date, date],
    daily_rows: list[Mapping[str, object]],
) -> None:
    exchange = as_mapping(instrument.get("exchange"))
    start_date, end_date = date_range
    start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=UTC)
    end = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, tzinfo=UTC)
    candle_params = {
        "instrument_symbol": scope.symbol,
        "exchange_code": scope.exchange_code,
        "timeframe": timeframe,
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "page_size": "100",
    }
    candles = cached_get(
        scope.access,
        scope.refresh,
        "/api/v1/analytics/candles/",
        params_key(candle_params),
    )
    rows = page_results(candles)
    _render_kpis(scope, rows, daily_rows, exchange)
    if not rows:
        st.info(
            "No trade-derived candles for this window. "
            "Daily OHLCV/trades below still load from MySQL. "
            "Use Build candles in the sidebar."
        )
        return
    incomplete = sum(1 for row in rows if row.get("is_complete") is False)
    st.markdown('<div class="mda-section-title">Candlestick chart</div>', unsafe_allow_html=True)
    st.caption(
        f"Exchange timezone: {exchange.get('timezone', 'n/a')} · Incomplete candles: {incomplete}"
    )
    st.plotly_chart(
        candle_figure(rows, title=f"{scope.symbol} / {scope.exchange_code} · {timeframe}"),
        use_container_width=True,
    )


def _render_kpis(
    scope: MarketScope,
    candle_rows: list[Mapping[str, object]],
    daily_rows: list[Mapping[str, object]],
    exchange: Mapping[str, object],
) -> None:
    latest_candle = candle_rows[-1] if candle_rows else {}
    latest_daily = daily_rows[0] if daily_rows else {}
    close = latest_candle.get("close", latest_daily.get("close"))
    volume = latest_candle.get("volume", latest_daily.get("volume"))
    daily = _daily_return(scope)
    drawdown = _max_drawdown(scope)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        kpi_card("Latest close", _fmt_price(close), sub=str(exchange.get("code") or ""))
    with c2:
        tone = "up" if daily.startswith("+") else "down" if daily.startswith("-") else ""
        kpi_card("Daily return", daily, tone=tone)
    with c3:
        kpi_card("Volume", _fmt_volume(volume))
    with c4:
        tone = "down" if drawdown.startswith("-") else ""
        kpi_card("Max drawdown", drawdown, tone=tone)


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _last_series_value(payload: object, field: str) -> float | None:
    series = page_results(payload)
    if not series:
        return None
    first = as_mapping(series[0])
    if field == "maximum_drawdown":
        return _as_float(first.get("maximum_drawdown"))
    points = page_results({"results": first.get("results")})
    value = points[-1].get("value") if points else None
    return _as_float(value)


def _daily_return(scope: MarketScope) -> str:
    end = date.today()
    start = end - timedelta(days=14)
    payload = cached_get(
        scope.access,
        scope.refresh,
        "/api/v1/analytics/returns/",
        params_key(
            {
                "instrument_symbol": scope.symbol,
                "exchange_code": scope.exchange_code,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "return_type": "simple",
            }
        ),
    )
    value = _last_series_value(payload, "value")
    return "—" if value is None else f"{value * 100:+.2f}%"


def _max_drawdown(scope: MarketScope) -> str:
    end = date.today()
    start = end - timedelta(days=365)
    payload = cached_get(
        scope.access,
        scope.refresh,
        "/api/v1/analytics/drawdown/",
        params_key(
            {
                "instrument_symbol": scope.symbol,
                "exchange_code": scope.exchange_code,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            }
        ),
    )
    value = _last_series_value(payload, "maximum_drawdown")
    return "—" if value is None else f"{value * 100:.1f}%"


def _anchor_dates(daily_rows: list[Mapping[str, object]]) -> tuple[date, date]:
    if daily_rows:
        raw = daily_rows[0].get("trading_date")
        if isinstance(raw, str) and raw:
            with contextlib.suppress(ValueError):
                latest = date.fromisoformat(raw[:10])
                return latest - timedelta(days=7), latest
    today = date.today()
    return today - timedelta(days=7), today


def _render_daily_table(daily_rows: list[Mapping[str, object]]) -> None:
    st.markdown('<div class="mda-section-title">Daily OHLCV</div>', unsafe_allow_html=True)
    if not daily_rows:
        st.caption("No daily OHLCV rows for this instrument/window.")
        return
    st.table(
        [
            {
                "Date": row.get("trading_date"),
                "Open": row.get("open"),
                "High": row.get("high"),
                "Low": row.get("low"),
                "Close": row.get("close"),
                "Volume": row.get("volume"),
            }
            for row in daily_rows[:15]
        ]
    )


def _market_date_range(days: int) -> tuple[date, date]:
    default_end = date.today()
    left, right = st.columns(2)
    with left:
        start_date = st.date_input(
            "Data from", value=default_end - timedelta(days=days), key="home_market_start"
        )
    with right:
        end_date = st.date_input("Data to", value=default_end, key="home_market_end")
    return start_date, end_date


def _render_trades_table(
    scope: MarketScope,
    start_date: date,
    end_date: date,
) -> None:
    rows = _load_trade_rows(scope, start_date, end_date)
    st.markdown('<div class="mda-section-title">Trades</div>', unsafe_allow_html=True)
    st.caption(f"Trades from {start_date} to {end_date}")
    if not rows:
        st.caption("No trades in this window.")
        return
    st.table(
        [
            {
                "Time": str(row.get("event_timestamp") or "")[:19],
                "Price": row.get("price"),
                "Qty": row.get("quantity"),
                "Trade ID": row.get("exchange_trade_id"),
            }
            for row in rows[:20]
        ]
    )


def _load_trade_rows(
    scope: MarketScope,
    start_date: date,
    end_date: date,
) -> list[Mapping[str, object]]:
    rows: list[Mapping[str, object]] = []
    chunk_start = start_date
    while chunk_start <= end_date:
        chunk_end = min(chunk_start + timedelta(days=30), end_date)
        start = datetime(chunk_start.year, chunk_start.month, chunk_start.day, tzinfo=UTC)
        end = datetime(chunk_end.year, chunk_end.month, chunk_end.day, 23, 59, 59, tzinfo=UTC)
        payload = cached_get(
            scope.access,
            scope.refresh,
            "/api/v1/market-data/ticks/",
            params_key(
                {
                    "instrument_symbol": scope.symbol,
                    "exchange_code": scope.exchange_code,
                    "start": start.isoformat().replace("+00:00", "Z"),
                    "end": end.isoformat().replace("+00:00", "Z"),
                    "ordering": "-event_timestamp",
                    "page_size": "25",
                }
            ),
        )
        rows.extend(page_results(payload))
        chunk_start = chunk_end + timedelta(days=1)
    return sorted(rows, key=lambda row: str(row.get("event_timestamp") or ""), reverse=True)


def _render_ticks(scope: MarketScope, daily_rows: list[Mapping[str, object]]) -> None:
    _start_day, end_day = _anchor_dates(daily_rows)
    start = datetime(end_day.year, end_day.month, end_day.day, tzinfo=UTC)
    end = datetime(end_day.year, end_day.month, end_day.day, 23, 59, 59, tzinfo=UTC)
    payload = cached_get(
        scope.access,
        scope.refresh,
        "/api/v1/market-data/ticks/",
        params_key(
            {
                "instrument_symbol": scope.symbol,
                "exchange_code": scope.exchange_code,
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": end.isoformat().replace("+00:00", "Z"),
                "ordering": "-event_timestamp",
                "page_size": "12",
            }
        ),
    )
    rows = page_results(payload)
    st.markdown('<div class="mda-section-title">Trade history</div>', unsafe_allow_html=True)
    if not rows:
        st.caption(f"No ticks on {end_day}.")
        return
    st.table(
        [
            {
                "Time": str(row.get("event_timestamp") or "")[:19],
                "Price": row.get("price"),
                "Size": row.get("quantity"),
            }
            for row in rows
        ]
    )


def _render_ingestion_preview(access: str, refresh: str) -> None:
    st.markdown('<div class="mda-section-title">Recent ingestion</div>', unsafe_allow_html=True)
    payload = cached_get(
        access,
        refresh,
        "/api/v1/ingestion/",
        params_key({"page_size": "5"}),
    )
    rows = page_results(payload)
    if not rows:
        st.caption("No recent ingestion activity.")
        return
    st.table(
        [
            {
                "File": row.get("file_name") or row.get("filename"),
                "Status": row.get("state") or row.get("status"),
                "Accepted": row.get("accepted_rows"),
            }
            for row in rows
        ]
    )


def _build_candles_panel(
    boot: Mapping[str, object],
    selected: Mapping[str, object],
) -> None:
    st.markdown('<div class="mda-section-title">Build candles</div>', unsafe_allow_html=True)
    st.caption("Queues POST /api/v1/analytics/aggregations/")
    instruments = bootstrap_instruments(boot) or [selected]
    preferred = symbol_exchange_from_instrument(selected)
    scope = select_symbol_and_exchange(instruments, key_prefix="agg", preferred=preferred)
    timeframes = st.multiselect(
        "Timeframes",
        list(TIMEFRAMES),
        default=["1m", "5m", "1h", "1d"],
        key="agg_timeframes",
    )
    start_day = st.date_input("Range start", value=date(2026, 10, 1), key="agg_start")
    end_day = st.date_input("Range end", value=date(2026, 11, 16), key="agg_end")
    if st.button("Run aggregation", type="primary", key="agg_run"):
        _run_aggregation_click(scope, timeframes, start_day, end_day)
    if st.button("Clear UI cache", key="agg_clear_cache"):
        clear_read_caches()
        st.rerun()


def _run_aggregation_click(
    scope: SymbolExchange | None,
    timeframes: Sequence[str],
    start_day: object,
    end_day: object,
) -> None:
    error = _aggregation_validation_error(scope, timeframes, start_day, end_day)
    if error is not None or scope is None:
        st.warning(error or "Select an instrument and exchange.")
        return
    if not isinstance(start_day, date) or not isinstance(end_day, date):
        st.warning("Pick a valid date range.")
        return
    body: dict[str, object] = {
        "instrument_symbol": scope.symbol,
        "exchange_code": scope.exchange_code,
        "timeframes": list(timeframes),
        "range_start": _day_start_iso(start_day),
        "range_end": _day_end_iso(end_day),
    }
    client = api_client()
    result = run_api(
        client,
        lambda: endpoints.post_json(client, "/api/v1/analytics/aggregations/", body),
    )
    client.close()
    if result is not None:
        payload = as_mapping(result)
        st.success(f"Aggregation accepted · run {payload.get('id', 'queued')}")
        clear_read_caches()


def _aggregation_validation_error(
    scope: SymbolExchange | None,
    timeframes: Sequence[str],
    start_day: object,
    end_day: object,
) -> str | None:
    message: str | None = None
    if scope is None:
        message = "Select an instrument and exchange."
    elif not timeframes:
        message = "Select at least one timeframe."
    elif not isinstance(start_day, date) or not isinstance(end_day, date):
        message = "Pick a valid date range."
    elif end_day < start_day:
        message = "Range end must be on or after range start."
    return message


def _day_start_iso(day: date) -> str:
    return datetime(day.year, day.month, day.day, tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _day_end_iso(day: date) -> str:
    return (
        datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _fmt_price(value: object) -> str:
    if isinstance(value, int | float):
        return f"${value:,.2f}"
    if isinstance(value, str) and value:
        with contextlib.suppress(ValueError):
            return f"${float(value):,.2f}"
    return "—"


def _fmt_volume(value: object) -> str:
    number: float | None = None
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        with contextlib.suppress(ValueError):
            number = float(value)
    if number is None:
        return "—"
    if number >= VOLUME_THOUSAND:
        return f"{number / VOLUME_THOUSAND:.2f}K"
    return f"{number:.0f}"
