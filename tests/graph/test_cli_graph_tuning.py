"""`auditr graph tuning`: the four subcommands spec 12.2 names, plus the trial the CLI runs."""

import asyncio
from pathlib import Path

from _support import cli_json, one_line, tool_data
from fastmcp import Client
from graph._support import cells, render_text
from typer.testing import CliRunner

from auditor.cli import app
from auditor.cli.render import render_graph_tuning, render_graph_tuning_row
from auditor.graph.payloads import TuningReport, TuningRowPayload
from auditor.graph.refine.models import TuningMetrics, TuningStatus
from auditor.mcp import mcp

runner = CliRunner()


def _propose(repo: Path, value: str = "helper", reason: str = "noise here") -> dict:
    """One proposal through the MCP producer, which is the only door that writes a row.

    Synchronous on purpose: the CLI's `run()` calls `asyncio.run`, which refuses to nest, so a
    test that drives both surfaces has to stay off the event loop.
    """

    async def go() -> dict:
        async with Client(mcp) as client:
            return tool_data(
                await client.call_tool(
                    "propose_tuning",
                    {
                        "key": "stopwords",
                        "value": value,
                        "reason": reason,
                        "path": str(repo),
                    },
                )
            )

    return asyncio.run(go())


def _payload(**over: object) -> TuningRowPayload:
    """One measured row as the wire carries it, so a renderer test needs no database."""
    base = {
        "tuning_id": 4,
        "key": "stopwords",
        "value": "helper",
        "status": TuningStatus.PENDING,
        "token": "qrs234",
        "reason": "noise",
        "run_id": "r",
        "created_at": 0.0,
        "metrics": TuningMetrics.model_validate(
            {
                "clusters": 22,
                "name_edge_churn": 0.0041,
                "label_churn": 0.25,
                "measured_at": 1.0,
                "baseline": {"clusters": 24},
            }
        ),
        "measured": True,
        "passed": True,
    }
    return TuningRowPayload.model_validate(base | over)


def test_list_shows_the_proposal_and_its_confirmation_word(refine_repo: Path):
    row = _propose(refine_repo)
    payload = cli_json(
        runner.invoke(app, ["graph", "tuning", "list", str(refine_repo), "--json"])
    )
    assert payload["rows"][0]["value"] == "helper"
    assert payload["rows"][0]["token"] == row["token"]
    assert payload["rows"][0]["measured"] is False
    assert payload["rows"][0]["passed"] is False
    assert (payload["active"], payload["cap"]) == (0, 20)


def test_accept_without_the_word_prints_it_and_changes_nothing(refine_repo: Path):
    """Spec 12.2 brackets `--token`; leaving it off is how a human is told the word (P9)."""
    row = _propose(refine_repo)
    ident = str(row["tuning_id"])
    runner.invoke(
        app, ["graph", "tuning", "measure", ident, str(refine_repo), "--json"]
    )
    result = runner.invoke(app, ["graph", "tuning", "accept", ident, str(refine_repo)])
    assert result.exit_code == 1
    assert f"--token {row['token']}" in one_line(result.stderr)
    listed = cli_json(
        runner.invoke(app, ["graph", "tuning", "list", str(refine_repo), "--json"])
    )
    assert listed["rows"][0]["status"] == "pending"


def test_measure_then_accept_then_revert(refine_repo: Path):
    """The whole hand path spec 12.2 names, in the order a human walks it."""
    row = _propose(refine_repo)
    ident, word = str(row["tuning_id"]), row["token"]
    measured = cli_json(
        runner.invoke(
            app, ["graph", "tuning", "measure", ident, str(refine_repo), "--json"]
        )
    )
    assert measured["measured"] is True
    assert measured["passed"] is True
    assert measured["status"] == "pending"
    accepted = cli_json(
        runner.invoke(
            app,
            [
                "graph",
                "tuning",
                "accept",
                ident,
                str(refine_repo),
                "--token",
                word,
                "--json",
            ],
        )
    )
    assert accepted["status"] == "active"
    reverted = cli_json(
        runner.invoke(
            app,
            [
                "graph",
                "tuning",
                "revert",
                ident,
                str(refine_repo),
                "--token",
                word,
                "--json",
            ],
        )
    )
    assert reverted["status"] == "reverted"


def test_the_stopword_names_the_row_as_well_as_the_id(refine_repo: Path):
    row = _propose(refine_repo)
    runner.invoke(
        app, ["graph", "tuning", "measure", "helper", str(refine_repo), "--json"]
    )
    accepted = cli_json(
        runner.invoke(
            app,
            [
                "graph",
                "tuning",
                "accept",
                "helper",
                str(refine_repo),
                "--token",
                row["token"],
                "--json",
            ],
        )
    )
    assert accepted["tuning_id"] == row["tuning_id"]


def test_an_empty_list_says_where_a_row_comes_from():
    rendered = render_text(render_graph_tuning, TuningReport())
    assert "propose_tuning" in one_line(rendered)


def test_the_list_renderer_puts_each_value_under_its_own_header():
    """A trial column that reads "not measured yet" is the one a human must not mistake for a
    passing trial."""
    unmeasured = TuningRowPayload(
        tuning_id=3,
        key="stopwords",
        value="helper",
        status=TuningStatus.PENDING,
        token="qrs234",
        reason="noise",
        run_id="r",
        created_at=0.0,
    )
    rendered = render_text(
        render_graph_tuning, TuningReport(rows=(unmeasured,), active=1, cap=20)
    )
    assert cells(rendered, "3") == [
        "3",
        "helper",
        "pending",
        "qrs234",
        "not measured yet",
    ]
    assert "1 of 20 stopwords active" in one_line(rendered)


def test_the_list_renderer_names_the_guard_that_refused_a_trial():
    """A refused trial has numbers too, and showing them where a passing trial's numbers go is
    exactly the mistake the verdict exists to stop (E1)."""
    rendered = one_line(
        render_text(
            render_graph_tuning,
            TuningReport(
                rows=(
                    _payload(
                        passed=False,
                        status=TuningStatus.REJECTED,
                        refused="3 pinned cluster refinement(s) would be stranded",
                    ),
                ),
                cap=20,
            ),
        )
    )
    assert "refused: 3 pinned cluster" in rendered


def test_the_row_renderer_shows_the_cluster_delta_a_human_decides_on():
    rendered = one_line(render_text(render_graph_tuning_row, _payload()))
    assert "24 -> 22" in rendered
    assert "name-edge churn 0.4%" in rendered
    assert "label churn 25.0%" in rendered
    assert "passed" in rendered


def test_the_row_renderer_says_which_guard_refused():
    rendered = one_line(
        render_text(
            render_graph_tuning_row,
            _payload(passed=False, refused="singleton clusters 2 -> 3"),
        )
    )
    assert "refused: singleton clusters 2 -> 3" in rendered
