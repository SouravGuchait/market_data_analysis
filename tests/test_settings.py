"""Settings helper and environment configuration tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from config.settings.base import env_bool, env_int, env_list, env_str


def test_env_str_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDA_TEST_VALUE", "configured")

    assert env_str("MDA_TEST_VALUE") == "configured"


def test_env_str_rejects_missing_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MDA_TEST_MISSING", raising=False)

    with pytest.raises(ImproperlyConfigured):
        env_str("MDA_TEST_MISSING")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("off", False),
    ],
)
def test_env_bool_parses_supported_values(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    expected: bool,
) -> None:
    monkeypatch.setenv("MDA_TEST_BOOL", raw)

    assert env_bool("MDA_TEST_BOOL") is expected


def test_env_bool_rejects_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDA_TEST_BOOL", "maybe")

    with pytest.raises(ImproperlyConfigured):
        env_bool("MDA_TEST_BOOL")


def test_env_int_returns_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MDA_TEST_INT", raising=False)

    assert env_int("MDA_TEST_INT", 42) == 42


def test_env_int_rejects_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDA_TEST_INT", "not-an-int")

    with pytest.raises(ImproperlyConfigured):
        env_int("MDA_TEST_INT", 1)


def test_env_list_parses_and_strips_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDA_TEST_LIST", "alpha, beta ,, gamma")

    assert env_list("MDA_TEST_LIST") == ["alpha", "beta", "gamma"]


def test_required_apps_are_installed() -> None:
    required = {
        "apps.accounts",
        "apps.portfolios",
        "apps.instruments",
        "apps.exchanges",
        "apps.market_data",
        "apps.ingestion",
        "apps.analytics",
        "apps.audit",
        "apps.common",
    }

    assert required.issubset(set(settings.INSTALLED_APPS))


def test_timezone_aware_configuration() -> None:
    assert settings.USE_TZ is True
    assert settings.TIME_ZONE == "UTC"


def test_custom_user_model_is_configured() -> None:
    assert settings.AUTH_USER_MODEL == "accounts.User"


def test_test_settings_use_sqlite_and_local_memory_cache() -> None:
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
    assert settings.CACHES["default"]["BACKEND"] == "django.core.cache.backends.locmem.LocMemCache"


def test_test_settings_configure_console_logging() -> None:
    assert "console" in settings.LOGGING["handlers"]


def test_log_path_is_absolute() -> None:
    assert Path(settings.LOG_FILE_PATH).is_absolute()
    assert Path(settings.INGESTION_LOCAL_ROOT).is_absolute()


def test_csrf_protection_is_enabled() -> None:
    assert "django.middleware.csrf.CsrfViewMiddleware" in settings.MIDDLEWARE
