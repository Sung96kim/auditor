"""Spec 9.1's commit-time conflict rules."""

import pytest

from auditor.graph.model import EdgeKind, GraphEdge, Provenance
from auditor.graph.refine.conflicts import Conflict, ConflictKind, ConflictRules
from auditor.graph.refine.models import (
    Proposal,
    Refinement,
    RefinementKind,
    RefinementStatus,
    RefinementTarget,
)


def _stored(kind: RefinementKind, target: RefinementTarget, rid: int = 7) -> Refinement:
    return Refinement(
        refinement_id=rid,
        run_id="r1",
        repo_identity="/repo/.git",
        kind=kind,
        target=target,
        reason="stored earlier",
        status=RefinementStatus.ACTIVE,
    )


def _add(src="a.py::f", dst="b.py::g", name="g") -> Proposal:
    return Proposal(
        kind=RefinementKind.ADD_EDGE,
        target=RefinementTarget(src=src, dst=dst, edge_kind=EdgeKind.CALLS, name=name),
        reason="the call resolves there",
    )


def _edge(
    src: str, dst: str, provenance: Provenance = Provenance.DETERMINISTIC
) -> GraphEdge:
    return GraphEdge(src=src, dst=dst, kind=EdgeKind.CALLS, provenance=provenance)


def test_no_prior_work_is_no_conflict():
    assert ConflictRules().check(_add()) is None


def test_an_identical_active_refinement_becomes_a_confirmation():
    rules = ConflictRules.of(
        [
            _stored(
                RefinementKind.ADD_EDGE,
                RefinementTarget(
                    src="a.py::f", dst="b.py::g", edge_kind=EdgeKind.CALLS, name="g"
                ),
            )
        ],
        [],
    )
    conflict = rules.check(_add())
    assert conflict == Conflict(
        kind=ConflictKind.DUPLICATE,
        detail="refinement 7 already adds this edge",
        prior_id=7,
    )
    assert conflict.rewrite_as_confirm is True


def test_a_prior_refinement_for_another_name_is_not_a_conflict():
    """The prior refinement answers `g`; this proposal answers `h`."""
    rules = ConflictRules.of(
        [
            _stored(
                RefinementKind.RETARGET_EDGE,
                RefinementTarget(
                    src="a.py::f",
                    from_dst="b.py::g",
                    to_dst="c.py::g",
                    edge_kind=EdgeKind.CALLS,
                    name="g",
                ),
                rid=11,
            )
        ],
        [],
    )
    assert rules.check(_add(dst="d.py::h", name="h")) is None


def test_an_add_that_contradicts_a_retarget_is_rejected():
    rules = ConflictRules.of(
        [
            _stored(
                RefinementKind.RETARGET_EDGE,
                RefinementTarget(
                    src="a.py::f",
                    from_dst="b.py::g",
                    to_dst="c.py::g",
                    edge_kind=EdgeKind.CALLS,
                    name="g",
                ),
                rid=11,
            )
        ],
        [],
    )
    conflict = rules.check(_add())
    assert conflict is not None
    assert conflict.kind is ConflictKind.CONTRADICTS
    assert conflict.prior_id == 11
    assert conflict.rewrite_as_confirm is False


def test_an_add_over_a_deterministic_edge_to_another_target_is_rejected():
    rules = ConflictRules.of([], [_edge("a.py::f", "z.py::g")])
    conflict = rules.check(_add())
    assert conflict is not None
    assert conflict.kind is ConflictKind.ALREADY_RESOLVED
    assert "retarget_edge" in conflict.detail


def test_an_add_matching_the_deterministic_edge_it_names_is_redundant_not_a_confirmation():
    """Spec 5.4: the resolver already produces this edge, so the proposal is terminal. Only an
    identical *active refinement* is something to confirm."""
    rules = ConflictRules.of([], [_edge("a.py::f", "b.py::g")])
    conflict = rules.check(_add())
    assert conflict is not None
    assert conflict.kind is ConflictKind.REDUNDANT
    assert conflict.rewrite_as_confirm is False
    assert conflict.stored_status is RefinementStatus.REDUNDANT


def test_another_call_from_the_same_source_is_not_a_contradiction():
    """One source with several unplaced calls is the normal case: accepting `read_event` must not
    make `emit_context` from the same function permanently unproposable."""
    rules = ConflictRules.of(
        [
            _stored(
                RefinementKind.ADD_EDGE,
                RefinementTarget(
                    src="a.py::f", dst="b.py::g", edge_kind=EdgeKind.CALLS, name="g"
                ),
            )
        ],
        [_edge("a.py::f", "b.py::g")],
    )
    assert rules.check(_add(dst="c.py::h", name="h")) is None


def test_a_refined_edge_never_counts_as_the_deterministic_one():
    rules = ConflictRules.of([], [_edge("a.py::f", "z.py::g", Provenance.REFINED)])
    assert rules.check(_add()) is None


@pytest.mark.parametrize(
    "kind", [RefinementKind.ANNOTATE_NODE, RefinementKind.UNRESOLVABLE]
)
def test_the_node_kinds_have_no_edge_conflicts(kind: RefinementKind):
    rules = ConflictRules.of([], [_edge("a.py::f", "z.py::g")])
    proposal = Proposal(
        kind=kind,
        target=RefinementTarget(node_id="a.py::f", name="g"),
        payload={"annotation": "note", "reason_code": "dynamic"},
        reason="judged by hand",
    )
    assert rules.check(proposal) is None


def _retarget(from_dst="b.py::g", to_dst="c.py::g", name="g") -> Proposal:
    return Proposal(
        kind=RefinementKind.RETARGET_EDGE,
        target=RefinementTarget(
            src="a.py::f",
            from_dst=from_dst,
            to_dst=to_dst,
            edge_kind=EdgeKind.CALLS,
            name=name,
        ),
        reason="the resolver picked the wrong g",
    )


def test_a_retarget_naming_the_deterministic_edge_it_corrects_is_not_a_conflict():
    """Spec 9.2 requires a `retarget_edge` to name a deterministic edge in `from_dst`, and spec
    5.4 offers no other correction, so the already-resolved rule is `add_edge`'s alone."""
    rules = ConflictRules.of([], [_edge("a.py::f", "b.py::g")])
    assert rules.check(_retarget()) is None


def test_a_retarget_onto_a_second_deterministic_edge_is_still_not_a_conflict():
    rules = ConflictRules.of(
        [], [_edge("a.py::f", "b.py::g"), _edge("a.py::f", "c.py::g")]
    )
    assert rules.check(_retarget()) is None


def test_an_add_matching_the_second_of_two_deterministic_edges_is_redundant():
    """The exact destination wins over an unordered first match: the caller is told the edge is
    already there, not to retarget the edge it just proposed."""
    rules = ConflictRules.of(
        [], [_edge("a.py::f", "z.py::g"), _edge("a.py::f", "b.py::g")]
    )
    conflict = rules.check(_add())
    assert conflict is not None
    assert conflict.kind is ConflictKind.REDUNDANT
    assert conflict.stored_status is RefinementStatus.REDUNDANT


def test_an_edge_the_resolver_now_produces_is_redundant_not_a_confirmation():
    """The prior refinement placed this edge and the resolver has since caught up: spec 5.4 makes
    that terminal, so the resolver's own edges are read first."""
    rules = ConflictRules.of(
        [
            _stored(
                RefinementKind.ADD_EDGE,
                RefinementTarget(
                    src="a.py::f", dst="b.py::g", edge_kind=EdgeKind.CALLS, name="g"
                ),
            )
        ],
        [_edge("a.py::f", "b.py::g")],
    )
    conflict = rules.check(_add())
    assert conflict is not None
    assert conflict.kind is ConflictKind.REDUNDANT
    assert conflict.rewrite_as_confirm is False
