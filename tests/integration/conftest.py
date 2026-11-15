"""Fixtures for MySQL / Redis / Celery / Streamlit integration tests."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.conf import settings
from rest_framework.test import APIClient

ROOT = Path(__file__).resolve().parents[2]
STRONG_PASSWORD = "Violet!River_Orbit-Glass_9274"
WORKER_START_TIMEOUT_SECONDS = 45
PING_TIMEOUT_SECONDS = 30


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: requires a real MySQL database, Redis cache and Celery broker",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    if os.environ.get("RUN_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="Set RUN_INTEGRATION=1 to run integration tests")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def unique_username() -> str:
    return f"integ_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def unique_email(unique_username: str) -> str:
    return f"{unique_username}@example.com"


@pytest.fixture
def strong_password() -> str:
    return STRONG_PASSWORD


@pytest.fixture(scope="session")
def celery_worker_process(
    django_db_setup: None, django_db_blocker: object
) -> Iterator[subprocess.Popen[bytes]]:
    """Start a solo Celery worker against the pytest-django test database."""

    del django_db_blocker
    db = settings.DATABASES["default"]
    env = os.environ.copy()
    env.update(
        {
            "DJANGO_SETTINGS_MODULE": "config.settings.integration",
            "DB_NAME": str(db["NAME"]),
            "DB_USER": str(db["USER"]),
            "DB_PASSWORD": str(db["PASSWORD"]),
            "DB_HOST": str(db["HOST"]),
            "DB_PORT": str(db["PORT"]),
            "CACHE_URL": str(settings.CACHES["default"]["LOCATION"]),
            "CELERY_BROKER_URL": str(settings.CELERY_BROKER_URL),
            "CELERY_RESULT_BACKEND": str(settings.CELERY_RESULT_BACKEND),
            "LOG_FILE_ENABLED": "False",
            "CELERY_TASK_ALWAYS_EAGER": "0",
        }
    )
    command = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "config",
        "worker",
        "--loglevel=INFO",
        "--concurrency=1",
        "--pool=solo",
        "-n",
        f"integ@{uuid.uuid4().hex[:8]}",
    ]
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_worker(process)
        yield process
    finally:
        _stop_process(process)


def _wait_for_worker(process: subprocess.Popen[bytes]) -> None:
    from config.celery import app

    deadline = time.monotonic() + WORKER_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = (
                process.stdout.read().decode("utf-8", errors="replace") if process.stdout else ""
            )
            raise RuntimeError(f"Celery worker exited early:\n{output}")
        try:
            replies = app.control.inspect(timeout=1.0).ping()
        except Exception:
            replies = None
        if replies:
            return
        time.sleep(0.5)
    _stop_process(process)
    raise RuntimeError("Celery worker did not become ready in time")


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@pytest.fixture
def mda_api_base_url(live_server: object, monkeypatch: pytest.MonkeyPatch) -> str:
    """Point the Streamlit client at the pytest-django live server."""

    url = str(live_server.url).rstrip("/")
    monkeypatch.setenv("MDA_API_BASE_URL", url)
    from streamlit_app.config import (
        api_base_url,
        market_cache_ttl_seconds,
        reference_cache_ttl_seconds,
        request_timeout_seconds,
    )

    api_base_url.cache_clear()
    request_timeout_seconds.cache_clear()
    reference_cache_ttl_seconds.cache_clear()
    market_cache_ttl_seconds.cache_clear()
    return url
