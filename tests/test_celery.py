"""Celery application configuration tests."""

from __future__ import annotations

from config.celery import app


def test_celery_application_is_configured() -> None:
    assert app.main == "market_data_analysis"
    assert app.conf.task_serializer == "json"
    assert app.conf.result_serializer == "json"
    assert app.conf.accept_content == ["json"]
    assert app.conf.timezone == "UTC"
    assert app.conf.enable_utc is True


def test_celery_uses_test_broker_and_result_backend() -> None:
    assert app.conf.broker_url == "memory://"
    assert app.conf.result_backend == "cache+memory://"


def test_celery_worker_keeps_django_logging_configuration() -> None:
    assert app.conf.worker_hijack_root_logger is False
