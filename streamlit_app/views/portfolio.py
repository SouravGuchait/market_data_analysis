"""Portfolio management pages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import streamlit as st

from streamlit_app.api import endpoints
from streamlit_app.api.cache import cached_bootstrap, clear_read_caches
from streamlit_app.session import ACCESS_KEY, REFRESH_KEY, api_client
from streamlit_app.views.helpers import (
    as_list,
    as_mapping,
    bootstrap_instruments,
    find_instrument,
    page_results,
    run_api,
    select_symbol_and_exchange,
)


def render_portfolio() -> None:
    st.title("Portfolio")
    tab_summary, tab_create, tab_instruments = st.tabs(
        ["Summary", "Create / Update", "Instruments"]
    )
    client = api_client()
    with tab_summary:
        _summary(client)
    with tab_create:
        _create_update(client)
    with tab_instruments:
        _instruments(client)
    client.close()


def _summary(client: object) -> None:
    from streamlit_app.api.client import ApiClient

    assert isinstance(client, ApiClient)
    payload = run_api(client, lambda: endpoints.get_json(client, "/api/v1/portfolios/"))
    rows = page_results(payload) if payload is not None else []
    if not rows:
        st.info("No portfolios yet.")
        return
    st.dataframe(
        [
            {
                "name": row.get("name"),
                "status": row.get("status"),
                "instruments": len(as_list(row.get("instruments"))),
                "id": row.get("id"),
            }
            for row in rows
        ],
        use_container_width=True,
        hide_index=True,
    )


def _create_update(client: object) -> None:
    from streamlit_app.api.client import ApiClient

    assert isinstance(client, ApiClient)
    with st.form("create_portfolio"):
        name = st.text_input("Name")
        description = st.text_area("Description")
        status = st.selectbox("Status", ["active", "archived", "draft"])
        submitted = st.form_submit_button("Create portfolio")
    if submitted:
        run_api(
            client,
            lambda: endpoints.post_json(
                client,
                "/api/v1/portfolios/",
                {"name": name, "description": description, "status": status},
            ),
        )
        st.success("Portfolio created.")


def _instruments(client: object) -> None:
    from streamlit_app.api.client import ApiClient

    assert isinstance(client, ApiClient)
    payload = run_api(client, lambda: endpoints.get_json(client, "/api/v1/portfolios/"))
    portfolios = page_results(payload) if payload is not None else []
    if not portfolios:
        st.info("Create a portfolio first.")
        return
    labels = {str(row.get("name")): str(row.get("id")) for row in portfolios}
    name = st.selectbox("Portfolio", list(labels))
    portfolio_id = labels[name]
    membership = run_api(
        client,
        lambda: endpoints.get_json(client, f"/api/v1/portfolios/{portfolio_id}/instruments/"),
    )
    rows = page_results(membership) if membership is not None else []
    st.subheader("Membership")
    st.dataframe(_membership_rows(rows), use_container_width=True, hide_index=True)
    _attach_instrument(client, portfolio_id)
    if rows:
        remove_id = st.selectbox(
            "Remove membership",
            [str(row.get("id")) for row in rows],
        )
        if st.button("Remove selected"):
            run_api(
                client,
                lambda: endpoints.delete_json(
                    client,
                    f"/api/v1/portfolios/{portfolio_id}/instruments/{remove_id}/",
                ),
            )
            clear_read_caches()
            st.rerun()


def _attach_instrument(client: object, portfolio_id: str) -> None:
    from streamlit_app.api.client import ApiClient

    assert isinstance(client, ApiClient)
    access = st.session_state.get(ACCESS_KEY)
    refresh = st.session_state.get(REFRESH_KEY)
    st.subheader("Attach instrument")
    if not isinstance(access, str) or not isinstance(refresh, str):
        st.error("Not authenticated.")
        return
    boot = cached_bootstrap(access, refresh)
    instruments = bootstrap_instruments(boot)
    asset_options, currency_options = _choice_options(boot, instruments)
    scope = select_symbol_and_exchange(instruments, key_prefix="pf_attach")
    selected = (
        None
        if scope is None
        else find_instrument(instruments, symbol=scope.symbol, exchange_code=scope.exchange_code)
    )
    missing = _attach_missing_message(scope, selected, asset_options, currency_options)
    if missing is not None or scope is None or selected is None:
        if missing is not None:
            st.warning(missing)
        return
    # Bulk attach with create_if_missing=false requires exact master metadata.
    # Lock name/asset_class/currency to the selected listing (do not edit them).
    name = str(selected.get("name") or scope.symbol)
    asset_class = str(selected.get("asset_class") or "")
    currency = str(selected.get("currency") or "")
    if asset_class not in asset_options or currency not in currency_options:
        st.warning(
            f"{scope.symbol}@{scope.exchange_code} metadata is not in bootstrap choices "
            f"({asset_class}/{currency}). Re-seed instruments or refresh bootstrap."
        )
        return
    widget_key = f"{scope.symbol}_{scope.exchange_code}"
    st.text_input("Name", value=name, disabled=True, key=f"pf_attach_name_{widget_key}")
    st.text_input(
        "Asset class", value=asset_class, disabled=True, key=f"pf_attach_asset_{widget_key}"
    )
    st.text_input("Currency", value=currency, disabled=True, key=f"pf_attach_currency_{widget_key}")
    alias = st.text_input("Alias", value="", key=f"pf_attach_alias_{widget_key}")
    if st.button("Add", type="primary", key=f"pf_attach_add_{widget_key}"):
        _post_bulk_attach(
            client,
            portfolio_id,
            {
                "exchange_code": scope.exchange_code,
                "symbol": scope.symbol,
                "name": name,
                "asset_class": asset_class,
                "currency": currency,
                "alias": alias.strip(),
                "is_active": True,
            },
        )


def _attach_missing_message(
    scope: object,
    selected: Mapping[str, object] | None,
    asset_options: Sequence[str],
    currency_options: Sequence[str],
) -> str | None:
    message: str | None = None
    if scope is not None and selected is None:
        message = "Selected instrument was not found in bootstrap."
    elif selected is not None and not asset_options:
        message = "No asset classes found in the database."
    elif selected is not None and not currency_options:
        message = "No currencies found in the database."
    return message


def _choice_options(
    boot: Mapping[str, object],
    instruments: Sequence[Mapping[str, object]],
) -> tuple[list[str], list[str]]:
    choices = as_mapping(boot.get("instrument_choices"))
    asset_options = _string_options(choices.get("asset_classes"))
    currency_options = _string_options(choices.get("currencies"))
    if not asset_options:
        asset_options = _unique_field(instruments, "asset_class")
    if not currency_options:
        currency_options = _unique_field(instruments, "currency")
    return asset_options, currency_options


def _string_options(value: object) -> list[str]:
    return [str(item) for item in as_list(value) if isinstance(item, str) and item]


def _unique_field(instruments: Sequence[Mapping[str, object]], field: str) -> list[str]:
    seen: list[str] = []
    for item in instruments:
        raw = item.get(field)
        if isinstance(raw, str) and raw and raw not in seen:
            seen.append(raw)
    return seen


def _post_bulk_attach(
    client: object,
    portfolio_id: str,
    instrument: Mapping[str, object],
) -> None:
    from streamlit_app.api.client import ApiClient

    assert isinstance(client, ApiClient)
    body = {"create_if_missing": False, "instruments": [dict(instrument)]}
    result = run_api(
        client,
        lambda: endpoints.post_json(
            client,
            f"/api/v1/portfolios/{portfolio_id}/instruments/bulk/",
            body,
        ),
    )
    if result is not None:
        st.success("Instrument attached.")
        clear_read_caches()
        st.rerun()


def _membership_rows(rows: list[Mapping[str, object]]) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for row in rows:
        instrument = as_mapping(row.get("instrument"))
        exchange = as_mapping(row.get("exchange"))
        output.append(
            {
                "id": row.get("id"),
                "symbol": instrument.get("symbol"),
                "exchange": exchange.get("code"),
                "alias": row.get("alias"),
                "active": row.get("is_active"),
            }
        )
    return output
