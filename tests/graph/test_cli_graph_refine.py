"""`auditr graph refine`: the runner choice, the exit codes, and what the run leaves behind."""

import asyncio
import json
from pathlib import Path

import pytest
from _support import assert_no_escape, cli_json, invoke, one_line
from graph._support import FailingClaude, render_text

from auditor.cli.helpers import load_settings, load_user, open_index
from auditor.cli.render import render_graph_refine
from auditor.graph.refine import drive
from auditor.graph.refine.models import (
    RunnerKind,
)
from auditor.graph.refine.payloads import RefinePayload
from auditor.graph.refine.service import RefinementService


def _refine(repo: Path, scope: str = ".", *args: str):
    """`scope` is the first positional and `target` the second, as every graph command reads."""
    return invoke("graph", "refine", scope, str(repo), *args, "--json")


def test_a_run_commits_and_reports_what_it_landed(refine_repo, claude_runner):
    payload = cli_json(_refine(refine_repo, ""))
    assert payload["run"]["status"] == "succeeded"
    assert payload["choice"] == "claude"
    assert payload["run"]["runner"] == "claude"
    assert payload["targets"] >= 1
    assert [v["outcome"] for v in payload["committed"]] == ["staged"]
    assert payload["build"] is not None


def test_the_log_row_carries_the_three_invariant_two_columns(
    refine_repo, claude_runner
):
    """The verbatim brief stays on the row; the log carries its shape (H6)."""
    _refine(refine_repo, "")
    log = cli_json(invoke("graph", "log", str(refine_repo), "--json"))
    (row,) = log["runs"]
    assert row["system_prompt_sha"] and len(row["system_prompt_sha"]) == 64
    assert row["prompt_chars"] > 0
    assert row["tool_calls"] >= 1
    assert row["num_turns"] >= 1


def test_the_correction_lands_pending(refine_repo, claude_runner):
    _refine(refine_repo, "")
    listed = cli_json(
        invoke("graph", "refinements", "list", str(refine_repo), "--json")
    )
    assert [row["status"] for row in listed["rows"]] == ["pending"]


def test_no_scope_briefs_the_whole_repo(refine_repo, claude_runner):
    payload = cli_json(invoke("graph", "refine", "", str(refine_repo), "--json"))
    assert payload["scope"] == ""
    assert payload["run"]["status"] == "succeeded"


def test_a_dot_scope_briefs_the_whole_repo_too(refine_repo):
    """`scope_path` normalises `.`, so the default and the value a user types agree."""
    whole = one_line(invoke("graph", "refine", "", str(refine_repo), "--brief").stdout)
    dotted = one_line(
        invoke("graph", "refine", ".", str(refine_repo), "--brief").stdout
    )
    assert "scope: (the whole repo)" in whole
    assert "caller.py::main" in whole
    assert dotted == whole


def test_brief_opens_no_run(refine_repo):
    result = invoke("graph", "refine", ".", str(refine_repo), "--brief")
    assert result.exit_code == 0, result.output
    assert "Refinement brief" in result.stdout
    assert cli_json(invoke("graph", "log", str(refine_repo), "--json"))["runs"] == []


async def _built(repo: Path, scope: str) -> str:
    """The brief a preview builds for one scope, outside the CLI."""
    settings, user = load_settings(repo), load_user(repo)
    async with await open_index(repo) as index:
        service = RefinementService(index, repo, settings, user)
        return (await service.preview(scope)).render()


def test_the_brief_the_cli_prints_is_the_one_the_builder_builds(refine_repo):
    """Sync on purpose: the CLI calls `asyncio.run`, which no running loop allows."""
    payload = cli_json(
        invoke("graph", "refine", "caller.py", str(refine_repo), "--brief", "--json")
    )
    assert payload["prompt"] == asyncio.run(_built(refine_repo, "caller.py"))
    assert payload["run_id"] is None


def test_a_codex_runner_is_refused_at_exit_one(refine_repo, claude_runner):
    result = _refine(refine_repo, ".", "--runner", "codex")
    assert result.exit_code == 1
    assert "S12" in result.output
    assert cli_json(invoke("graph", "log", str(refine_repo), "--json"))["runs"] == []


@pytest.mark.parametrize(
    ("option", "value", "named"),
    [
        ("--runner", "other", "auto, claude, codex"),
        ("--model", "opus", "haiku, sonnet"),
    ],
)
def test_an_unknown_option_value_is_exit_two(refine_repo, option, value, named):
    """The job refuses it, so no run row exists to be left open by the refusal."""
    result = _refine(refine_repo, ".", option, value)
    assert result.exit_code == 2
    assert named in one_line(result.output)
    assert cli_json(invoke("graph", "log", str(refine_repo), "--json"))["runs"] == []


@pytest.mark.parametrize("option", ["--runner", "--model"])
def test_brief_refuses_the_options_it_would_ignore(refine_repo, option):
    """Two flags that are individually valid and jointly meaningless: `--brief` opens no run."""
    value = "claude" if option == "--runner" else "haiku"
    result = invoke("graph", "refine", ".", str(refine_repo), "--brief", option, value)
    assert result.exit_code == 2
    assert "opens no run" in one_line(result.output)


def test_without_the_extra_the_refusal_names_it_and_prints_no_json(
    refine_repo, monkeypatch
):
    monkeypatch.setattr(drive, "SDK_AVAILABLE", False)
    result = _refine(refine_repo)
    assert result.exit_code == 1
    assert "auditr[observer-claude]" in one_line(result.output)
    assert result.stdout.strip() == ""


def test_without_credentials_the_refusal_says_how_to_log_in(refine_repo, monkeypatch):
    monkeypatch.setattr(drive, "SDK_AVAILABLE", True)
    monkeypatch.setattr(drive, "auth_hinted", lambda *a, **k: False)
    result = _refine(refine_repo)
    assert result.exit_code == 1
    assert "log in" in one_line(result.output)


def test_a_run_that_did_not_succeed_still_prints_its_payload(
    refine_repo, claude_runner
):
    """Exit 1, and the JSON too: a caller has to be able to see why (M8)."""
    claude_runner.setitem(drive.RUNNERS, RunnerKind.CLAUDE, FailingClaude)
    result = _refine(refine_repo)
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["run"]["status"] == "aborted"
    assert payload["run"]["error"] == "the model gave up"
    log = cli_json(invoke("graph", "log", str(refine_repo), "--json"))
    assert [row["status"] for row in log["runs"]] == ["aborted"]


def test_a_scope_outside_the_checkout_is_refused(refine_repo, claude_runner):
    result = _refine(refine_repo, "../elsewhere")
    assert result.exit_code == 1
    assert "not a repo-relative path" in one_line(result.output)
    assert_no_escape(result)


def test_the_human_render_names_the_cost_and_the_pending_step(
    refine_repo, claude_runner
):
    payload = cli_json(_refine(refine_repo, ""))
    text = one_line(
        render_text(render_graph_refine, RefinePayload.model_validate(payload))
    )
    assert "graph refinements accept" in text
    assert "$0.0000" in text
    assert "queue rows" in text


def test_a_scope_refuses_a_correction_that_reaches_outside_it(
    refine_repo, claude_runner
):
    """`StagedRun.covers` wants every id under the scope, so a cross-file edge needs a wider run."""
    payload = cli_json(_refine(refine_repo, "caller.py"))
    assert payload["committed"] == []
    assert payload["run"]["refinements"] == {"committed": 0, "rejected": 1}
    assert payload["run"]["status"] == "succeeded"


def test_a_dot_slash_scope_is_the_directory_under_it(refine_repo, claude_runner):
    """`./caller.py` is what shell completion produces; it used to brief nothing, refuse every
    proposal and still exit 0."""
    payload = cli_json(_refine(refine_repo, "./caller.py"))
    assert payload["scope"] == "caller.py"
    assert payload["targets"] >= 1
