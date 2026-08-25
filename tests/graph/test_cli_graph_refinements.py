"""`auditr graph refinements` — the five subcommands spec 12.2 names."""

import asyncio
import io
import time
from pathlib import Path

import pytest
from _support import cli_json
from fastmcp import Client
from rich.console import Console
from typer.testing import CliRunner

from auditor.cli import app
from auditor.cli.render import render_graph_refinement, render_graph_refinements
from auditor.database import open_repo_index
from auditor.graph.model import EdgeKind
from auditor.graph.payloads import RefinementRowPayload, RefinementsReport
from auditor.graph.refine.models import (
    ProducerKind,
    RefinementKind,
    RefinementStatus,
    Run,
    RunnerKind,
    RunStatus,
    Tier,
    TriggerKind,
)
from auditor.graph.refine.service import RunRegistry
from auditor.mcp import mcp

runner = CliRunner()

HELPER = "def read_event():\n    return {}\n"
CALLER = "def main():\n    return read_event()\n"

GOOD = {
    "kind": "add_edge",
    "src": "caller.py::main",
    "dst": "helper.py::read_event",
    "edge_kind": "calls",
    "name": "read_event",
    "reason": "main calls read_event, which helper.py defines",
}
BAD = GOOD | {"name": "get_user", "reason": "a name main does not call"}


@pytest.fixture
def refined_repo(graph_repo: Path, process_runs: RunRegistry) -> Path:
    """The one-module repo plus a bare call the resolver cannot place, built once."""
    (graph_repo / "helper.py").write_text(HELPER)
    (graph_repo / "caller.py").write_text(CALLER)
    assert runner.invoke(app, ["graph", "build", str(graph_repo)]).exit_code == 0
    return graph_repo


def _data(result):
    return result.data if hasattr(result, "data") else result


def _commit(repo: Path, *proposals: dict) -> list[dict]:
    """Drive one run through the MCP tools and answer the commit's committed verdicts.

    The tools are the public producer, so the rows these tests read were written the way a real
    agent writes them.
    """

    async def go() -> list[dict]:
        async with Client(mcp) as client:
            begun = await client.call_tool("graph_refine_begin", {"path": str(repo)})
            run_id = _data(begun)["run_id"]
            for proposal in proposals:
                await client.call_tool(
                    "graph_refine_propose",
                    {"path": str(repo), "run_id": run_id, **proposal},
                )
            done = await client.call_tool(
                "graph_refine_commit", {"path": str(repo), "run_id": run_id}
            )
            return _data(done)["committed"]

    return asyncio.run(go())


def _propose(repo: Path) -> int:
    """One committed `add_edge` for the queue row, and its id."""
    return _commit(repo, GOOD)[0]["refinement_id"]


def _runs(repo: Path, *, skipped: bool) -> list[dict]:
    """The run log through the `graph_log` MCP tool. `auditr graph log` is the next branch's."""

    async def go() -> list[dict]:
        async with Client(mcp) as client:
            page = await client.call_tool(
                "graph_log", {"path": str(repo), "skipped": skipped}
            )
            return _data(page)["runs"]

    return asyncio.run(go())


def _add_skipped_run(repo: Path, *, age_days: int) -> None:
    """One assessment-only run, written directly: nothing produces a `skipped` run before S8."""

    async def go() -> None:
        index = await open_repo_index(repo)
        try:
            await index.runs.add_run(
                Run(
                    repo_identity=index.partition.identity,
                    producer=ProducerKind.OBSERVER,
                    runner=RunnerKind.NONE,
                    trigger_kind=TriggerKind.EDIT,
                    status=RunStatus.SKIPPED,
                    summary="no structural change",
                    started_at=time.time() - age_days * 86400,
                )
            )
        finally:
            await index.aclose()

    asyncio.run(go())


def test_an_empty_list_says_it_is_empty_not_filtered(refined_repo: Path):
    payload = cli_json(
        runner.invoke(
            app, ["graph", "refinements", "list", str(refined_repo), "--json"]
        )
    )
    assert payload == {"rows": [], "filtered": False}


def test_a_status_filter_narrows_to_the_rows_that_match(refined_repo: Path):
    """Two rows, one of each status, so `filtered` distinguishes "nothing matched" from "nothing
    recorded" against a list that is not empty either way."""
    committed = _commit(refined_repo, GOOD, BAD)
    pending = cli_json(
        runner.invoke(
            app,
            [
                "graph",
                "refinements",
                "list",
                str(refined_repo),
                "--status",
                "pending",
                "--json",
            ],
        )
    )
    assert [r["refinement_id"] for r in pending["rows"]] == [
        committed[0]["refinement_id"]
    ]
    assert pending["filtered"] is True
    rejected = cli_json(
        runner.invoke(
            app,
            [
                "graph",
                "refinements",
                "list",
                str(refined_repo),
                "--status",
                "rejected",
                "--json",
            ],
        )
    )
    assert [r["status"] for r in rejected["rows"]] == ["rejected"]
    none_active = cli_json(
        runner.invoke(
            app,
            [
                "graph",
                "refinements",
                "list",
                str(refined_repo),
                "--status",
                "active",
                "--json",
            ],
        )
    )
    assert none_active == {"rows": [], "filtered": True}


def test_an_unknown_status_is_refused_with_the_valid_set(refined_repo: Path):
    result = runner.invoke(
        app, ["graph", "refinements", "list", str(refined_repo), "--status", "nope"]
    )
    assert result.exit_code != 0
    assert "pending" in result.output


def test_list_shows_the_committed_correction(refined_repo: Path):
    rid = _propose(refined_repo)
    payload = cli_json(
        runner.invoke(
            app, ["graph", "refinements", "list", str(refined_repo), "--json"]
        )
    )
    assert [r["refinement_id"] for r in payload["rows"]] == [rid]
    row = payload["rows"][0]
    assert row["status"] == "pending"
    assert row["tier"] == "B"
    assert row["src"] == "caller.py::main"
    assert set(row["anchors"]) == {"caller.py::main", "helper.py::read_event"}


def test_the_limit_caps_the_rows(refined_repo: Path):
    _commit(refined_repo, GOOD, BAD)
    payload = cli_json(
        runner.invoke(
            app,
            [
                "graph",
                "refinements",
                "list",
                str(refined_repo),
                "--limit",
                "1",
                "--json",
            ],
        )
    )
    assert len(payload["rows"]) == 1


def test_accept_activates_and_the_next_build_applies_it(refined_repo: Path):
    rid = _propose(refined_repo)
    accepted = cli_json(
        runner.invoke(
            app,
            ["graph", "refinements", "accept", str(rid), str(refined_repo), "--json"],
        )
    )
    assert accepted["status"] == "active"
    built = cli_json(
        runner.invoke(app, ["graph", "build", str(refined_repo), "--no-scan", "--json"])
    )
    assert built["refined"] == 1


@pytest.mark.parametrize(
    ("command", "expected"), [("revert", "reverted"), ("pin", "pinned")]
)
def test_the_other_transitions(refined_repo: Path, command: str, expected: str):
    rid = _propose(refined_repo)
    payload = cli_json(
        runner.invoke(
            app,
            ["graph", "refinements", command, str(rid), str(refined_repo), "--json"],
        )
    )
    assert payload["status"] == expected


def test_an_illegal_transition_names_the_current_status(refined_repo: Path):
    rid = _propose(refined_repo)
    assert (
        runner.invoke(
            app, ["graph", "refinements", "revert", str(rid), str(refined_repo)]
        ).exit_code
        == 0
    )
    result = runner.invoke(
        app, ["graph", "refinements", "accept", str(rid), str(refined_repo)]
    )
    assert result.exit_code != 0
    assert "reverted" in result.output


def test_an_unknown_id_is_named(refined_repo: Path):
    result = runner.invoke(
        app, ["graph", "refinements", "accept", "4242", str(refined_repo)]
    )
    assert result.exit_code != 0
    assert "4242" in result.output


def test_prune_drops_only_the_assessment_runs_past_the_window(refined_repo: Path):
    _add_skipped_run(refined_repo, age_days=30)
    _add_skipped_run(refined_repo, age_days=0)
    _propose(refined_repo)  # a real run, which prune must never touch
    payload = cli_json(
        runner.invoke(
            app, ["graph", "refinements", "prune", str(refined_repo), "--json"]
        )
    )
    assert payload == {"removed": 1}
    assert sorted(r["status"] for r in _runs(refined_repo, skipped=True)) == [
        "skipped",
        "succeeded",
    ]


def _render(payload) -> str:
    buf = io.StringIO()
    console = Console(file=buf, width=120)
    if isinstance(payload, RefinementsReport):
        render_graph_refinements(console, payload)
    else:
        render_graph_refinement(console, payload)
    return buf.getvalue()


def test_the_empty_renderer_distinguishes_no_rows_from_no_matches():
    assert "none recorded" in _render(RefinementsReport())
    assert "matched" in _render(RefinementsReport(filtered=True))


def test_the_row_renderer_shows_the_edge_the_tier_and_the_reason():
    """A real node id is 70-odd characters. Five columns keep it on one line at width 120; the
    reason rides the continuation row rather than stealing half the table."""
    row = RefinementRowPayload(
        refinement_id=3,
        run_id="r1",
        kind=RefinementKind.ADD_EDGE,
        tier=Tier.B,
        status=RefinementStatus.PENDING,
        src="plugin/hooks/audit_edit.py::main",
        dst="plugin/hooks/_common.py::read_event",
        edge_kind=EdgeKind.CALLS,
        name="read_event",
        reason="main calls read_event, imported from the sibling _common",
    )
    out = _render(RefinementsReport(rows=(row,)))
    assert (
        "plugin/hooks/audit_edit.py::main -> plugin/hooks/_common.py::read_event" in out
    )
    assert "pending" in out
    assert "sibling _common" in out
