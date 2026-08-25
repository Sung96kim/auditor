"""The wire models the refinement CLI and the refinement MCP tools share."""

import time

import pytest

from auditor.graph.model import EdgeKind
from auditor.graph.payloads import (
    LogFilter,
    LogReport,
    LogView,
    RefinementRowPayload,
    RunRowPayload,
    parse_since,
)
from auditor.graph.refine.models import (
    Anchor,
    Refinement,
    RefinementKind,
    RefinementStatus,
    RefinementTarget,
    Run,
    RunStatus,
    RunUsage,
    Tier,
)


def _refinement() -> Refinement:
    return Refinement(
        refinement_id=3,
        run_id="r1",
        repo_identity="/repo/.git",
        kind=RefinementKind.ADD_EDGE,
        target=RefinementTarget(
            src="a.py::f", dst="b.py::g", edge_kind=EdgeKind.CALLS, name="g"
        ),
        reason="the bare call resolves in b.py",
        confidence=0.8,
        tier=Tier.B,
        status=RefinementStatus.PENDING,
    )


def test_a_refinement_row_carries_its_target_flat_and_its_anchors_by_id():
    payload = RefinementRowPayload.of(
        _refinement(), [Anchor(node_id="a.py::f", path="a.py", truth_sha="0" * 64)]
    )
    assert payload.refinement_id == 3
    assert (payload.src, payload.dst, payload.name) == ("a.py::f", "b.py::g", "g")
    assert payload.edge_kind is EdgeKind.CALLS
    assert payload.tier is Tier.B
    assert payload.anchors == ("a.py::f",)
    assert payload.summary == "a.py::f -> b.py::g"


def test_a_node_refinement_summarises_as_its_node():
    payload = RefinementRowPayload.of(
        Refinement(
            run_id="r1",
            repo_identity="/repo/.git",
            kind=RefinementKind.ANNOTATE_NODE,
            target=RefinementTarget(node_id="a.py::f"),
            payload={"annotation": "the retry path"},
            reason="worth a note",
        )
    )
    assert payload.summary == "a.py::f"


def test_a_run_row_carries_its_cost_its_count_and_who_made_it():
    """Invariant 2 is attributability, and `graph log --runs` is the only surface that shows a run:
    a payload that dropped the session, the agent or the checkout could not prove it."""
    run = Run(
        repo_identity="/repo/.git",
        usage=RunUsage(cost_usd=0.02, num_turns=4),
        status=RunStatus.SUCCEEDED,
        summary="1 committed, 0 rejected",
        session_id="s-1",
        agent_name="claude",
        branch="feat/x",
        commit_sha="deadbeef",
    )
    payload = RunRowPayload.of(run, refinements=2)
    assert (payload.cost_usd, payload.num_turns, payload.refinements) == (0.02, 4, 2)
    assert payload.status is RunStatus.SUCCEEDED
    assert (payload.session_id, payload.agent_name) == ("s-1", "claude")
    assert (payload.branch, payload.commit_sha) == ("feat/x", "deadbeef")


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [("45m", 2700), ("2h", 7200), ("7d", 604800), ("90s", 90)],
)
def test_a_duration_since_is_a_cutoff_in_the_past(raw: str, seconds: int):
    now = 1_000_000.0
    assert parse_since(raw, now=now) == pytest.approx(now - seconds)


@pytest.mark.parametrize("raw", ["2026-08-20", "2026-08-20T14:00:00"])
def test_an_iso_since_is_that_instant(raw: str):
    assert parse_since(raw) < time.time()


@pytest.mark.parametrize("raw", ["yesterday", "2h30m", "", "7w", "2026-13-01"])
def test_an_unparseable_since_names_what_is_accepted(raw: str):
    with pytest.raises(ValueError, match="90s, 45m, 2h, 7d"):
        parse_since(raw)


def test_the_log_filter_validates_status_against_the_view_it_shows():
    runs = LogFilter.of(
        view="runs", status=["succeeded"], since=None, skipped=False, limit=10
    )
    assert runs.view is LogView.RUNS
    assert [s.value for s in runs.run_statuses or []] == ["succeeded"]
    assert runs.refinement_statuses is None
    assert runs.filtered is True

    refinements = LogFilter.of(
        view="refinements", status=["active"], since=None, skipped=False, limit=10
    )
    assert [s.value for s in refinements.refinement_statuses or []] == ["active"]
    assert refinements.run_statuses is None


def test_a_status_the_view_does_not_own_is_an_error():
    with pytest.raises(ValueError, match="unknown status"):
        LogFilter.of(
            view="runs", status=["active"], since=None, skipped=False, limit=10
        )
    with pytest.raises(ValueError, match="unknown status"):
        LogFilter.of(
            view="refinements",
            status=["succeeded"],
            since=None,
            skipped=False,
            limit=10,
        )


def test_an_unknown_view_is_an_error():
    with pytest.raises(ValueError, match="unknown view"):
        LogFilter.of(view="tuning", status=None, since=None, skipped=False, limit=10)


def test_skipped_runs_are_out_of_the_default_view():
    default = LogFilter.of(
        view="runs", status=None, since=None, skipped=False, limit=10
    )
    assert default.run_statuses is None
    assert default.excluded_run_statuses == (RunStatus.SKIPPED,)
    assert default.filtered is False
    everything = LogFilter.of(
        view="runs", status=None, since=None, skipped=True, limit=10
    )
    assert everything.run_statuses is None
    assert everything.excluded_run_statuses == ()


def test_the_log_report_is_built_from_the_rows_it_was_given():
    """`LogReport` is a wire model: it holds rows a store already fetched, the way every other
    `of()` in this module does. `LogQuery` is what reads the database."""
    spec = LogFilter.of(
        view="refinements", status=["pending"], since=None, skipped=False, limit=10
    )
    report = LogReport.of(spec, refinements=[RefinementRowPayload.of(_refinement())])
    assert report.view is LogView.REFINEMENTS
    assert [r.refinement_id for r in report.refinements] == [3]
    assert report.runs == ()
    assert report.filtered is True
