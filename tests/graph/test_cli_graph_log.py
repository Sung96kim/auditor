"""`auditr graph log` — the provenance log, in two views."""

import asyncio
import time
from pathlib import Path

import pytest
from _support import cli_json, one_line
from graph._support import (
    GOOD_PROPOSAL,
    add_observer_run,
    refine_abort,
    refine_run,
    render_text,
)
from typer.testing import CliRunner

from auditor.cli import app
from auditor.cli.render import render_graph_log
from auditor.database import open_repo_index
from auditor.graph.payloads import LogReport, LogView, RunRowPayload
from auditor.graph.refine.models import (
    Refinement,
    RefinementCounts,
    RefinementKind,
    RefinementPayload,
    RefinementStatus,
    RefinementTarget,
    Run,
    RunStatus,
)

runner = CliRunner()


async def _add_refinement(repo: Path, *, name: str, age_days: float) -> int:
    """One backdated `unresolvable` row. `created_at` is stamped when a row is written, so a row
    older than the log window cannot be produced through the service."""
    index = await open_repo_index(repo)
    try:
        run_id = await index.runs.add_run(
            Run(repo_identity=index.partition.identity, status=RunStatus.SUCCEEDED)
        )
        return await index.refinements.add_refinement(
            Refinement(
                run_id=run_id,
                repo_identity=index.partition.identity,
                kind=RefinementKind.UNRESOLVABLE,
                target=RefinementTarget(node_id="caller.py::main", name=name),
                payload=RefinementPayload(reason_code="dynamic"),
                reason="recorded by hand for the window test",
                created_at=time.time() - age_days * 86400,
            )
        )
    finally:
        await index.aclose()


@pytest.fixture
def logged_repo(refine_repo: Path) -> Path:
    """`refine_repo` with one committed run and one assessment-only run recorded.

    The `skipped` row is written directly: at S5c the only producer of one is
    `RunsDB.finish_stranded_runs`, and the observer that spec 12.2 describes arrives in S8.
    `refine_repo` already took `process_runs`, so the registries are emptied around the test.
    """
    refine_run(refine_repo, GOOD_PROPOSAL)
    add_observer_run(refine_repo, status=RunStatus.SKIPPED, age_seconds=0)
    return refine_repo


def test_the_default_view_is_runs_and_hides_the_skipped_ones(logged_repo: Path):
    """`filtered` is true on a page nobody filtered, because hiding `skipped` is a narrowing.
    `hidden_statuses` is what says which narrowing, and it is the only thing that separates
    "none you can see" from "none"."""
    payload = cli_json(runner.invoke(app, ["graph", "log", str(logged_repo), "--json"]))
    assert payload["view"] == "runs"
    assert [r["status"] for r in payload["runs"]] == ["succeeded"]
    assert payload["refinements"] == []
    assert payload["filtered"] is True
    assert payload["hidden_statuses"] == ["skipped"]
    assert payload["run_count"] == 1


def test_skipped_brings_the_assessment_rows_in(logged_repo: Path):
    payload = cli_json(
        runner.invoke(app, ["graph", "log", str(logged_repo), "--skipped", "--json"])
    )
    assert sorted(r["status"] for r in payload["runs"]) == ["skipped", "succeeded"]
    assert payload["filtered"] is False
    assert payload["hidden_statuses"] == []


def test_a_run_row_carries_the_split_of_what_its_run_produced(logged_repo: Path):
    """The last column is a `RefinementCounts`, not a number: a run must not be credited with the
    proposals it refused."""
    payload = cli_json(runner.invoke(app, ["graph", "log", str(logged_repo), "--json"]))
    assert payload["runs"][0]["refinements"] == {"committed": 1, "rejected": 0}
    assert payload["runs"][0]["summary"] == "1 committed, 0 rejected"


def test_a_status_filter_narrows_and_says_so(logged_repo: Path):
    """Two real runs of different statuses, so the filter has something to narrow to as well as
    something to narrow away."""
    refine_abort(logged_repo, GOOD_PROPOSAL, reason="changed my mind")
    aborted = cli_json(
        runner.invoke(
            app, ["graph", "log", str(logged_repo), "--status", "aborted", "--json"]
        )
    )
    assert [r["status"] for r in aborted["runs"]] == ["aborted"]
    assert aborted["filtered"] is True
    assert aborted["hidden_statuses"] == []
    failed = cli_json(
        runner.invoke(
            app, ["graph", "log", str(logged_repo), "--status", "failed", "--json"]
        )
    )
    assert failed["runs"] == []
    assert failed["filtered"] is True


def test_a_run_status_is_refused_in_the_refinements_view(logged_repo: Path):
    """`one_line` because rich wraps at 80 columns off a TTY and the valid set is longer."""
    result = runner.invoke(
        app,
        ["graph", "log", str(logged_repo), "--refinements", "--status", "succeeded"],
    )
    assert result.exit_code != 0
    printed = one_line(result.output)
    assert "unknown status: succeeded" in printed
    for status in RefinementStatus:
        assert status.value in printed


def test_a_refinement_status_is_refused_in_the_runs_view(logged_repo: Path):
    result = runner.invoke(
        app, ["graph", "log", str(logged_repo), "--status", "pending"]
    )
    assert result.exit_code != 0
    printed = one_line(result.output)
    assert "unknown status: pending" in printed
    for status in RunStatus:
        assert status.value in printed


def test_the_refinements_view_is_a_different_shape(logged_repo: Path):
    payload = cli_json(
        runner.invoke(
            app, ["graph", "log", str(logged_repo), "--refinements", "--json"]
        )
    )
    assert payload["view"] == "refinements"
    assert payload["runs"] == []
    assert payload["filtered"] is False
    assert payload["hidden_statuses"] == []
    assert payload["refinement_count"] == len(payload["refinements"]) == 1


@pytest.mark.parametrize("since", ["90s", "2h", "7d", "2026-08-20"])
def test_a_valid_since_is_accepted(logged_repo: Path, since: str):
    payload = cli_json(
        runner.invoke(
            app, ["graph", "log", str(logged_repo), "--since", since, "--json"]
        )
    )
    assert payload["filtered"] is True


def test_a_far_past_cutoff_keeps_the_rows_and_a_future_one_drops_them(
    logged_repo: Path,
):
    kept = cli_json(
        runner.invoke(
            app, ["graph", "log", str(logged_repo), "--since", "7d", "--json"]
        )
    )
    assert len(kept["runs"]) == 1
    dropped = cli_json(
        runner.invoke(
            app, ["graph", "log", str(logged_repo), "--since", "2099-01-01", "--json"]
        )
    )
    assert dropped["runs"] == []


def test_the_refinements_page_is_newest_first_and_cuts_the_window_before_the_limit(
    logged_repo: Path,
):
    """Both halves of the contract in one page: the view is newest first, and `--since` is a SQL
    clause, so a `--limit` smaller than the window's contents still answers the newest rows.

    This is not the mutation gate for the SQL-versus-Python window: `LogQuery.page` orders
    descending, so a post-limit filter could not lose a row here.
    `tests/graph/test_refinements_db.py::test_the_time_window_runs_before_the_limit_not_after_it`
    is that gate, on the oldest-first reader where the distinction is observable.
    """
    for name in ("one", "two", "three"):
        asyncio.run(_add_refinement(logged_repo, name=name, age_days=0))
    asyncio.run(_add_refinement(logged_repo, name="ancient", age_days=30))
    payload = cli_json(
        runner.invoke(
            app,
            [
                "graph",
                "log",
                str(logged_repo),
                "--refinements",
                "--since",
                "2h",
                "--limit",
                "2",
                "--json",
            ],
        )
    )
    assert [r["name"] for r in payload["refinements"]] == ["three", "two"]
    assert (
        payload["refinement_count"] == 4
    )  # the three plus the committed one, not `ancient`
    assert payload["truncated"] is True


@pytest.mark.parametrize("since", ["yesterday", "", "2h ago", "7"])
def test_an_unparseable_since_names_what_is_accepted(logged_repo: Path, since: str):
    """An empty string is a caller that thinks it set a window, not one that set none, so it is
    an error too rather than a silently unfiltered page."""
    result = runner.invoke(app, ["graph", "log", str(logged_repo), "--since", since])
    assert result.exit_code != 0
    printed = one_line(result.output)
    assert "45m" in printed
    assert "ISO date" in printed


def test_the_limit_caps_the_rows_and_the_page_says_what_it_left(logged_repo: Path):
    payload = cli_json(
        runner.invoke(
            app,
            ["graph", "log", str(logged_repo), "--skipped", "--limit", "1", "--json"],
        )
    )
    assert len(payload["runs"]) == 1
    assert payload["run_count"] == 2
    assert payload["truncated"] is True


def test_the_renderer_tells_an_empty_repo_from_a_narrowed_page_from_a_hidden_one():
    """Three causes, three sentences. Keying the "nothing matched" line off `filtered` alone would
    print it on a repo with nothing recorded, because the default run view sets `filtered`."""
    assert "none recorded" in render_text(render_graph_log, LogReport())
    assert "nothing matched" in render_text(render_graph_log, LogReport(filtered=True))
    hidden = render_text(
        render_graph_log, LogReport(filtered=True, hidden_statuses=(RunStatus.SKIPPED,))
    )
    assert "nothing matched" not in hidden
    assert "--skipped" in hidden


def test_the_renderer_shows_a_run_summary_and_the_rows_its_run_owns():
    """`started_at=0.0` so `_stamp` answers "" and the `n` cell is the only number in the row:
    a wall-clock timestamp would put digits in the `when` column and make the assertion drift."""
    report = LogReport(
        view=LogView.RUNS,
        runs=(
            RunRowPayload.of(
                Run(
                    repo_identity="/repo/.git",
                    status=RunStatus.SUCCEEDED,
                    summary="1 committed, 0 rejected",
                    started_at=0.0,
                ),
                refinements=RefinementCounts(committed=1, rejected=2),
            ),
        ),
        run_count=1,
    )
    out = render_text(render_graph_log, report)
    assert "succeeded" in out
    assert "1 committed" in out
    assert " 3 " in out  # the `n` column is the run's row total, not just what it kept


def test_the_renderer_says_how_much_a_capped_page_left_behind():
    report = LogReport(
        view=LogView.RUNS,
        runs=(RunRowPayload.of(Run(repo_identity="/repo/.git", started_at=0.0)),),
        run_count=9,
        truncated=True,
    )
    out = render_text(render_graph_log, report)
    assert "1 of 9" in out
    assert "--limit" in out
