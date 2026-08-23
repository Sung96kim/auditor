"""The refinement records themselves: what a kind's target must name, what a fresh row's two
timestamps say, and what `frozen=True` really reaches."""

import pytest
from pydantic import ValidationError

from auditor.graph.model import EdgeKind
from auditor.graph.refine.models import (
    Refinement,
    RefinementKind,
    RefinementPayload,
    RefinementStatus,
    RefinementTarget,
    Run,
    RunStatus,
    ToolCall,
    TriggerDetail,
    TuningStatus,
)

IDENTITY = "/checkout/.git"

#: one valid (target, payload) pair per kind, in the shape the build overlay reads
_SHAPES: dict[RefinementKind, tuple[RefinementTarget, RefinementPayload]] = {
    RefinementKind.ADD_EDGE: (
        RefinementTarget(
            src="m.py::f", dst="s.py::g", edge_kind=EdgeKind.CALLS, name="g"
        ),
        RefinementPayload(),
    ),
    RefinementKind.RETARGET_EDGE: (
        RefinementTarget(
            src="m.py::f",
            from_dst="a.py::g",
            to_dst="s.py::g",
            edge_kind=EdgeKind.CALLS,
            name="g",
        ),
        RefinementPayload(),
    ),
    RefinementKind.CONFIRM_EDGE: (
        RefinementTarget(
            src="m.py::f", dst="s.py::g", edge_kind=EdgeKind.CALLS, name="g"
        ),
        RefinementPayload(),
    ),
    RefinementKind.RESOLVE_AMBIGUOUS: (
        RefinementTarget(node_id="m.py::f", name="g", edge_kind=EdgeKind.CALLS),
        RefinementPayload(candidate="s.py::g"),
    ),
    RefinementKind.RELABEL_CLUSTER: (
        RefinementTarget(members=("m.py::f", "s.py::g")),
        RefinementPayload(label="ingest"),
    ),
    RefinementKind.MOVE_NODE: (
        RefinementTarget(node_id="m.py::f", members=("s.py::g",)),
        RefinementPayload(),
    ),
    RefinementKind.ANNOTATE_NODE: (
        RefinementTarget(node_id="m.py::f"),
        RefinementPayload(annotation="entry point"),
    ),
    RefinementKind.UNRESOLVABLE: (
        RefinementTarget(node_id="m.py::f", name="g"),
        RefinementPayload(),
    ),
}


def _refinement(kind: RefinementKind, **kw) -> Refinement:
    target, payload = _SHAPES[kind]
    return Refinement(
        run_id="run-1",
        repo_identity=IDENTITY,
        kind=kind,
        **{"target": target, "payload": payload, **kw},
    )


@pytest.mark.parametrize("kind", list(RefinementKind))
def test_every_kind_accepts_its_own_shape(kind):
    """`_REQUIRED_BY_KIND` has to stay total: a new kind with no entry raises a KeyError here."""
    assert _refinement(kind).kind is kind


@pytest.mark.parametrize("kind", list(RefinementKind))
def test_every_kind_rejects_an_empty_target(kind):
    """A target no build could apply is worse stored than refused: nothing downstream reports a
    proposal that names no destination, it just never applies."""
    with pytest.raises(ValidationError, match="is missing"):
        _refinement(kind, target=RefinementTarget(), payload=RefinementPayload())


def test_an_edge_kind_needs_the_name_it_answers():
    """Without `name` a build cannot retire the `graph_unresolved` row the proposal answers, so
    the same question is re-briefed for ever (spec 5.7)."""
    target, _ = _SHAPES[RefinementKind.ADD_EDGE]
    with pytest.raises(ValidationError, match="name"):
        _refinement(
            RefinementKind.ADD_EDGE, target=target.model_copy(update={"name": None})
        )


def test_an_edge_kind_no_proposal_may_name_is_refused():
    """A15: spec 9.2 names five structural kinds. The overlay's collision index is built from
    structural edges only, so a similarity kind would collapse a real row; refuse it on the way in
    and the overlay's own branch is defence in depth."""
    target, _ = _SHAPES[RefinementKind.ADD_EDGE]
    with pytest.raises(ValidationError, match="name_similar"):
        _refinement(
            RefinementKind.ADD_EDGE,
            target=target.model_copy(update={"edge_kind": EdgeKind.NAME_SIMILAR}),
        )


@pytest.mark.parametrize(
    "kind, update",
    [
        (RefinementKind.ADD_EDGE, {"dst": "m.py::f"}),
        (RefinementKind.RETARGET_EDGE, {"to_dst": "m.py::f"}),
    ],
    ids=["add_edge", "retarget_edge"],
)
def test_a_self_edge_is_refused(kind, update):
    """A15: a cheap guard on the way in; S5's verifier stays the real gate."""
    target, _ = _SHAPES[kind]
    with pytest.raises(ValidationError, match="itself"):
        _refinement(kind, target=target.model_copy(update={"src": "m.py::f", **update}))


def test_a_fresh_refinement_carries_one_timestamp():
    """Two independent `time.time()` defaults disagreed about one construction in six, and
    `status_at` is what the staleness sweep reads as "has it moved?"."""
    for _ in range(1000):
        refinement = _refinement(RefinementKind.ADD_EDGE)
        assert refinement.status_at == refinement.created_at


def test_an_explicit_status_at_survives():
    assert (
        _refinement(
            RefinementKind.ADD_EDGE,
            created_at=100.0,
            status_at=140.0,
            status=RefinementStatus.STALE,
        ).status_at
        == 140.0
    )


def test_the_tuning_statuses_are_a_subset_of_the_refinement_ones():
    """The docstring's claim, checked rather than left to a reader diffing two enums by eye."""
    assert {s.value for s in TuningStatus} <= {s.value for s in RefinementStatus}


def test_a_run_is_frozen_all_the_way_down():
    """`frozen=True` blocks attribute assignment but not mutation of a `dict` or `list` field, so
    the JSON columns are models and tuples rather than raw containers."""
    run = Run(
        repo_identity=IDENTITY,
        status=RunStatus.RUNNING,
        trigger_detail=TriggerDetail(files=("m.py",)),
        tool_trace=(ToolCall(tool="Read"),),
    )
    assert isinstance(run.trigger_detail.files, tuple)
    assert isinstance(run.tool_trace, tuple)
    with pytest.raises(AttributeError):
        run.trigger_detail.files.append("other.py")  # a tuple has no append
    with pytest.raises(ValidationError):
        run.tool_trace[0].tool = "Write"
