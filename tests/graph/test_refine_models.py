"""The refinement records themselves: what a kind's target must name, what a fresh row's two
timestamps say, and what `frozen=True` really reaches."""

import pytest
from pydantic import ValidationError

from auditor.graph.model import EdgeKind
from auditor.graph.refine.models import (
    STORED_ROW,
    Anchor,
    Assessment,
    AssessmentDecision,
    Decision,
    NodePair,
    Proposal,
    Refinement,
    RefinementKind,
    RefinementPayload,
    RefinementStatus,
    RefinementTarget,
    Run,
    RunStatus,
    Tier,
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
        **{
            "target": target,
            "payload": payload,
            "reason": "judged by hand",
            **kw,
        },
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


def test_a_cluster_label_must_be_a_name_a_reader_recognises():
    with pytest.raises(ValidationError, match="label"):
        Proposal(
            kind=RefinementKind.RELABEL_CLUSTER,
            target=RefinementTarget(members=("m.py::a",)),
            payload=RefinementPayload(label="x"),
            reason="too short",
        )
    with pytest.raises(ValidationError, match="label"):
        Proposal(
            kind=RefinementKind.RELABEL_CLUSTER,
            target=RefinementTarget(members=("m.py::a",)),
            payload=RefinementPayload(label="cluster-7"),
            reason="that is the fallback label, not a name",
        )


def test_an_annotation_is_capped_at_280_characters():
    with pytest.raises(ValidationError, match="annotation"):
        Proposal(
            kind=RefinementKind.ANNOTATE_NODE,
            target=RefinementTarget(node_id="m.py::a"),
            payload=RefinementPayload(annotation="x" * 281),
            reason="too long",
        )


def test_a_stored_row_reads_back_even_when_it_predates_the_text_rules():
    """`_refinement_from_row` re-validates every row on every read, and the overlay reads them on
    every build, so a rule added after a row was written must not make that row unreadable."""
    stored = Refinement.model_validate(
        {
            "run_id": "r1",
            "repo_identity": "/repo/.git",
            "kind": RefinementKind.ANNOTATE_NODE,
            "target": {"node_id": "m.py::a"},
            "payload": {"annotation": "x" * 400},
            "reason": "",
        },
        context={STORED_ROW: True},
    )
    assert len(stored.payload.annotation or "") == 400
    assert stored.reason == ""


def test_a_refinement_built_by_hand_obeys_the_same_text_rules_as_a_proposal():
    """The leniency belongs to the stored row, not to the class: without the read context an
    out-of-bounds annotation is refused exactly as `Proposal` refuses it."""
    with pytest.raises(ValidationError, match="annotation"):
        Refinement(
            run_id="r1",
            repo_identity="/repo/.git",
            kind=RefinementKind.ANNOTATE_NODE,
            target=RefinementTarget(node_id="m.py::a"),
            payload=RefinementPayload(annotation="x" * 400),
            reason="written by hand",
        )


@pytest.mark.parametrize("reason", ["", "   "])
def test_a_proposal_without_a_reason_is_refused(reason: str):
    """Spec 9.2 requires a reason everywhere, and whitespace is not one."""
    with pytest.raises(ValidationError, match="reason"):
        Proposal(
            kind=RefinementKind.ANNOTATE_NODE,
            target=RefinementTarget(node_id="m.py::a"),
            payload=RefinementPayload(annotation="the retry path"),
            reason=reason,
        )


def test_a_whitespace_label_is_not_a_name_a_reader_recognises():
    with pytest.raises(ValidationError, match="label"):
        Proposal(
            kind=RefinementKind.RELABEL_CLUSTER,
            target=RefinementTarget(members=("m.py::a",)),
            payload=RefinementPayload(label="   "),
            reason="three spaces are not two characters of name",
        )


def test_a_stored_refinement_can_be_re_proposed_into_a_new_run():
    """Spec 5.7's re-confirmation path hands the stored row back in, so only its proposal half
    may be copied."""
    stored = Refinement(
        refinement_id=41,
        run_id="r1",
        repo_identity=IDENTITY,
        kind=RefinementKind.ADD_EDGE,
        target=RefinementTarget(
            src="a.py::f", dst="b.py::g", edge_kind=EdgeKind.CALLS, name="g"
        ),
        reason="the bare call resolves in b.py",
        tier=Tier.C,
        status=RefinementStatus.ACTIVE,
    )
    again = Refinement.of(
        stored,
        run_id="r2",
        repo_identity=IDENTITY,
        tier=Tier.B,
        status=RefinementStatus.PENDING,
        supersedes=stored.refinement_id,
    )
    assert (again.run_id, again.tier, again.status) == (
        "r2",
        Tier.B,
        RefinementStatus.PENDING,
    )
    assert (again.refinement_id, again.supersedes) == (0, 41)
    assert again.target == stored.target


def test_a_refinement_is_built_from_the_proposal_it_stores():
    proposal = Proposal(
        kind=RefinementKind.ADD_EDGE,
        target=RefinementTarget(
            src="a.py::f", dst="b.py::g", edge_kind=EdgeKind.CALLS, name="g"
        ),
        reason="the bare call resolves in b.py",
        confidence=0.8,
    )
    stored = Refinement.of(
        proposal,
        run_id="r1",
        repo_identity="/repo/.git",
        tier=Tier.B,
        status=RefinementStatus.PENDING,
    )
    assert isinstance(stored, Proposal)
    assert stored.target == proposal.target
    assert (stored.run_id, stored.tier, stored.confidence) == ("r1", Tier.B, 0.8)
    assert stored.created_at > 0 and stored.status_at == stored.created_at


def test_rebasing_moves_every_id_a_proposal_names_into_the_toplevel_namespace():
    """Identity rows are toplevel relative and a caller names ids the way its own partition shows
    them (spec 5.2). `move_node` is the kind that names both a node and a member set."""
    proposal = Proposal(
        kind=RefinementKind.MOVE_NODE,
        target=RefinementTarget(node_id="a.py::f", members=("b.py::g", "c.py::h")),
        reason="f belongs with the b/c cluster",
    )
    rebased = proposal.rebased("sub/")
    assert rebased.target.node_id == "sub/a.py::f"
    assert rebased.target.members == ("sub/b.py::g", "sub/c.py::h")
    assert proposal.rebased("") is proposal


def test_rebasing_moves_a_retarget_and_the_candidate_a_choice_names():
    retarget = Proposal(
        kind=RefinementKind.RETARGET_EDGE,
        target=RefinementTarget(
            src="a.py::f",
            from_dst="b.py::g",
            to_dst="c.py::g",
            edge_kind=EdgeKind.CALLS,
            name="g",
        ),
        reason="the call resolves in c.py, not b.py",
    ).rebased("sub/")
    assert (retarget.target.src, retarget.target.from_dst, retarget.target.to_dst) == (
        "sub/a.py::f",
        "sub/b.py::g",
        "sub/c.py::g",
    )
    chosen = Proposal(
        kind=RefinementKind.RESOLVE_AMBIGUOUS,
        target=RefinementTarget(node_id="a.py::f", name="g", edge_kind=EdgeKind.CALLS),
        payload=RefinementPayload(candidate="b.py::g"),
        reason="b.py is the one this module imports",
    ).rebased("sub/")
    assert chosen.payload.candidate == "sub/b.py::g"


def test_an_anchor_rebases_its_path_the_way_it_rebases_its_node():
    """A partition prefix is a path prefix, which is why one reader answers for both halves."""
    anchor = Anchor(node_id="a.py::f", path="a.py", truth_sha="t").rebased("sub/")
    assert (anchor.node_id, anchor.path) == ("sub/a.py::f", "sub/a.py")
    assert Anchor(node_id="a.py::f", path="a.py", truth_sha="t").rebased("") == Anchor(
        node_id="a.py::f", path="a.py", truth_sha="t"
    )


def test_a_trigger_detail_round_trips_an_assessment():
    detail = TriggerDetail(
        files=("m.py",),
        assessment=Assessment(
            files=("m.py",),
            new_pairs=(NodePair(node_id="m.py::Store.get", name="widen"),),
            verdict=Decision(decision=AssessmentDecision.RUN, reason="1 new question"),
        ),
    )
    back = TriggerDetail.model_validate_json(detail.model_dump_json())
    assert back == detail
    assert back.assessment is not None
    assert back.assessment.new_pairs[0].name == "widen"


def test_a_trigger_detail_without_an_assessment_still_decodes_the_old_shape():
    """Every row written before this slice holds `{"files": [...], "reason": ""}`."""
    assert TriggerDetail.model_validate_json('{"files": ["m.py"]}').assessment is None
