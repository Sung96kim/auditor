"""CI installs explicit extras: the observer SDKs bundle ~300 MB binaries CI must never fetch."""

import re
from pathlib import Path

import pytest
import yaml

_WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
_SUITE_SYNC = "uv sync --extra dev --extra mcp --extra ts"
# Every tree ruff must see. `plugin` is here because its stdlib scripts ship to users and were
# outside CI's scope long enough for an unformatted file to merge; `scripts` for the same reason,
# since `build_codex_plugin.py` is what `release.yml` runs.
_LINT_PATHS = "auditor plugin auditr_observer.py tests scripts"
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
#: the one job allowed an opt-in extra, and the one file it buys. Everything else stays on the
#: explicit three, because the observer SDKs bundle ~640MB of CLI binaries.
_CODEX_JOB = "codex-shapes"
_CODEX_SYNC = "uv sync --extra dev --extra mcp --extra observer-codex"
_CODEX_TEST = "uv run pytest -q tests/graph/test_codex_client.py"
_MIRROR_CHECK = "uv run python scripts/build_codex_plugin.py --check"


def _jobs(workflow: str) -> dict:
    """Every job in one workflow. `_run_steps` walks the same document, so it calls this."""
    return yaml.safe_load((_WORKFLOWS / workflow).read_text())["jobs"]


def _run_steps(workflow: str, job: str | None = None) -> list[str]:
    """Every `run:` line in a workflow, or in one of its jobs."""
    jobs = _jobs(workflow)
    chosen = [jobs[job]] if job is not None else list(jobs.values())
    return [step["run"] for one in chosen for step in one["steps"] if "run" in step]


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
def test_suite_job_syncs_explicit_extras(workflow: str):
    assert _SUITE_SYNC in _run_steps(workflow)


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
@pytest.mark.parametrize("banned", _BANNED)
def test_no_run_step_pulls_the_opt_in_extras(workflow: str, banned: str):
    """A multi-line ``run: |`` block hides its later lines from any prefix filter, so scan it all.

    `codex-shapes` is exempt by name: it is the one job that exists to install the Codex extra,
    and its own tests below pin how narrow it stays.
    """
    jobs = {name: job for name, job in _jobs(workflow).items() if name != _CODEX_JOB}
    steps = [
        step["run"] for job in jobs.values() for step in job["steps"] if "run" in step
    ]
    assert steps
    for step in steps:
        assert banned not in step, step


def test_one_ci_job_installs_the_codex_extra_so_the_real_sdk_shapes_are_pinned():
    """`test_codex_client.py` `importorskip`s the extra, so without this leg it never runs."""
    assert _CODEX_SYNC in _run_steps("ci.yml", _CODEX_JOB)


def test_the_codex_job_runs_that_one_file_and_nothing_else():
    """The extra is ~640MB; a whole second suite run on top of it buys nothing."""
    assert [
        step for step in _run_steps("ci.yml", _CODEX_JOB) if step.startswith("uv run")
    ] == [_CODEX_TEST]


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
def test_the_codex_mirror_is_checked_and_never_rewritten_in_a_workflow(workflow: str):
    """A write step before pytest disarms the drift test and rides into `cz bump`'s `commit -a`."""
    steps = _run_steps(workflow)
    assert not any(
        "build_codex_plugin.py" in step and "--check" not in step for step in steps
    ), steps


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
def test_the_gate_checks_the_mirror_before_it_runs_the_suite(workflow: str):
    steps = _run_steps(workflow)
    assert _MIRROR_CHECK in steps
    assert steps.index(_MIRROR_CHECK) < steps.index("uv run pytest -q")


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
    """The only check that compares the shipped artifact to source rather than to a digest.

    Scoped to the directory rather than to `index.html`: the narrower diff exited 0 while the
    build deleted the committed `dist/inputs.sha256` next to it.
    """
    steps = [s["run"] for s in _jobs("ci.yml")["ui"]["steps"] if "run" in s]
    assert "git diff --exit-code -- auditor/graph/ui/dist/" in steps


def test_the_ui_job_runs_its_steps_in_the_one_order_that_works():
    """`pnpm/action-setup` has to precede `setup-node`, or `cache: pnpm` resolves no store."""
    steps = _jobs("ci.yml")["ui"]["steps"]
    used = [s["uses"].split("@")[0] for s in steps if "uses" in s]
    assert used == ["actions/checkout", "pnpm/action-setup", "actions/setup-node"]
    ran = [s["run"] for s in steps if "run" in s]
    assert ran == [*_UI_STEPS, "git diff --exit-code -- auditor/graph/ui/dist/"]


def test_the_ui_job_pins_the_node_major_and_the_pnpm_cache():
    """`engines.node` only warns, so `setup-node` is the actual pin the reproducible build needs."""
    setup = next(
        s
        for s in _jobs("ci.yml")["ui"]["steps"]
        if s.get("uses", "").startswith("actions/setup-node@")
    )
    assert setup["with"]["node-version"] == 22
    assert setup["with"]["cache"] == "pnpm"
    assert setup["with"]["cache-dependency-path"] == "auditor/graph/ui/pnpm-lock.yaml"


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
def test_every_job_runs_under_a_declared_token_scope(workflow: str):
    """A job with no `permissions` above it inherits whatever the repository default happens to be."""
    document = yaml.safe_load((_WORKFLOWS / workflow).read_text())
    top = document.get("permissions")
    for name, job in document["jobs"].items():
        assert top or job.get("permissions"), name


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

    Word-bounded on purpose: a substring check calls every `pnpm` line an `npm` one. Scoped to
    the `ui` job on purpose too: the `test` job installs the Claude CLI with npm because that is
    the only distribution it has, and that step is not this repo's own dependency resolution.
    """
    steps = [s["run"] for s in _jobs("ci.yml")["ui"]["steps"] if "run" in s]
    for step in steps:
        assert not re.search(r"\b(npm|npx|yarn|bun)\b", step), step
