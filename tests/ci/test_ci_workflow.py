"""Lock-in guard for the CI pipeline (bd-cpo.1).

These tests assert that `.github/workflows/ci.yml` encodes the dual-dialect
guarantee E2 built: pytest runs on push/PR against BOTH sqlite (default path)
and postgres (via PIXELPIVOT_DB_URL). Deleting the postgres leg — or the
workflow entirely — fails the build, so a dialect regression cannot slip in
unnoticed.
"""
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _load() -> dict:
    assert WORKFLOW.exists(), f"CI workflow missing: {WORKFLOW}"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers(wf: dict):
    # PyYAML 1.1 parses a bare `on:` key as the boolean True, not the string
    # "on" — accept either so the assertion survives a round-trip.
    return wf.get("on", wf.get(True))


def test_ci_workflow_file_exists():
    assert WORKFLOW.exists(), f"expected CI workflow at {WORKFLOW}"


def test_ci_triggers_on_push_and_pull_request():
    triggers = _triggers(_load())
    assert triggers is not None, "workflow has no `on:` triggers"
    assert "push" in triggers, "CI must run on push"
    assert "pull_request" in triggers, "CI must run on pull_request"


def test_ci_runs_pytest_on_postgres_dialect():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "postgresql+psycopg://" in text, (
        "CI must run the suite against postgres via PIXELPIVOT_DB_URL"
    )
    assert "PIXELPIVOT_DB_URL" in text


def test_ci_declares_a_postgres_service():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "postgres:" in text, "CI must spin up a postgres service container"


def test_ci_invokes_pytest():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pytest" in text, "CI must invoke pytest"
