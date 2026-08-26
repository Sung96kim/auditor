"""`auditr graph refine`: the runner choice, the exit codes, and what the run leaves behind."""

import asyncio
import json
from pathlib import Path

import pytest
from _support import assert_no_escape, cli_json, invoke, one_line
from graph._support import render_text

from auditor.cli.helpers import load_settings, load_user, open_index
from auditor.cli.render import render_graph_refine
from auditor.graph.model import EdgeKind
from auditor.graph.refine import drive
from auditor.graph.refine.brief import BriefBuilder
from auditor.graph.refine.models import (
    Proposal,
    RefinementKind,
    RefinementTarget,
    RunnerKind,
)
from auditor.graph.refine.payloads import RefinePayload
from auditor.graph.refine.runner import FakeRunner
from auditor.graph.refine.service import RefinementService

#: the one true correction on the `refine_repo` pair, in the nested shape `propose` takes
GOOD = Proposal(
    kind=RefinementKind.ADD_EDGE,
    target=RefinementTarget(
        src="caller.py::main",
        dst="helper.py::read_event",
        edge_kind=EdgeKind.CALLS,
        name="read_event",
    ),
    reason="main calls read_event, which helper.py defines",
).model_dump()


class ClaudeShaped(FakeRunner):
    """A fake that reports itself as the Claude runner, so the choice logic runs unchanged."""

    kind = RunnerKind.CLAUDE
    script: tuple = (GOOD,)
    stops: str | None = None

    def __init__(self, service, client_factory=None, **kwargs):
        super().__init__(
            service, client_factory, script=self.script, stop=self.stops, **kwargs
        )


class Failing(ClaudeShaped):
    script = ()
    stops = "the model gave up"


@pytest.fixture
def claude(monkeypatch):
    """A logged-in machine with the extra installed, driving the fake runner."""
    monkeypatch.setattr(drive, "SDK_AVAILABLE", True)
    monkeypatch.setattr(drive, "auth_hinted", lambda *a, **k: True)
    monkeypatch.setitem(drive.RUNNERS, RunnerKind.CLAUDE, ClaudeShaped)
    return monkeypatch


def _refine(repo: Path, scope: str = ".", *args: str):
    """`scope` is the first positional and `target` the second, as every graph command reads."""
    return invoke("graph", "refine", scope, str(repo), *args, "--json")


def test_a_run_commits_and_reports_what_it_landed(refine_repo, claude):
    payload = cli_json(_refine(refine_repo, ""))
    assert payload["run"]["status"] == "succeeded"
    assert payload["choice"] == "claude"
    assert payload["runner"] == "claude"
    assert payload["targets"] >= 1
    assert [v["outcome"] for v in payload["committed"]] == ["staged"]
    assert payload["build"] is not None


def test_the_log_row_carries_the_three_invariant_two_columns(refine_repo, claude):
    """The verbatim brief stays on the row; the log carries its shape (H6)."""
    _refine(refine_repo, "")
    log = cli_json(invoke("graph", "log", str(refine_repo), "--json"))
    (row,) = log["runs"]
    assert row["system_prompt_sha"] and len(row["system_prompt_sha"]) == 64
    assert row["prompt_chars"] > 0
    assert row["tool_calls"] >= 1
    assert row["num_turns"] >= 1


def test_the_correction_lands_pending(refine_repo, claude):
    _refine(refine_repo, "")
    listed = cli_json(
        invoke("graph", "refinements", "list", str(refine_repo), "--json")
    )
    assert [row["status"] for row in listed["rows"]] == ["pending"]


def test_no_scope_briefs_the_whole_repo(refine_repo, claude):
    payload = cli_json(invoke("graph", "refine", "", str(refine_repo), "--json"))
    assert payload["scope"] == ""
    assert payload["run"]["status"] == "succeeded"


def test_a_dot_scope_briefs_the_whole_repo_too(refine_repo):
    """`scope_path` normalises `.`, so the default and the value a user types agree."""
    whole = invoke("graph", "refine", "", str(refine_repo), "--brief").stdout
    dotted = invoke("graph", "refine", ".", str(refine_repo), "--brief").stdout
    assert one_line(dotted) == one_line(whole)


def test_brief_opens_no_run(refine_repo):
    result = invoke("graph", "refine", ".", str(refine_repo), "--brief")
    assert result.exit_code == 0, result.output
    assert "Refinement brief" in result.stdout
    assert cli_json(invoke("graph", "log", str(refine_repo), "--json"))["runs"] == []


async def _built(repo: Path, scope: str) -> str:
    """The brief the builder builds for one scope, outside the CLI."""
    settings, user = load_settings(repo), load_user(repo)
    async with await open_index(repo) as index:
        service = RefinementService(index, repo, settings, user)
        brief = await BriefBuilder(
            facts=service.facts, limits=user.observer.limits
        ).build(scope, commit_sha=(await service.head())[1])
    return brief.render()


def test_the_brief_the_cli_prints_is_the_one_the_builder_builds(refine_repo):
    """Sync on purpose: the CLI calls `asyncio.run`, which no running loop allows."""
    payload = cli_json(
        invoke("graph", "refine", "caller.py", str(refine_repo), "--brief", "--json")
    )
    assert payload["prompt"] == asyncio.run(_built(refine_repo, "caller.py"))
    assert payload["run_id"] is None


def test_a_codex_runner_is_refused_at_exit_one(refine_repo, claude):
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
    result = _refine(refine_repo, ".", option, value)
    assert result.exit_code == 2
    assert named in one_line(result.output)


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


def test_a_run_that_did_not_succeed_still_prints_its_payload(refine_repo, claude):
    """Exit 1, and the JSON too: a caller has to be able to see why (M8)."""
    claude.setitem(drive.RUNNERS, RunnerKind.CLAUDE, Failing)
    result = _refine(refine_repo)
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["run"]["status"] == "aborted"
    assert payload["run"]["error"] == "the model gave up"
    log = cli_json(invoke("graph", "log", str(refine_repo), "--json"))
    assert [row["status"] for row in log["runs"]] == ["aborted"]


def test_a_scope_outside_the_checkout_is_refused(refine_repo, claude):
    result = _refine(refine_repo, "../elsewhere")
    assert result.exit_code == 1
    assert "not a repo-relative path" in one_line(result.output)
    assert_no_escape(result)


def test_the_human_render_names_the_cost_and_the_pending_step(refine_repo, claude):
    payload = cli_json(_refine(refine_repo, ""))
    text = one_line(
        render_text(render_graph_refine, RefinePayload.model_validate(payload))
    )
    assert "graph refinements accept" in text
    assert "$0.0000" in text
    assert "queue rows" in text


def test_a_scope_refuses_a_correction_that_reaches_outside_it(refine_repo, claude):
    """`StagedRun.covers` wants every id under the scope, so a cross-file edge needs a wider run."""
    payload = cli_json(_refine(refine_repo, "caller.py"))
    assert payload["committed"] == []
    assert payload["run"]["refinements"] == {"committed": 0, "rejected": 1}
    assert payload["run"]["status"] == "succeeded"
