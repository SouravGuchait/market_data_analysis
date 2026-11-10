"""Dependency check management command tests."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.common.health import DependencyStatus, HealthState, ReadinessResult
from apps.common.management.commands import check_dependencies


class _FakeConnection:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.released = False

    def ensure_connection(self, max_retries: int) -> None:
        assert max_retries == 1
        if self.error is not None:
            raise self.error

    def release(self) -> None:
        self.released = True


class _FakeCeleryApp:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    def connection_for_write(self) -> _FakeConnection:
        return self.connection


def _patch_checks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    database: HealthState = "ok",
    cache: HealthState = "ok",
    broker_error: Exception | None = None,
) -> _FakeConnection:
    checks = {
        "database": DependencyStatus(status=database),
        "cache": DependencyStatus(status=cache),
    }
    monkeypatch.setattr(
        check_dependencies,
        "run_readiness_checks",
        lambda: ReadinessResult(
            is_ready=database == "ok" and cache == "ok",
            checks=checks,
            errors={},
        ),
    )
    connection = _FakeConnection(error=broker_error)
    monkeypatch.setattr(
        check_dependencies,
        "celery_app",
        _FakeCeleryApp(connection),
    )
    return connection


def test_command_reports_all_dependencies_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _patch_checks(monkeypatch)
    stdout = StringIO()
    stderr = StringIO()

    call_command("check_dependencies", stdout=stdout, stderr=stderr)

    assert "database: ok" in stdout.getvalue()
    assert "cache: ok" in stdout.getvalue()
    assert "celery_broker: ok" in stdout.getvalue()
    assert stderr.getvalue() == ""
    assert connection.released is True


def test_command_fails_when_database_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_checks(monkeypatch, database="unavailable")
    stderr = StringIO()

    with pytest.raises(CommandError, match="database"):
        call_command("check_dependencies", stdout=StringIO(), stderr=stderr)

    assert "database: unavailable" in stderr.getvalue()


def test_command_fails_when_cache_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_checks(monkeypatch, cache="unavailable")
    stderr = StringIO()

    with pytest.raises(CommandError, match="cache"):
        call_command("check_dependencies", stdout=StringIO(), stderr=stderr)

    assert "cache: unavailable" in stderr.getvalue()


def test_command_fails_when_celery_broker_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _patch_checks(
        monkeypatch,
        broker_error=ConnectionError("broker down"),
    )
    stderr = StringIO()

    with pytest.raises(CommandError, match="celery_broker"):
        call_command("check_dependencies", stdout=StringIO(), stderr=stderr)

    assert "celery_broker: unavailable" in stderr.getvalue()
    assert connection.released is True


def test_command_does_not_leak_broker_exception_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_checks(
        monkeypatch,
        broker_error=ConnectionError("redis://user:secret@example.invalid/0"),
    )
    stderr = StringIO()

    with pytest.raises(CommandError):
        call_command("check_dependencies", stdout=StringIO(), stderr=stderr)

    assert "secret" not in stderr.getvalue()
    assert "ConnectionError" in stderr.getvalue()
