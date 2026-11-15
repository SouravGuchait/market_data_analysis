"""Config helpers for Streamlit."""

from __future__ import annotations

from streamlit_app import config


def test_api_base_url_default(monkeypatch: object) -> None:
    from _pytest.monkeypatch import MonkeyPatch

    assert isinstance(monkeypatch, MonkeyPatch)
    monkeypatch.delenv("MDA_API_BASE_URL", raising=False)
    config.api_base_url.cache_clear()
    assert config.api_base_url() == "http://127.0.0.1:8000"
    config.api_base_url.cache_clear()


def test_timeout_fallback(monkeypatch: object) -> None:
    from _pytest.monkeypatch import MonkeyPatch

    assert isinstance(monkeypatch, MonkeyPatch)
    monkeypatch.setenv("MDA_API_TIMEOUT_SECONDS", "not-a-number")
    config.request_timeout_seconds.cache_clear()
    assert config.request_timeout_seconds() == 30.0
    config.request_timeout_seconds.cache_clear()
