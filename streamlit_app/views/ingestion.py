"""Ingestion configuration and monitoring UI."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import streamlit as st

from streamlit_app.api import endpoints
from streamlit_app.session import api_client
from streamlit_app.views.helpers import as_list, as_mapping, page_results, run_api

# Mirrors apps.ingestion.models.ValidatorType and parsers.profiles column sets.
VALIDATORS = (
    "REQUIRED",
    "NUMERIC",
    "POSITIVE",
    "NON_NEGATIVE",
    "MIN_VALUE",
    "MAX_VALUE",
    "MAX_DECIMAL_PLACES",
    "DATETIME",
    "ENUM",
    "OHLC_RELATIONSHIP",
    "INSTRUMENT_EXISTS",
    "EXCHANGE_EXISTS",
    "EXCHANGE_INSTRUMENT_MATCH",
    "DUPLICATE_CHECK",
)
DAILY_FIELDS = (
    "exchange_identifier",
    "instrument_identifier",
    "instrument_name",
    "asset_class",
    "currency",
    "trading_date",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "adjusted_close",
    "volume",
)
TICK_FIELDS = (
    "exchange_identifier",
    "instrument_identifier",
    "event_timestamp",
    "price",
    "quantity",
    "exchange_trade_id",
    "sequence_number",
    "side",
    "source_metadata",
    "deduplication_key",
)
DATA_TYPES = ("DAILY_OHLCV", "TICK_DATA")


def render_ingestion() -> None:
    st.title("Ingestion")
    tabs = st.tabs(["Monitor", "Validation rules", "Ingestion rules", "Import"])
    client = api_client()
    with tabs[0]:
        _monitor(client)
    with tabs[1]:
        _validation_rules(client)
    with tabs[2]:
        _ingestion_rules(client)
    with tabs[3]:
        _import_files(client)
    client.close()


def _monitor(client: object) -> None:
    from streamlit_app.api.client import ApiClient

    assert isinstance(client, ApiClient)
    payload = run_api(
        client,
        lambda: endpoints.get_json(client, "/api/v1/ingestion/", {"page_size": 20}),
    )
    rows = page_results(payload) if payload is not None else []
    st.dataframe(
        [
            {
                "file": row.get("file_name") or row.get("filename"),
                "uuid": row.get("id"),
                "status": row.get("state") or row.get("status"),
                "accepted": row.get("accepted_rows"),
                "rejected": row.get("rejected_rows"),
                "duplicates": row.get("duplicate_rows"),
                "error": row.get("error_summary"),
            }
            for row in rows
        ],
        use_container_width=True,
        hide_index=True,
    )
    if not rows:
        return
    selected = st.selectbox("Inspect ingestion", [str(row.get("id")) for row in rows])
    if st.button("Show rejections"):
        rejections = run_api(
            client,
            lambda: endpoints.get_json(client, f"/api/v1/ingestion/{selected}/rejections/"),
        )
        st.dataframe(page_results(rejections) if rejections is not None else [], hide_index=True)
    if st.button("Retry selected"):
        result = run_api(
            client,
            lambda: endpoints.post_json(client, f"/api/v1/ingestion/{selected}/retry/", {}),
        )
        if result is not None:
            st.success("Retry requested.")


def _validation_rules(client: object) -> None:
    from streamlit_app.api.client import ApiClient

    assert isinstance(client, ApiClient)
    existing = run_api(
        client,
        lambda: endpoints.get_json(client, "/api/v1/ingestion/validation-rule-sets/"),
    )
    rows = page_results(existing) if existing is not None else []
    st.subheader("Existing rule sets")
    st.dataframe(rows, hide_index=True)
    st.subheader("Create rule set")
    st.caption("Name + version must be unique. Bump version to add another set with the same name.")
    name = st.text_input("Name", key="vrs_name")
    data_type = st.selectbox("Data type", list(DATA_TYPES), key="vrs_data_type")
    version = st.text_input("Version", value="1", key="vrs_version")
    fields = _fields_for_data_type(data_type)
    field_options = _field_options(rows, fields)
    field_name = st.selectbox("Field name", field_options, key="vrs_field_name")
    validator = st.selectbox("Validator", list(VALIDATORS), key="vrs_validator")
    sort_order = st.number_input("Sort order", min_value=0, value=1, key="vrs_sort")
    if not st.button("Create", type="primary", key="vrs_create"):
        return
    body = {
        "name": name.strip(),
        "data_type": data_type,
        "version": version.strip() or "1",
        "rules": [
            {
                "field_name": field_name,
                "validator": validator,
                "sort_order": int(sort_order),
            }
        ],
    }
    result = run_api(
        client,
        lambda: endpoints.post_json(client, "/api/v1/ingestion/validation-rule-sets/", body),
    )
    if result is not None:
        st.success("Validation rule set created.")
        st.rerun()


def _fields_for_data_type(data_type: object) -> tuple[str, ...]:
    return TICK_FIELDS if data_type == "TICK_DATA" else DAILY_FIELDS


def _field_options(
    existing_sets: Sequence[Mapping[str, object]],
    canonical: Sequence[str],
) -> list[str]:
    seen: list[str] = list(canonical)
    for rule_set in existing_sets:
        for rule in as_list(rule_set.get("rules")):
            if not isinstance(rule, Mapping):
                continue
            field = rule.get("field_name")
            if isinstance(field, str) and field and field not in seen:
                seen.append(field)
    return seen


def _ingestion_rules(client: object) -> None:
    from streamlit_app.api.client import ApiClient

    assert isinstance(client, ApiClient)
    sets = run_api(
        client,
        lambda: endpoints.get_json(client, "/api/v1/ingestion/validation-rule-sets/"),
    )
    set_rows = page_results(sets) if sets is not None else []
    set_labels = {f"{row.get('name')}@{row.get('version')}": str(row.get("id")) for row in set_rows}
    rules = run_api(client, lambda: endpoints.get_json(client, "/api/v1/ingestion/rules/"))
    st.subheader("Existing rules")
    st.dataframe(page_results(rules) if rules is not None else [], hide_index=True)
    if not set_labels:
        st.warning("Create a validation rule set before an ingestion rule.")
        return
    name = st.text_input("Rule name", key="ing_rule_name")
    data_type = st.selectbox("Data type", list(DATA_TYPES), key="rule_dt")
    vrs = st.selectbox("Validation rule set", list(set_labels), key="ing_rule_vrs")
    pattern = st.text_input("File pattern", value="synthetic_market_clean_*.csv", key="ing_pat")
    chunk_size = st.number_input("Chunk size", min_value=1, value=5000, key="ing_chunk")
    if not st.button("Create rule", type="primary", key="ing_rule_create"):
        return
    body: dict[str, object] = {
        "name": name.strip(),
        "data_type": data_type,
        "storage_prefix": "files",
        "file_pattern": pattern,
        "chunk_size": int(chunk_size),
        "parser_config": {"format": "csv"},
        "instrument_mapping": {"instrument_identifier": "symbol"},
        "exchange_mapping": {"exchange_identifier": "exchange"},
        "validation_rule_set_id": set_labels[vrs],
    }
    result = run_api(
        client, lambda: endpoints.post_json(client, "/api/v1/ingestion/rules/", body)
    )
    if result is not None:
        st.success("Ingestion rule created.")
        st.rerun()


def _import_files(client: object) -> None:
    from streamlit_app.api.client import ApiClient

    assert isinstance(client, ApiClient)
    st.caption(
        "Import stores drop-folder files, then queues only files that match an "
        "enabled ingestion rule. Create rules first; re-run import after adding rules "
        "to queue already-stored files."
    )
    rules = run_api(client, lambda: endpoints.get_json(client, "/api/v1/ingestion/rules/"))
    rule_rows = page_results(rules) if rules is not None else []
    if not rule_rows:
        st.warning("No ingestion rules yet — import will store files but queue nothing.")
    backends = st.multiselect("Storage backends", ["local", "s3"], default=["local"])
    storage_prefix = st.text_input("storage_prefix", value="files", key="import_storage_prefix")
    prefix = storage_prefix.strip() or "files"
    if st.button("Run import", type="primary"):
        body: Mapping[str, object] = {
            "storage_backends": backends,
            "storage_prefix": prefix,
        }
        result = run_api(
            client,
            lambda: endpoints.post_json(client, "/api/v1/ingestion/files/import/", dict(body)),
        )
        if result is not None:
            _show_import_result(result)


def _show_import_result(result: object) -> None:
    data = as_mapping(result) if isinstance(result, Mapping) else {}
    stored = as_list(data.get("stored"))
    skipped = as_list(data.get("skipped"))
    queued = as_list(data.get("queued"))
    if queued:
        st.success(f"Queued {len(queued)} file(s) for conversion/ingestion.")
    elif stored:
        st.warning(
            f"Stored {len(stored)} file(s) but queued 0. "
            "No enabled rule matched the filenames, or they were already queued. "
            "Check patterns on the Ingestion rules tab, then re-run import."
        )
    else:
        st.info(f"Nothing new stored. Skipped {len(skipped)} file(s).")
    st.json(data)
