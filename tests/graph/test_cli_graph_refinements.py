"""`auditr graph refinements` — the five subcommands spec 12.2 names."""

import asyncio
import shutil
from pathlib import Path

import pytest
from _support import cli_json, one_line, tool_data
from fastmcp import Client
from graph._support import (
    GOOD_PROPOSAL,
    add_observer_run,
    cells,
    refine_run,
    render_text,
)
from typer.testing import CliRunner

from auditor.cli import app
from auditor.cli.render import render_graph_refinement, render_graph_refinements
from auditor.database import open_repo_index
from auditor.graph.model import MAX_LOG_ROWS, EdgeKind
from auditor.graph.payloads import RefinementRowPayload, RefinementsReport
from auditor.graph.refine.models import (
    RefinementKind,
    RefinementPayload,
    RefinementStatus,
    RunStatus,
    Tier,
)
from auditor.mcp import mcp
from auditor.paths import auditor_home, user_config_path

runner = CliRunner()

BAD = GOOD_PROPOSAL | {"name": "get_user", "reason": "a name main does not call"}


def _propose(repo: Path) -> int:
    """One committed `add_edge` for the queue row, and its id."""
    return refine_run(repo, GOOD_PROPOSAL)["committed"][0]["refinement_id"]


def _log(repo: Path, **kw) -> dict:
    """One page of the provenance log through the `graph_log` MCP tool, which is the surface these
    tests read; `tests/graph/test_cli_graph_log.py` drives the CLI half."""

    async def go() -> dict:
        async with Client(mcp) as client:
            return tool_data(
                await client.call_tool("graph_log", {"path": str(repo), **kw})
            )

    return asyncio.run(go())


def _runs(repo: Path, *, skipped: bool) -> list[dict]:
    """The runs half of that page."""
    return _log(repo, skipped=skipped)["runs"]


def test_an_empty_list_says_it_is_empty_not_filtered(refine_repo: Path):
    payload = cli_json(
        runner.invoke(app, ["graph", "refinements", "list", str(refine_repo), "--json"])
    )
    assert payload == {
        "rows": [],
        "filtered": False,
        "refinement_count": 0,
        "truncated": False,
    }


def test_a_status_filter_narrows_to_the_rows_that_match(refine_repo: Path):
    """Two rows, one of each status, so `filtered` distinguishes "nothing matched" from "nothing
    recorded" against a list that is not empty either way."""
    committed = refine_run(refine_repo, GOOD_PROPOSAL, BAD)["committed"]
    pending = cli_json(
        runner.invoke(
            app,
            [
                "graph",
                "refinements",
                "list",
                str(refine_repo),
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
                str(refine_repo),
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
                str(refine_repo),
                "--status",
                "active",
                "--json",
            ],
        )
    )
    assert none_active["rows"] == []
    assert (none_active["filtered"], none_active["refinement_count"]) == (True, 0)


def test_an_unknown_status_is_refused_with_the_valid_set(refine_repo: Path):
    """Every value, not one of them: naming half the set is the same unhelpful message."""
    result = runner.invoke(
        app, ["graph", "refinements", "list", str(refine_repo), "--status", "nope"]
    )
    assert result.exit_code != 0
    printed = one_line(result.output)
    for status in RefinementStatus:
        assert status.value in printed


def test_list_shows_the_committed_correction(refine_repo: Path):
    rid = _propose(refine_repo)
    payload = cli_json(
        runner.invoke(app, ["graph", "refinements", "list", str(refine_repo), "--json"])
    )
    assert [r["refinement_id"] for r in payload["rows"]] == [rid]
    row = payload["rows"][0]
    assert row["status"] == "pending"
    assert row["tier"] == "B"
    assert row["src"] == "caller.py::main"
    assert set(row["anchors"]) == {"caller.py::main", "helper.py::read_event"}


def test_the_limit_caps_the_rows(refine_repo: Path):
    refine_run(refine_repo, GOOD_PROPOSAL, BAD)
    payload = cli_json(
        runner.invoke(
            app,
            [
                "graph",
                "refinements",
                "list",
                str(refine_repo),
                "--limit",
                "1",
                "--json",
            ],
        )
    )
    assert len(payload["rows"]) == 1


def test_the_page_is_the_newest_rows_and_the_total_says_what_it_left(
    refine_repo: Path,
):
    """A page at the cap and a complete list looked the same, and the page was the *oldest* rows:
    the `pending` row a human has to accept is the newest one, so it was the one hidden."""
    refine_run(refine_repo, BAD, GOOD_PROPOSAL)
    payload = cli_json(
        runner.invoke(
            app,
            [
                "graph",
                "refinements",
                "list",
                str(refine_repo),
                "--limit",
                "1",
                "--json",
            ],
        )
    )
    assert [r["status"] for r in payload["rows"]] == ["pending"]
    assert (payload["refinement_count"], payload["truncated"]) == (2, True)
    whole = cli_json(
        runner.invoke(app, ["graph", "refinements", "list", str(refine_repo), "--json"])
    )
    assert [r["status"] for r in whole["rows"]] == ["pending", "rejected"]
    assert whole["truncated"] is False


def test_a_limit_over_the_ceiling_is_refused_with_the_ceiling(refine_repo: Path):
    result = runner.invoke(
        app, ["graph", "refinements", "list", str(refine_repo), "--limit", "10000"]
    )
    assert result.exit_code != 0
    assert str(MAX_LOG_ROWS) in result.output


def test_a_transition_answers_with_the_row_the_listing_shows(refine_repo: Path):
    """`accept` answered `anchors: []` for a row the listing showed two anchors for, and the human
    panel printed an empty target for every kind that has no `src`/`dst` pair."""
    rid = _propose(refine_repo)
    listed = cli_json(
        runner.invoke(app, ["graph", "refinements", "list", str(refine_repo), "--json"])
    )["rows"][0]
    accepted = cli_json(
        runner.invoke(
            app,
            ["graph", "refinements", "accept", str(rid), str(refine_repo), "--json"],
        )
    )
    assert accepted["anchors"] == listed["anchors"] != []
    assert accepted["src"] == listed["src"]


def test_the_default_run_view_says_it_hid_the_skipped_rows(refine_repo: Path):
    """`filtered` is what tells an agent "nothing matched" from "nothing recorded". A repo whose
    only runs were skipped answered `runs: [], filtered: false`, which reads as "never run"."""
    add_observer_run(refine_repo, status=RunStatus.SKIPPED, age_seconds=0)
    hidden = _log(refine_repo)
    assert hidden["runs"] == []
    assert hidden["filtered"] is True
    assert hidden["hidden_statuses"] == ["skipped"]
    shown = _log(refine_repo, skipped=True)
    assert [r["status"] for r in shown["runs"]] == ["skipped"]
    assert (shown["filtered"], shown["hidden_statuses"]) == (False, [])
    assert shown["run_count"] == 1


def _refined_edges(repo: Path) -> int:
    """Edges the overlay put in the graph, which is what a rebuild would have added."""

    async def go() -> int:
        index = await open_repo_index(repo)
        try:
            edges = await index.graph.all_edges()
            return sum(1 for e in edges if e["provenance"] == "refined")
        finally:
            await index.aclose()

    return asyncio.run(go())


def test_accept_activates_and_the_next_build_applies_it(refine_repo: Path):
    """`accept` is a status change and nothing else: the build is the one merge point (spec 6).

    The locks directory is emptied first because the commit that produced this row already took
    the rebuild lock; a stopwatch is not the gate, since a rebuild of a three-file repo is fast
    enough to be flaky, but the lock file and the unapplied edge are not.
    """
    rid = _propose(refine_repo)
    locks = auditor_home() / "observer" / "locks"
    shutil.rmtree(locks, ignore_errors=True)
    accepted = cli_json(
        runner.invoke(
            app,
            ["graph", "refinements", "accept", str(rid), str(refine_repo), "--json"],
        )
    )
    assert accepted["status"] == "active"
    assert not list(locks.glob("*.lock"))
    assert _refined_edges(refine_repo) == 0
    built = cli_json(
        runner.invoke(app, ["graph", "build", str(refine_repo), "--no-scan", "--json"])
    )
    assert built["refined"] == 1
    assert _refined_edges(refine_repo) == 1


@pytest.mark.parametrize(
    ("command", "expected"), [("revert", "reverted"), ("pin", "pinned")]
)
def test_the_other_transitions(refine_repo: Path, command: str, expected: str):
    rid = _propose(refine_repo)
    payload = cli_json(
        runner.invoke(
            app,
            ["graph", "refinements", command, str(rid), str(refine_repo), "--json"],
        )
    )
    assert payload["status"] == expected


def test_an_illegal_transition_names_the_current_status(refine_repo: Path):
    rid = _propose(refine_repo)
    assert (
        runner.invoke(
            app, ["graph", "refinements", "revert", str(rid), str(refine_repo)]
        ).exit_code
        == 0
    )
    result = runner.invoke(
        app, ["graph", "refinements", "accept", str(rid), str(refine_repo)]
    )
    assert result.exit_code != 0
    assert "reverted" in result.output


def test_an_unknown_id_is_named(refine_repo: Path):
    result = runner.invoke(
        app, ["graph", "refinements", "accept", "4242", str(refine_repo)]
    )
    assert result.exit_code != 0
    assert "4242" in result.output


def test_prune_drops_only_the_assessment_runs_past_the_window(refine_repo: Path):
    add_observer_run(refine_repo, status=RunStatus.SKIPPED, age_seconds=30 * 86400)
    add_observer_run(refine_repo, status=RunStatus.SKIPPED, age_seconds=0)
    _propose(refine_repo)  # a real run, which prune must never touch
    payload = cli_json(
        runner.invoke(
            app, ["graph", "refinements", "prune", str(refine_repo), "--json"]
        )
    )
    assert payload == {
        "removed_runs": 1,
        "removed_refinements": 0,
        "stranded_runs": 0,
    }
    assert sorted(r["status"] for r in _runs(refine_repo, skipped=True)) == [
        "skipped",
        "succeeded",
    ]


def test_prune_finishes_a_run_a_dead_process_left_queued(refine_repo: Path):
    """Nothing else can close it: `abort` is refused from any other process and the registry died
    with the one that opened it, so before this the row sat `queued` and out of every view."""
    stranded = add_observer_run(refine_repo, status=RunStatus.QUEUED, age_seconds=7200)
    payload = cli_json(
        runner.invoke(
            app, ["graph", "refinements", "prune", str(refine_repo), "--json"]
        )
    )
    assert payload["stranded_runs"] == 1
    rows = {r["run_id"]: r for r in _runs(refine_repo, skipped=True)}
    assert rows[stranded]["status"] == "skipped"
    assert "stranded" in rows[stranded]["error"]


def test_a_broken_user_config_is_one_line_from_prune(refine_repo: Path):
    """`prune` is the one command here that reads the user's settings, and it read them outside
    the guard every other command surface uses: a bad file printed a pydantic traceback."""
    user_config_path().parent.mkdir(parents=True, exist_ok=True)
    user_config_path().write_text('{"observer": {"skipped_retention_days": "soon"}}')
    result = runner.invoke(app, ["graph", "refinements", "prune", str(refine_repo)])
    assert result.exit_code == 1
    assert "invalid user config" in result.output
    assert "Traceback" not in result.output


def _row() -> RefinementRowPayload:
    """One `add_edge` row with a real node id: 70-odd characters, which is what the five-column
    layout has to keep on one line at width 120."""
    return RefinementRowPayload(
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


def test_the_empty_renderer_distinguishes_no_rows_from_no_matches():
    assert "none recorded" in render_text(render_graph_refinements, RefinementsReport())
    assert "matched" in render_text(
        render_graph_refinements, RefinementsReport(filtered=True)
    )


def test_every_value_is_rendered_under_its_own_header():
    """Swapping the tier and the status left the suite green: every asserted substring was still
    somewhere in the table, just in the wrong column."""
    out = render_text(
        render_graph_refinements,
        RefinementsReport.of([_row()], filtered=False, total=1),
    )
    assert cells(out, "id") == ["id", "kind", "tier", "status", "target"]
    assert cells(out, "3") == [
        "3",
        "add_edge",
        "B",
        "pending",
        "plugin/hooks/audit_edit.py::main -> plugin/hooks/_common.py::read_event",
    ]


def test_a_capped_page_says_how_many_rows_there_are():
    assert "showing 1 of 9" in render_text(
        render_graph_refinements,
        RefinementsReport.of([_row()], filtered=False, total=9),
    )
    assert "showing" not in render_text(
        render_graph_refinements,
        RefinementsReport.of([_row()], filtered=False, total=1),
    )


def test_the_panel_shows_the_label_a_cluster_row_proposes():
    """A `relabel_cluster` row printed an empty target and no label, which is the whole of what a
    human accepting it has to judge."""
    row = RefinementRowPayload(
        refinement_id=4,
        run_id="r1",
        kind=RefinementKind.RELABEL_CLUSTER,
        tier=Tier.A,
        status=RefinementStatus.PENDING,
        members=("m.py::f", "m.py::g"),
        payload=RefinementPayload(label="user lookup"),
        reason="both fetch a user",
    )
    out = render_text(render_graph_refinement, row)
    assert "m.py::f, m.py::g" in out
    assert "user lookup" in out


def test_the_row_renderer_shows_the_edge_the_tier_and_the_reason():
    """A real node id is 70-odd characters. Five columns keep it on one line at width 120; the
    reason rides the continuation row rather than stealing half the table."""
    out = render_text(
        render_graph_refinements,
        RefinementsReport.of([_row()], filtered=False, total=1),
    )
    assert (
        "plugin/hooks/audit_edit.py::main -> plugin/hooks/_common.py::read_event" in out
    )
    assert "pending" in out
    assert "sibling _common" in out
