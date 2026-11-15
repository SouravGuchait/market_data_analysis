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


def test_workflow_references_every_app_test_module() -> None:
    text = _text()
    missing = [
        path.relative_to(ROOT).as_posix()
        for path in sorted((ROOT / "apps").rglob("test_*.py"))
        if path.relative_to(ROOT).as_posix() not in text
    ]
    assert missing == []
