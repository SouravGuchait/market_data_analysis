"""Shared UI helpers for parsing API payloads."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import streamlit as st

from streamlit_app.api.client import ApiClient
from streamlit_app.api.errors import ApiError, AuthExpiredError
from streamlit_app.session import clear_session, sync_client_tokens


@dataclass(frozen=True)
class SymbolExchange:
    symbol: str
    exchange_code: str


def as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def as_list(value: object) -> list[object]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return list(value)
    return []


def page_results(payload: object) -> list[Mapping[str, object]]:
    data = as_mapping(payload)
    rows = data.get("results", payload)
    return [as_mapping(row) for row in as_list(rows) if isinstance(row, Mapping)]


def bootstrap_instruments(boot: Mapping[str, object]) -> list[Mapping[str, object]]:
    return [as_mapping(item) for item in as_list(boot.get("instruments"))]


def symbol_exchange_from_instrument(instrument: Mapping[str, object]) -> SymbolExchange:
    exchange = as_mapping(instrument.get("exchange"))
    return SymbolExchange(
        symbol=str(instrument.get("symbol") or ""),
        exchange_code=str(exchange.get("code") or ""),
    )


def find_instrument(
    instruments: Sequence[Mapping[str, object]],
    *,
    symbol: str,
    exchange_code: str,
) -> Mapping[str, object] | None:
    target_symbol = symbol.strip().upper()
    target_code = exchange_code.strip().upper()
    for item in instruments:
        exchange = as_mapping(item.get("exchange"))
        item_symbol = str(item.get("symbol") or "").strip().upper()
        item_code = str(exchange.get("code") or "").strip().upper()
        if item_symbol == target_symbol and item_code == target_code:
            return item
    return None



def select_symbol_and_exchange(
    instruments: Sequence[Mapping[str, object]],
    *,
    key_prefix: str,
    preferred: SymbolExchange | None = None,
) -> SymbolExchange | None:
    """Render Instrument symbol / Exchange code selectboxes from bootstrap rows."""

    by_symbol = _codes_by_symbol(instruments)
    if not by_symbol:
        st.warning("No instruments available from bootstrap.")
        return None
    symbols = sorted(by_symbol)
    symbol = st.selectbox(
        "Instrument symbol",
        symbols,
        index=_preferred_index(symbols, preferred.symbol if preferred else None),
        key=f"{key_prefix}_symbol",
    )
    codes = by_symbol.get(symbol, []) if isinstance(symbol, str) else []
    if not codes:
        st.warning("No exchange codes for the selected symbol.")
        return None
    exchange = st.selectbox(
        "Exchange code",
        codes,
        index=_preferred_index(codes, preferred.exchange_code if preferred else None),
        key=f"{key_prefix}_exchange",
    )
    selected: SymbolExchange | None = None
    if isinstance(symbol, str) and isinstance(exchange, str):
        selected = SymbolExchange(symbol=symbol, exchange_code=exchange)
    return selected


def _preferred_index(options: Sequence[str], value: str | None) -> int:
    return options.index(value) if value is not None and value in options else 0


def _codes_by_symbol(
    instruments: Sequence[Mapping[str, object]],
) -> dict[str, list[str]]:
    by_symbol: dict[str, list[str]] = {}
    for item in instruments:
        symbol = str(item.get("symbol") or "").strip()
        exchange = as_mapping(item.get("exchange"))
        code = str(exchange.get("code") or "").strip()
        if not symbol or not code:
            continue
        codes = by_symbol.setdefault(symbol, [])
        if code not in codes:
            codes.append(code)
    return by_symbol


def run_api[T](client: ApiClient, action: Callable[[], T]) -> T | None:
    """Execute an API callable and surface errors in the UI."""

    try:
        result = action()
        sync_client_tokens(client)
        return result
    except AuthExpiredError as exc:
        clear_session()
        st.error(exc.message)
        st.rerun()
        return None
    except ApiError as exc:
        st.error(exc.message)
        return None
