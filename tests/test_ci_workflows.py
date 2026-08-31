"""CI installs explicit extras: the observer SDKs bundle ~300 MB binaries CI must never fetch."""

import re
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
_UI_STEPS = (
    "pnpm --dir auditor/graph/ui install --frozen-lockfile",
    "pnpm --dir auditor/graph/ui typecheck",
    "pnpm --dir auditor/graph/ui test",
    "pnpm --dir auditor/graph/ui build",
)
_BANNED = (
    "--all-extras",
    "--extra observer",
    "--extra observer-claude",
    "--extra observer-codex",
    "--extra vectors",
    "[observer",
    "[vectors",
)


def _jobs(workflow: str) -> dict:
    """Every job in one workflow. `_run_steps` walks the same document, so it calls this."""
    return yaml.safe_load((_WORKFLOWS / workflow).read_text())["jobs"]


def _run_steps(workflow: str) -> list[str]:
    return [
        step["run"]
        for job in _jobs(workflow).values()
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


def test_ci_has_a_ui_job():
    """Spec 20 row S10. There was no Node in CI at all, so nothing typechecked the page."""
    assert "ui" in _jobs("ci.yml")


@pytest.mark.parametrize("step", _UI_STEPS)
def test_the_ui_job_installs_frozen_then_typechecks_builds_and_tests(step: str):
    """`vite` and `typescript` float on `^`; only a frozen install makes the build reproducible."""
    assert step in [s["run"] for s in _jobs("ci.yml")["ui"]["steps"] if "run" in s]


def test_the_ui_job_fails_when_the_committed_bundle_is_not_what_a_rebuild_produces():
    """The only check that compares the shipped artifact to source rather than to a digest."""
    steps = [s["run"] for s in _jobs("ci.yml")["ui"]["steps"] if "run" in s]
    assert "git diff --exit-code -- auditor/graph/ui/dist/index.html" in steps


def test_the_ui_job_pins_the_package_manager_through_the_repo_s_own_package_manager_pin():
    """`pnpm/action-setup` reads `packageManager`, and this repo has no root `package.json`.

    Without `package_json_file` the action fails before any pnpm step runs (P22).
    """
    steps = _jobs("ci.yml")["ui"]["steps"]
    assert any(s.get("uses", "").startswith("actions/setup-node@") for s in steps)
    setup = next(s for s in steps if s.get("uses", "").startswith("pnpm/action-setup@"))
    assert setup["with"]["package_json_file"] == "auditor/graph/ui/package.json"


def test_the_ui_job_never_reaches_for_npm_or_yarn():
    """The repo is pnpm only; an `npm i` here would resolve outside the lockfile.

    Word-bounded on purpose: a substring check calls every `pnpm` line an `npm` one.
    """
    steps = [s["run"] for s in _jobs("ci.yml")["ui"]["steps"] if "run" in s]
    for step in steps:
        assert not re.search(r"\b(npm|npx|yarn|bun)\b", step), step
