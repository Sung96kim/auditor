"""CI installs explicit extras: the observer SDKs bundle ~300 MB binaries CI must never fetch."""

from pathlib import Path

import pytest
import yaml

_WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
_SUITE_SYNC = "uv sync --extra dev --extra mcp --extra graph --extra ts"
_LINT = "uv run ruff check auditor auditr_observer.py tests"
_FORMAT = "uv run ruff format --check auditor auditr_observer.py tests"


def _run_steps(workflow: str) -> list[str]:
    document = yaml.safe_load((_WORKFLOWS / workflow).read_text())
    return [
        step["run"]
        for job in document["jobs"].values()
        for step in job["steps"]
        if "run" in step
    ]


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
def test_suite_job_syncs_explicit_extras(workflow: str):
    assert _SUITE_SYNC in _run_steps(workflow)


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
@pytest.mark.parametrize("banned", ["--all-extras", "observer", "vectors"])
def test_no_sync_pulls_the_opt_in_extras(workflow: str, banned: str):
    syncs = [step for step in _run_steps(workflow) if step.startswith("uv sync")]
    assert syncs
    for step in syncs:
        assert banned not in step, step


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
def test_lint_covers_the_top_level_observer_client(workflow: str):
    assert _LINT in _run_steps(workflow)


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
def test_format_check_covers_the_top_level_observer_client(workflow: str):
    assert _FORMAT in _run_steps(workflow)
