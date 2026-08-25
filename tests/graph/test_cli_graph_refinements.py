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
from auditor.graph.model import MAX_LOG_ROWS, EdgeKind
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
from auditor.paths import user_config_path

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
def refined_repo(graph_repo: Path, process_runs: dict[str, RunRegistry]) -> Path:
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


def _log(repo: Path, **kw) -> dict:
    """One page of the provenance log through the `graph_log` MCP tool. `auditr graph log` is the
    next branch's."""

    async def go() -> dict:
        async with Client(mcp) as client:
            return _data(await client.call_tool("graph_log", {"path": str(repo), **kw}))

    return asyncio.run(go())


def _runs(repo: Path, *, skipped: bool) -> list[dict]:
    """The runs half of that page."""
    return _log(repo, skipped=skipped)["runs"]


def _add_run(repo: Path, *, status: RunStatus, age_seconds: float) -> str:
    """One run row written directly and aged by hand, which is the only way a test can have a run
    older than a retention window. The assessment writes `skipped` rows in S8; eviction already
    does today."""

    async def go() -> str:
        index = await open_repo_index(repo)
        try:
            return await index.runs.add_run(
                Run(
                    repo_identity=index.partition.identity,
                    producer=ProducerKind.OBSERVER,
                    runner=RunnerKind.NONE,
                    trigger_kind=TriggerKind.EDIT,
                    status=status,
                    summary="no structural change",
                    started_at=time.time() - age_seconds,
                )
            )
        finally:
            await index.aclose()

    return asyncio.run(go())


def test_an_empty_list_says_it_is_empty_not_filtered(refined_repo: Path):
    payload = cli_json(
        runner.invoke(
            app, ["graph", "refinements", "list", str(refined_repo), "--json"]
        )
    )
    assert payload == {
        "rows": [],
        "filtered": False,
        "refinement_count": 0,
        "truncated": False,
    }


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
    assert none_active["rows"] == []
    assert (none_active["filtered"], none_active["refinement_count"]) == (True, 0)


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


def test_the_page_is_the_newest_rows_and_the_total_says_what_it_left(
    refined_repo: Path,
):
    """A page at the cap and a complete list looked the same, and the page was the *oldest* rows:
    the `pending` row a human has to accept is the newest one, so it was the one hidden."""
    _commit(refined_repo, BAD, GOOD)
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
    assert [r["status"] for r in payload["rows"]] == ["pending"]
    assert (payload["refinement_count"], payload["truncated"]) == (2, True)
    whole = cli_json(
        runner.invoke(
            app, ["graph", "refinements", "list", str(refined_repo), "--json"]
        )
    )
    assert [r["status"] for r in whole["rows"]] == ["pending", "rejected"]
    assert whole["truncated"] is False


def test_a_limit_over_the_ceiling_is_refused_with_the_ceiling(refined_repo: Path):
    result = runner.invoke(
        app, ["graph", "refinements", "list", str(refined_repo), "--limit", "10000"]
    )
    assert result.exit_code != 0
    assert str(MAX_LOG_ROWS) in result.output


def test_a_transition_answers_with_the_row_the_listing_shows(refined_repo: Path):
    """`accept` answered `anchors: []` for a row the listing showed two anchors for, and the human
    panel printed an empty target for every kind that has no `src`/`dst` pair."""
    rid = _propose(refined_repo)
    listed = cli_json(
        runner.invoke(
            app, ["graph", "refinements", "list", str(refined_repo), "--json"]
        )
    )["rows"][0]
    accepted = cli_json(
        runner.invoke(
            app,
            ["graph", "refinements", "accept", str(rid), str(refined_repo), "--json"],
        )
    )
    assert accepted["anchors"] == listed["anchors"] != []
    assert accepted["src"] == listed["src"]


def test_the_default_run_view_says_it_hid_the_skipped_rows(refined_repo: Path):
    """`filtered` is what tells an agent "nothing matched" from "nothing recorded". A repo whose
    only runs were skipped answered `runs: [], filtered: false`, which reads as "never run"."""
    _add_run(refined_repo, status=RunStatus.SKIPPED, age_seconds=0)
    hidden = _log(refined_repo)
    assert hidden["runs"] == []
    assert hidden["filtered"] is True
    assert hidden["hidden_statuses"] == ["skipped"]
    shown = _log(refined_repo, skipped=True)
    assert [r["status"] for r in shown["runs"]] == ["skipped"]
    assert (shown["filtered"], shown["hidden_statuses"]) == (False, [])
    assert shown["run_count"] == 1


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
    _add_run(refined_repo, status=RunStatus.SKIPPED, age_seconds=30 * 86400)
    _add_run(refined_repo, status=RunStatus.SKIPPED, age_seconds=0)
    _propose(refined_repo)  # a real run, which prune must never touch
    payload = cli_json(
        runner.invoke(
            app, ["graph", "refinements", "prune", str(refined_repo), "--json"]
        )
    )
    assert payload == {
        "removed_runs": 1,
        "removed_refinements": 0,
        "stranded_runs": 0,
    }
    assert sorted(r["status"] for r in _runs(refined_repo, skipped=True)) == [
        "skipped",
        "succeeded",
    ]


def test_prune_finishes_a_run_a_dead_process_left_queued(refined_repo: Path):
    """Nothing else can close it: `abort` is refused from any other process and the registry died
    with the one that opened it, so before this the row sat `queued` and out of every view."""
    stranded = _add_run(refined_repo, status=RunStatus.QUEUED, age_seconds=7200)
    payload = cli_json(
        runner.invoke(
            app, ["graph", "refinements", "prune", str(refined_repo), "--json"]
        )
    )
    assert payload["stranded_runs"] == 1
    rows = {r["run_id"]: r for r in _runs(refined_repo, skipped=True)}
    assert rows[stranded]["status"] == "skipped"
    assert "stranded" in rows[stranded]["error"]


def test_a_broken_user_config_is_one_line_from_prune(refined_repo: Path):
    """`prune` is the one command here that reads the user's settings, and it read them outside
    the guard every other command surface uses: a bad file printed a pydantic traceback."""
    user_config_path().parent.mkdir(parents=True, exist_ok=True)
    user_config_path().write_text('{"observer": {"skipped_retention_days": "soon"}}')
    result = runner.invoke(app, ["graph", "refinements", "prune", str(refined_repo)])
    assert result.exit_code == 1
    assert "invalid user config" in result.output
    assert "Traceback" not in result.output


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
