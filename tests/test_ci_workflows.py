"""CI installs explicit extras: the observer SDKs bundle ~300 MB binaries CI must never fetch."""

from pathlib import Path

import pytest
import yaml

_WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
_SUITE_SYNC = "uv sync --extra dev --extra mcp --extra ts"
# Every tree ruff must see. `plugin` is here because its stdlib scripts ship to users and were
# outside CI's scope long enough for an unformatted file to merge.
_LINT_PATHS = "auditor plugin auditr_observer.py tests"
_LINT = f"uv run ruff check {_LINT_PATHS}"
_FORMAT = f"uv run ruff format --check {_LINT_PATHS}"
_BANNED = (
    "--all-extras",
    "--extra observer",
    "--extra observer-claude",
    "--extra observer-codex",
    "--extra vectors",
    "[observer",
    "[vectors",
)


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
@pytest.mark.parametrize("banned", _BANNED)
def test_no_run_step_pulls_the_opt_in_extras(workflow: str, banned: str):
    """A multi-line ``run: |`` block hides its later lines from any prefix filter, so scan it all."""
    steps = _run_steps(workflow)
    assert steps
    for step in steps:
        assert banned not in step, step


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
def test_lint_covers_every_shipped_tree(workflow: str):
    assert _LINT in _run_steps(workflow)


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
def test_format_check_covers_every_shipped_tree(workflow: str):
    assert _FORMAT in _run_steps(workflow)
