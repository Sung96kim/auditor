"""`auditr graph log` — the provenance log, in two views."""

import asyncio
import json
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
from _support import cli_json, one_line
from graph._support import (
    GOOD_PROPOSAL,
    add_observer_run,
    cells,
    refine_abort,
    refine_run,
    render_text,
    tool_log,
)
from typer.testing import CliRunner

from auditor.cli import app
from auditor.cli.render import render_graph_log
from auditor.database import open_repo_index
from auditor.graph.model import MAX_LOG_ROWS, EdgeKind
from auditor.graph.payloads import (
    LogReport,
    LogView,
    RefinementRowPayload,
    RunRowPayload,
)
from auditor.graph.refine.models import (
    Assessment,
    Decision,
    NodePair,
    ProducerKind,
    Refinement,
    RefinementCounts,
    RefinementKind,
    RefinementPayload,
    RefinementStatus,
    RefinementTarget,
    Run,
    RunnerKind,
    RunStatus,
    Tier,
    TriggerDetail,
    TriggerKind,
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
    """A page nobody filtered is not `filtered`. What the view hid on its own is reported apart,
    by name and by count, because that is what separates "none you can see" from "none"."""
    payload = cli_json(runner.invoke(app, ["graph", "log", str(logged_repo), "--json"]))
    assert payload["view"] == "runs"
    assert [r["status"] for r in payload["runs"]] == ["succeeded"]
    assert payload["refinements"] == []
    assert payload["filtered"] is False
    assert payload["narrowed_by"] == []
    assert payload["hidden_statuses"] == ["skipped"]
    assert payload["hidden_count"] == 1
    assert payload["run_count"] == 1


def test_skipped_brings_the_assessment_rows_in(logged_repo: Path):
    payload = cli_json(
        runner.invoke(app, ["graph", "log", str(logged_repo), "--skipped", "--json"])
    )
    assert sorted(r["status"] for r in payload["runs"]) == ["skipped", "succeeded"]
    assert payload["filtered"] is False
    assert payload["hidden_statuses"] == []
    assert payload["hidden_count"] == 0


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
    assert aborted["narrowed_by"] == ["status"]
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


#: computed against this run's clock, because a fixed date would drift out of every window
_ISO_DATE = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
_ISO_DATETIME = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 7200))


@pytest.fixture
def windowed_repo(refine_repo: Path) -> Path:
    """One run inside any window a reader would open, and one older than every one of them."""
    add_observer_run(refine_repo, status=RunStatus.SUCCEEDED, age_seconds=0)
    add_observer_run(refine_repo, status=RunStatus.FAILED, age_seconds=30 * 86400)
    return refine_repo


@pytest.mark.parametrize("since", ["90s", "45m", "2h", "7d", _ISO_DATE, _ISO_DATETIME])
def test_a_since_window_keeps_the_rows_inside_it_and_drops_the_rest(
    windowed_repo: Path, since: str
):
    """Every form the help text and `graph.md` advertise, asserted on the rows. This test asserted
    `filtered`, which the default runs view set on its own with `--since` wired to nothing."""
    inside = cli_json(
        runner.invoke(
            app, ["graph", "log", str(windowed_repo), "--since", since, "--json"]
        )
    )
    assert [r["status"] for r in inside["runs"]] == ["succeeded"]
    assert inside["run_count"] == 1
    assert inside["narrowed_by"] == ["since"]
    both = cli_json(runner.invoke(app, ["graph", "log", str(windowed_repo), "--json"]))
    assert [r["status"] for r in both["runs"]] == ["succeeded", "failed"]


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


def _nothing_recorded(repo: Path) -> None:
    """`refine_repo` is built but has never run anything, so the log is empty on its own."""


def _a_window_that_matched_nothing(repo: Path) -> None:
    """Runs older than any window a reader would open, and not one of them hidden."""
    add_observer_run(repo, status=RunStatus.SUCCEEDED, age_seconds=30 * 86400)


def _rows_the_view_hides(repo: Path) -> None:
    add_observer_run(repo, status=RunStatus.SKIPPED, age_seconds=0)


@pytest.mark.parametrize(
    ("prepare", "extra", "phrase", "expected"),
    [
        (
            _nothing_recorded,
            [],
            "none recorded",
            {"filtered": False, "narrowed_by": [], "hidden_count": 0},
        ),
        (
            _a_window_that_matched_nothing,
            ["--since", "2h"],
            "nothing matched --since",
            {"filtered": True, "narrowed_by": ["since"], "hidden_count": 0},
        ),
        (
            _rows_the_view_hides,
            [],
            "1 skipped run hidden",
            {"filtered": False, "narrowed_by": [], "hidden_count": 1},
        ),
    ],
    ids=["nothing recorded", "a window that matched nothing", "rows the view hides"],
)
def test_an_empty_page_names_the_cause_that_emptied_it(
    refine_repo: Path,
    prepare: Callable[[Path], None],
    extra: list[str],
    phrase: str,
    expected: dict[str, object],
) -> None:
    """The page a reader gets, not one built by hand: the default runs view used to blame its own
    hiding for a window that missed, and to offer `--skipped` on a repo with nothing in it."""
    prepare(refine_repo)
    payload = cli_json(
        runner.invoke(app, ["graph", "log", str(refine_repo), *extra, "--json"])
    )
    assert payload["runs"] == []
    assert {key: payload[key] for key in expected} == expected
    assert phrase in render_text(render_graph_log, LogReport.model_validate(payload))


@pytest.mark.parametrize(
    ("prepare", "kwargs", "expected"),
    [
        (
            _nothing_recorded,
            {},
            {"filtered": False, "narrowed_by": [], "hidden_count": 0},
        ),
        (
            _a_window_that_matched_nothing,
            {"since": "2h"},
            {"filtered": True, "narrowed_by": ["since"], "hidden_count": 0},
        ),
        (
            _rows_the_view_hides,
            {},
            {"filtered": False, "narrowed_by": [], "hidden_count": 1},
        ),
    ],
    ids=["nothing recorded", "a window that matched nothing", "rows the view hides"],
)
def test_the_tool_reports_the_same_three_causes(
    refine_repo: Path,
    prepare: Callable[[Path], None],
    kwargs: dict[str, object],
    expected: dict[str, object],
) -> None:
    """The agent-facing half. `filtered: false` with `hidden_count: 0` is the only shape that
    means "nothing is recorded", and the tool docstring promises exactly that."""
    prepare(refine_repo)
    payload = tool_log(refine_repo, **kwargs)
    assert payload["runs"] == []
    assert {key: payload[key] for key in expected} == expected


def test_a_window_that_missed_outranks_the_hiding_the_view_does_anyway(
    refine_repo: Path,
):
    """Both causes are live at once: the window emptied the page and the view is also hiding a
    row. Naming the hiding sends a reader to `--skipped`, which reveals nothing they asked for."""
    add_observer_run(refine_repo, status=RunStatus.SUCCEEDED, age_seconds=30 * 86400)
    add_observer_run(refine_repo, status=RunStatus.SKIPPED, age_seconds=0)
    payload = cli_json(
        runner.invoke(
            app, ["graph", "log", str(refine_repo), "--since", "2h", "--json"]
        )
    )
    assert payload["runs"] == []
    assert payload["narrowed_by"] == ["since"]
    assert payload["hidden_count"] == 1
    printed = render_text(render_graph_log, LogReport.model_validate(payload))
    assert "nothing matched --since" in printed
    assert "hidden" not in printed


def test_a_page_at_the_cap_does_not_advise_a_limit_the_cli_would_refuse(
    logged_repo: Path,
):
    """`--limit` is bounded at `MAX_LOG_ROWS`, so "raise --limit for more" on a page that size is
    advice to a usage error."""
    row = RunRowPayload.of(Run(repo_identity="/repo/.git", started_at=_STARTED))
    capped = LogReport(
        view=LogView.RUNS,
        runs=(row,) * MAX_LOG_ROWS,
        run_count=MAX_LOG_ROWS + 1,
        truncated=True,
    )
    at_cap = render_text(render_graph_log, capped)
    assert f"{MAX_LOG_ROWS} is the cap" in at_cap
    assert "raise --limit" not in at_cap
    under = LogReport(
        view=LogView.RUNS, runs=(row,), run_count=MAX_LOG_ROWS, truncated=True
    )
    assert "raise --limit for more" in render_text(render_graph_log, under)


def test_a_failed_run_marks_its_reason_apart_from_a_summary(logged_repo: Path):
    """Two sources share the `summary` column, and an unmarked failure reason reads as one."""
    report = LogReport(
        view=LogView.RUNS,
        runs=(
            RunRowPayload.of(
                Run(
                    repo_identity="/repo/.git",
                    status=RunStatus.FAILED,
                    error="the runner died",
                    started_at=_STARTED,
                )
            ),
        ),
        run_count=1,
    )
    out = render_text(render_graph_log, report, color=True)
    assert "\x1b[31mthe runner died" in out  # red, where a real summary is unstyled


def test_the_empty_line_reads_the_hidden_set_rather_than_naming_one(logged_repo: Path):
    """Hardcoding "skipped" here survived the whole suite: only the emptiness of the hidden set
    was ever asserted, never its contents."""
    page = LogReport(
        hidden_statuses=(RunStatus.QUEUED, RunStatus.FAILED), hidden_count=4
    )
    printed = render_text(render_graph_log, page)
    assert "4 queued, failed runs hidden" in printed
    assert "skipped run" not in printed


def test_skipped_is_refused_in_the_refinements_view(logged_repo: Path):
    """Its sibling `--status` is validated against the view; this one was inert there, so a page
    that answered nothing different looked like one that had."""
    result = runner.invoke(
        app, ["graph", "log", str(logged_repo), "--refinements", "--skipped"]
    )
    assert result.exit_code != 0
    printed = one_line(result.output)
    assert "skipped applies to the runs view only" in printed
    assert "status, since, limit" in printed


def test_skipped_is_accepted_in_the_runs_view(logged_repo: Path):
    result = runner.invoke(
        app, ["graph", "log", str(logged_repo), "--skipped", "--json"]
    )
    assert result.exit_code == 0


#: two epochs three days apart, so a `when` cell reading the wrong one is a different string
_STARTED = 1755000000.0
_FINISHED = _STARTED + 3 * 86400


def _at(epoch: float) -> str:
    """The stamp the `when` column carries for one epoch, in local time as the renderer reads it."""
    return datetime.fromtimestamp(epoch).strftime("%m-%d %H:%M")


def test_every_run_value_is_rendered_under_its_own_header():
    """Swapping the producer and the runner, or stamping `when` from `finished_at`, left all 3494
    tests green: every asserted substring was still somewhere in the table."""
    report = LogReport(
        view=LogView.RUNS,
        runs=(
            RunRowPayload.of(
                Run(
                    repo_identity="/repo/.git",
                    producer=ProducerKind.OBSERVER,
                    runner=RunnerKind.NONE,
                    trigger_kind=TriggerKind.EDIT,
                    status=RunStatus.SUCCEEDED,
                    summary="1 committed, 2 rejected",
                    started_at=_STARTED,
                    finished_at=_FINISHED,
                ),
                refinements=RefinementCounts(committed=1, rejected=2),
            ),
        ),
        run_count=1,
    )
    out = render_text(render_graph_log, report)
    assert cells(out, "when") == [
        "when",
        "producer",
        "runner",
        "trigger",
        "status",
        "n",
        "summary",
    ]
    assert cells(out, _at(_STARTED)) == [
        _at(_STARTED),
        "observer",
        "none",
        "edit",
        "succeeded",
        "3",
        "1 committed, 2 rejected",
    ]
    assert _at(_FINISHED) not in out


def test_every_refinement_value_is_rendered_under_its_own_header():
    """The kind and the tier swapped invisibly here too, and this view dropped the note row its
    sibling `graph refinements list` prints for the same row."""
    report = LogReport(
        view=LogView.REFINEMENTS,
        refinements=(
            RefinementRowPayload(
                refinement_id=3,
                run_id="r1",
                kind=RefinementKind.ADD_EDGE,
                tier=Tier.B,
                status=RefinementStatus.PENDING,
                src="caller.py::main",
                dst="helper.py::read_event",
                edge_kind=EdgeKind.CALLS,
                name="read_event",
                reason="main calls read_event",
                drifted=True,
                created_at=_STARTED,
            ),
        ),
        refinement_count=1,
    )
    out = render_text(render_graph_log, report)
    assert cells(out, "when") == ["when", "id", "kind", "tier", "status", "target"]
    assert cells(out, _at(_STARTED)) == [
        _at(_STARTED),
        "3",
        "add_edge",
        "B",
        "pending",
        "caller.py::main -> helper.py::read_event",
    ]
    assert "drifted" in out
    assert "main calls read_event" in out


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


def _declined(*files: str, reason: str, **over) -> RunRowPayload:
    """One assessment-only run row on the wire, the way `decline` writes it."""
    return RunRowPayload.of(
        Run(
            repo_identity="/repo/.git",
            producer=ProducerKind.OBSERVER,
            runner=RunnerKind.NONE,
            trigger_kind=TriggerKind.EDIT,
            status=RunStatus.SKIPPED,
            started_at=_STARTED,
            trigger_detail=TriggerDetail(
                files=files,
                assessment=Assessment(
                    files=files,
                    verdict=Decision(reason=reason),
                    **over,
                ),
            ),
        )
    )


def _page(*runs: RunRowPayload) -> LogReport:
    return LogReport(view=LogView.RUNS, runs=runs, run_count=len(runs))


def test_the_log_shows_an_assessment_row_with_its_own_line():
    """Spec 8.6's sentence: what it looked at, what it found, what it did."""
    out = render_text(
        render_graph_log, _page(_declined("m.py", reason="no structural change"))
    )
    assert "looked at m.py: no structural change, skipped" in one_line(out)


def test_the_log_caps_the_files_it_names_and_says_how_many_it_dropped():
    row = _declined("a.py", "b.py", "c.py", "d.py", "e.py", reason="no new questions")
    out = render_text(render_graph_log, _page(row), width=200)
    assert "looked at a.py, b.py, c.py +2 more: no new questions, skipped" in one_line(
        out
    )


def test_a_run_row_with_no_assessment_gains_no_note_line():
    """Stranded and evicted runs are `skipped` too, and their reason is in `error` (drift 3)."""
    evicted = RunRowPayload.of(
        Run(
            repo_identity="/repo/.git",
            status=RunStatus.SKIPPED,
            error="evicted: registry full",
            started_at=_STARTED,
        )
    )
    assert "looked at" not in render_text(render_graph_log, _page(evicted))


def test_the_json_row_carries_the_assessment_counts_not_its_ids():
    row = _declined(
        "m.py",
        reason="no structural change",
        new_pairs=(NodePair(node_id="m.py::Store.get", name="widen"),),
    )
    detail = _page(row).model_dump(mode="json")["runs"][0]["trigger_detail"]
    assert detail["files"] == ["m.py"]
    assert detail["file_count"] == 1
    assert detail["assessment"]["verdict"] == {
        "decision": "skip",
        "reason": "no structural change",
    }
    assert detail["assessment"]["new_pairs"] == 1
    assert "node_id" not in json.dumps(detail)


def test_a_pre_assessment_run_row_still_renders():
    """Every row written before this slice decodes `trigger_detail` to `assessment=None`."""
    row = RunRowPayload.of(Run(repo_identity="/repo/.git", started_at=_STARTED))
    payload = _page(row).model_dump(mode="json")
    assert payload["runs"][0]["trigger_detail"]["assessment"] is None
    assert "looked at" not in render_text(render_graph_log, _page(row))
