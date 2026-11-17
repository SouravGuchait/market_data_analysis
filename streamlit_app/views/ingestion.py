"""Ingestion configuration and monitoring UI."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import streamlit as st

from streamlit_app.api import endpoints
from streamlit_app.config import import_timeout_seconds
from streamlit_app.session import api_client
from streamlit_app.views.helpers import as_list, as_mapping, page_results, run_api

DATA_TYPES = ("DAILY_OHLCV", "TICK_DATA")


def render_ingestion() -> None:
    st.title("Ingestion")
    tabs = st.tabs(["Monitor", "Ingestion rules", "Import"])
    client = api_client()
    with tabs[0]:
        _monitor(client)
    with tabs[1]:
        _ingestion_rules(client)
    with tabs[2]:
        _import_files(client)
    client.close()


def _monitor(client: object) -> None:
    from streamlit_app.api.client import ApiClient

    assert isinstance(client, ApiClient)
    payload = run_api(
        client,
        lambda: endpoints.get_json(client, "/api/v1/ingestion/", {"page_size": 100}),
    )
    rows = page_results(payload) if payload is not None else []
    if not rows:
        st.info("No ingestion files yet.")
        return

    sections = _status_sections(rows)
    tabs = st.tabs(list(sections))
    for tab, (label, section_rows) in zip(tabs, sections.items(), strict=True):
        with tab:
            if section_rows:
                st.dataframe(
                    _ingestion_table(section_rows), use_container_width=True, hide_index=True
                )
            else:
                st.info("No files in this section.")
            if label == "Accepted rows":
                st.caption("Accepted rows are shown as per-file counts in the ingestion log.")
            if label == "Rejected rows" and section_rows:
                _show_rejections(client, section_rows)
            if label == "All files":
                _retry_control(client, rows)


def _status_sections(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, list[Mapping[str, object]]]:
    return {
        "All files": list(rows),
        "Accepted rows": [row for row in rows if _row_count(row, "accepted_rows") > 0],
        "Rejected rows": [row for row in rows if _row_count(row, "rejected_rows") > 0],
        "In progress": [
            row
            for row in rows
            if str(row.get("status") or row.get("state") or "").lower()
            in {"discovered", "queued", "processing", "retrying"}
        ],
        "Failed": [
            row
            for row in rows
            if str(row.get("status") or row.get("state") or "").lower() == "failed"
        ],
    }


def _row_count(row: Mapping[str, object], field: str) -> int:
    value = row.get(field)
    if not isinstance(value, int | str):
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _ingestion_table(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "file": row.get("filename") or row.get("file_name"),
            "ingestion_id": row.get("id"),
            "status": row.get("status") or row.get("state"),
            "rows_read": row.get("rows_read"),
            "accepted_rows": row.get("accepted_rows"),
            "rejected_rows": row.get("rejected_rows"),
            "duplicates": row.get("duplicate_rows"),
            "completed_at": row.get("completed_at"),
            "error": row.get("error_summary"),
        }
        for row in rows
    ]


def _show_rejections(client: object, rows: Sequence[Mapping[str, object]]) -> None:
    from streamlit_app.api.client import ApiClient

    assert isinstance(client, ApiClient)
    file_names = {
        str(row.get("id")): str(row.get("filename") or row.get("file_name") or "Unknown file")
        for row in rows
        if row.get("id")
    }
    selected = st.selectbox(
        "Rejected rows for file",
        list(file_names),
        format_func=lambda ingestion_id: f"{file_names[ingestion_id]} ({ingestion_id[:8]})",
        key="ingestion_rejection_file",
    )
    page = int(
        st.number_input(
            "Rejected row page",
            min_value=1,
            value=1,
            step=1,
            key=f"rejection_page_{selected}",
        )
    )
    if not st.button("Load rejected rows", key="load_ingestion_rejections"):
        return
    rejections = run_api(
        client,
        lambda: endpoints.get_json(
            client,
            f"/api/v1/ingestion/{selected}/rejections/",
            {"page": page, "page_size": 100},
        ),
    )
    rejection_rows = page_results(rejections) if rejections is not None else []
    st.dataframe(
        _rejection_table(rejection_rows),
        use_container_width=True,
        hide_index=True,
    )


def _rejection_table(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "source_row": row.get("source_row_number"),
            "reason": row.get("reason_code"),
            "field": row.get("field_name"),
            "error": row.get("error_message"),
            "rejected_at": row.get("rejected_at"),
        }
        for row in rows
    ]


def _retry_control(client: object, rows: Sequence[Mapping[str, object]]) -> None:
    from streamlit_app.api.client import ApiClient

    assert isinstance(client, ApiClient)
    labels = {
        str(row.get("id")): str(row.get("filename") or row.get("file_name") or "Unknown file")
        for row in rows
        if row.get("id")
    }
    if not labels:
        return
    selected = st.selectbox(
        "Retry file",
        list(labels),
        format_func=lambda ingestion_id: f"{labels[ingestion_id]} ({ingestion_id[:8]})",
        key="retry_ingestion_file",
    )
    if not st.button("Retry selected", key="retry_ingestion"):
        return
    result = run_api(
        client,
        lambda: endpoints.post_json(client, f"/api/v1/ingestion/{selected}/retry/", {}),
    )
    if result is not None:
        st.success("Retry requested.")


def _ingestion_rules(client: object) -> None:
    from streamlit_app.api.client import ApiClient

    assert isinstance(client, ApiClient)
    rules = run_api(client, lambda: endpoints.get_json(client, "/api/v1/ingestion/rules/"))
    st.subheader("Existing rules")
    st.dataframe(page_results(rules) if rules is not None else [], hide_index=True)
    name = st.text_input("Rule name", key="ing_rule_name")
    data_type = st.selectbox("Data type", list(DATA_TYPES), key="rule_dt")
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
    }
    result = run_api(client, lambda: endpoints.post_json(client, "/api/v1/ingestion/rules/", body))
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
            lambda: endpoints.post_json(
                client,
                "/api/v1/ingestion/files/import/",
                dict(body),
                timeout=import_timeout_seconds(),
            ),
        )
        if result is not None:
            _show_import_result(result, backends)


def _show_import_result(result: object, backends: Sequence[str] = ()) -> None:
    data = as_mapping(result) if isinstance(result, Mapping) else {}
    stored = as_list(data.get("stored"))
    skipped = as_list(data.get("skipped"))
    queued = as_list(data.get("queued"))
    scanned_value = data.get("scanned")
    scanned = as_mapping(scanned_value) if isinstance(scanned_value, Mapping) else {}
    local_files = _scanned_count(scanned, "local")
    s3_files = _scanned_count(scanned, "s3")
    if queued:
        st.success(f"Queued {len(queued)} file(s) for conversion/ingestion.")
    elif stored:
        st.warning(
            f"Stored {len(stored)} file(s) but queued 0. "
            "No enabled rule matched the filenames, or they were already queued. "
            "Check patterns on the Ingestion rules tab, then re-run import."
        )
    elif isinstance(scanned_value, Mapping) and local_files == 0 and s3_files == 0:
        st.warning(_empty_scan_message(backends))
    else:
        st.info(
            f"Nothing new stored. Scanned {local_files} local file(s) and "
            f"{s3_files} S3 object(s); skipped {len(skipped)}."
        )
    stored_rows = page_results(stored)
    if stored_rows:
        st.subheader("Stored files")
        st.dataframe(
            [
                {
                    "file": row.get("original_filename"),
                    "backend": row.get("storage_backend"),
                    "format": row.get("detected_format"),
                    "size_bytes": row.get("file_size"),
                    "uploaded_at": row.get("uploaded_at"),
                }
                for row in stored_rows
            ],
            use_container_width=True,
            hide_index=True,
        )
    skipped_rows = page_results(skipped)
    if skipped_rows:
        st.subheader("Skipped files")
        st.dataframe(
            [{"file": row.get("name"), "reason": row.get("reason")} for row in skipped_rows],
            use_container_width=True,
            hide_index=True,
        )
    queued_rows = page_results(queued)
    if queued_rows:
        st.subheader("Queued files")
        st.dataframe(
            [
                {
                    "file": row.get("name"),
                    "ingestion_id": row.get("id"),
                    "rule_id": row.get("rule_id"),
                }
                for row in queued_rows
            ],
            use_container_width=True,
            hide_index=True,
        )


def _scanned_count(scanned: Mapping[str, object], key: str) -> int:
    value = scanned.get(key)
    return value if isinstance(value, int) else 0


def _empty_scan_message(backends: Sequence[str]) -> str:
    sources = " and ".join(backends) if backends else "the configured sources"
    settings_names = {
        "local": "INGESTION_LOCAL_ROOT",
        "s3": "INGESTION_S3_BUCKET/INGESTION_S3_PREFIX",
    }
    hints = ", ".join(settings_names[backend] for backend in backends if backend in settings_names)
    return (
        f"No files were found in {sources}. "
        f"Check {hints or 'the ingestion settings'}, then re-run import."
    )
