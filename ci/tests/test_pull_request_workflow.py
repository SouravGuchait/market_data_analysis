"""Contract tests for the pull-request CI workflow."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
REQUIRED_JOBS = (
    "quality",
    "security",
    "django",
    "tests",
    "streamlit",
    "integration",
    "docker",
)
FORBIDDEN = (
    "aws-actions/configure-aws-credentials",
    "amazon-ecr",
    "docker push",
    "ecs deploy",
    "id-token: write",
    "secrets.",
)


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _gate() -> str:
    return _text().split("\n  ci:", maxsplit=1)[1]


def test_pull_request_workflow_exists() -> None:
    assert WORKFLOW.is_file()


def test_workflow_runs_on_pull_requests_with_read_only_permissions() -> None:
    text = _text()
    assert "pull_request:" in text
    assert "contents: read" in text


def test_pull_request_workflow_has_no_deployment() -> None:
    text = _text()
    for token in FORBIDDEN:
        assert token not in text


def test_pull_request_workflow_declares_required_jobs() -> None:
    text = _text()
    for job in REQUIRED_JOBS:
        assert f"\n  {job}:" in text


def test_ci_gate_requires_every_job() -> None:
    gate = _gate()
    assert "if: always() && !cancelled()" in gate
    for job in REQUIRED_JOBS:
        assert f"- {job}" in gate
        assert f"needs.{job}.result" in gate


def test_dev_compose_uses_development_settings() -> None:
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "config.settings.development" in text


def test_messaging_script_uses_ssm_send_command() -> None:
    text = (ROOT / "scripts" / "ec2_messaging.sh").read_text(encoding="utf-8")
    assert "aws ssm send-command" in text
    assert "start-session" not in text
    assert "aws rds start-db-instance" in text
    assert "aws ec2 start-instances" in text
    assert "aws ecs update-service" in text
    assert "market-data-web" in text
    assert "market-data-celery" in text


def test_integration_job_runs_features_on_mysql() -> None:
    integration = _text().split("\n  integration:", maxsplit=1)[1].split("\n  docker:", maxsplit=1)[0]
    assert "config.settings.integration" in integration
    assert "\n            apps \\" in integration
    assert "tests/integration" in integration


def test_ec2_compose_is_messaging_only() -> None:
    text = (ROOT / "docker-compose.ec2.yml").read_text(encoding="utf-8")
    assert "redis:7-alpine" in text
    assert "rabbitmq:3.13-management" in text
    assert "RABBITMQ_DEFAULT_VHOST: mda" in text
    assert "web:" not in text
    assert "streamlit:" not in text


def test_prod_compose_has_no_redis_or_broker() -> None:
    text = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "config.settings.production" in text
    assert "CACHE_URL" not in text
    assert "CELERY_BROKER_URL" not in text
    assert "\n  redis:" not in text
    assert "\n  rabbitmq:" not in text
    assert "\n  worker:" not in text


def test_production_settings_load_env_before_shared_settings() -> None:
    text = (ROOT / "config" / "settings" / "production.py").read_text(encoding="utf-8")
    assert text.index("load_dotenv(") < text.index("from .base import")


def test_workflow_references_every_app_test_module() -> None:
    text = _text()
    missing = [
        path.relative_to(ROOT).as_posix()
        for path in sorted((ROOT / "apps").rglob("test_*.py"))
        if path.relative_to(ROOT).as_posix() not in text
    ]
    assert missing == []
