"""The wire models the refinement CLI and the refinement MCP tools share."""

from datetime import datetime

import pytest

from auditor.graph.model import MAX_LOG_ROWS, EdgeKind, row_limit
from auditor.graph.payloads import (
    LogFilter,
    LogReport,
    LogView,
    RefinementRowPayload,
    RefinementsReport,
    RunRowPayload,
    parse_since,
)
from auditor.graph.refine.models import (
    Anchor,
    Refinement,
    RefinementCounts,
    RefinementKind,
    RefinementPayload,
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


def _shaped(
    kind: RefinementKind, target: RefinementTarget, **kw
) -> RefinementRowPayload:
    """One row of a given kind, for the target shapes spec 5.4 allows besides a plain edge."""
    return RefinementRowPayload.of(
        Refinement(
            run_id="r1",
            repo_identity="/repo/.git",
            kind=kind,
            target=target,
            reason="because",
            **kw,
        )
    )


def test_a_retarget_row_shows_the_edge_it_replaces():
    """`retarget_edge` lands pending and a human accepts it: shown only its destination, the one
    thing being decided, which edge it takes away, was invisible."""
    row = _shaped(
        RefinementKind.RETARGET_EDGE,
        RefinementTarget(
            src="a.py::f",
            from_dst="b.py::g",
            to_dst="c.py::g",
            edge_kind=EdgeKind.CALLS,
            name="g",
        ),
    )
    assert (row.from_dst, row.dst) == ("b.py::g", "c.py::g")
    assert row.summary == "a.py::f: b.py::g -> c.py::g"


def test_a_move_row_shows_where_the_node_goes():
    """`move_node` lands pending too, and its whole content is the cluster it moves into."""
    row = _shaped(
        RefinementKind.MOVE_NODE,
        RefinementTarget(node_id="a.py::f", members=("b.py::g", "c.py::h")),
    )
    assert row.members == ("b.py::g", "c.py::h")
    assert row.summary == "a.py::f -> b.py::g, c.py::h"


def test_a_cluster_row_shows_its_members_and_the_label_it_proposes():
    """The label is the proposal. Carried in `payload`, so a reader does not have to know which of
    five names this kind fills in."""
    row = _shaped(
        RefinementKind.RELABEL_CLUSTER,
        RefinementTarget(members=("a.py::f", "b.py::g")),
        payload=RefinementPayload(label="user lookup"),
    )
    assert row.payload.label == "user lookup"
    assert row.summary == "a.py::f, b.py::g"


@pytest.mark.parametrize(
    ("asked", "capped"), [(0, 1), (-3, 1), (10, 10), (10_000_000, MAX_LOG_ROWS)]
)
def test_a_page_size_is_bounded_at_both_ends(asked: int, capped: int):
    """One bound, both surfaces: `graph_refinements(limit=-3)` answered one row and
    `limit=10_000_000` pulled the whole table into a caller's context."""
    assert row_limit(asked) == capped
    spec = LogFilter.of(
        view="runs", status=None, since=None, skipped=False, limit=asked
    )
    assert spec.limit == capped


def test_a_page_at_the_cap_says_how_much_it_left():
    """A page that filled up and a complete list are the same list, unless the total says
    otherwise."""
    rows = [RefinementRowPayload.of(_refinement())]
    assert RefinementsReport.of(rows, filtered=False, total=9).truncated is True
    assert RefinementsReport.of(rows, filtered=False, total=1).truncated is False


def test_a_run_row_carries_its_cost_its_count_and_who_made_it():
    """Invariant 2 is attributability, and `graph log --runs` is the only surface that shows a run:
    a payload that dropped the session, the agent or the checkout could not prove it.

    `cost_estimated` travels with the price because every Codex price is modelled, and a column
    that showed the number alone would present a model's guess as a measurement.
    """
    run = Run(
        repo_identity="/repo/.git",
        usage=RunUsage(cost_usd=0.02, num_turns=4, cost_estimated=True),
        status=RunStatus.SUCCEEDED,
        summary="1 committed, 0 rejected",
        session_id="s-1",
        agent_name="claude",
        branch="feat/x",
        commit_sha="deadbeef",
    )
    payload = RunRowPayload.of(
        run, refinements=RefinementCounts(committed=2, rejected=3)
    )
    assert (payload.cost_usd, payload.num_turns) == (0.02, 4)
    assert (payload.refinements.committed, payload.refinements.rejected) == (2, 3)
    assert payload.cost_estimated is True
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


@pytest.mark.parametrize(
    ("raw", "stamp"),
    [
        ("2026-08-20", datetime(2026, 8, 20).timestamp()),
        ("2026-08-20T14:00:00", datetime(2026, 8, 20, 14).timestamp()),
    ],
)
def test_an_iso_since_is_that_instant(raw: str, stamp: float):
    """The instant itself, not merely "in the past": a `parse_since` that answered 0.0 passed."""
    assert parse_since(raw) == stamp


@pytest.mark.parametrize("raw", ["yesterday", "2h30m", "", "7w", "2026-13-01"])
def test_an_unparseable_since_names_what_is_accepted(raw: str):
    with pytest.raises(ValueError, match="90s, 45m, 2h, 7d"):
        parse_since(raw)


@pytest.mark.parametrize("raw", ["", "yesterday"])
def test_a_since_the_caller_gave_is_always_validated(raw: str):
    """An empty string is a caller that thinks it set a window, not one that set none: it used to
    reach the filter as "no window" while the parser's own test pinned it as an error."""
    with pytest.raises(ValueError, match="90s, 45m, 2h, 7d"):
        LogFilter.of(view="runs", status=None, since=raw, skipped=False, limit=10)


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


def test_skipped_runs_are_out_of_the_default_view_and_that_counts_as_filtered():
    """`filtered` is what tells "nothing matched" from "nothing recorded", so a default view that
    hides every skipped run has to say so: a repo whose only runs were skipped answered
    `runs: [], filtered: false`, which reads as a repo that has never run anything."""
    default = LogFilter.of(
        view="runs", status=None, since=None, skipped=False, limit=10
    )
    assert default.run_statuses is None
    assert default.excluded_run_statuses == (RunStatus.SKIPPED,)
    assert default.filtered is True
    everything = LogFilter.of(
        view="runs", status=None, since=None, skipped=True, limit=10
    )
    assert everything.run_statuses is None
    assert everything.excluded_run_statuses == ()
    assert everything.filtered is False


def test_the_log_report_is_built_from_the_rows_it_was_given():
    """`LogReport` is a wire model: it holds rows a store already fetched, the way every other
    `of()` in this module does. `LogQuery` is what reads the database."""
    spec = LogFilter.of(
        view="refinements", status=["pending"], since=None, skipped=False, limit=10
    )
    report = LogReport.of(
        spec, refinements=[RefinementRowPayload.of(_refinement())], total=4
    )
    assert report.view is LogView.REFINEMENTS
    assert [r.refinement_id for r in report.refinements] == [3]
    assert report.runs == ()
    assert report.filtered is True
    assert (report.refinement_count, report.run_count) == (4, 0)
    assert report.truncated is True
