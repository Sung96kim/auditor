"""Spec 9.1's commit-time conflict rules."""

import pytest

from auditor.graph.model import EdgeKind, Provenance
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
) -> dict:
    return {
        "src": src,
        "dst": dst,
        "kind": EdgeKind.CALLS.value,
        "provenance": provenance.value,
    }


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
