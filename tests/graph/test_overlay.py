"""The pure merge: what an active refinement does to a build's edges, nodes and clusters, and what
status it earns for trying. No database anywhere in this module."""

import pytest
from pydantic import ValidationError

from auditor.graph.model import (
    CallForm,
    EdgeKind,
    FactKind,
    GraphCluster,
    GraphEdge,
    GraphNode,
    NodeKind,
    Provenance,
    UnresolvedReason,
    UnresolvedRow,
)
from auditor.graph.refine.models import (
    Anchor,
    Refinement,
    RefinementKind,
    RefinementOutcome,
    RefinementPayload,
    RefinementStatus,
    RefinementTarget,
)
from auditor.graph.refine.overlay import (
    apply_edge_overlay,
    apply_node_overlay,
    merge_outcomes,
    retire_queue_rows,
    triage,
)

NODES = {"m.py::f", "s.py::g", "s.py::h"}
BASE_EDGE = GraphEdge(src="m.py::f", dst="s.py::h", kind=EdgeKind.CALLS)


def _node(node_id: str, **kw) -> GraphNode:
    return GraphNode(
        id=node_id,
        kind=NodeKind.FUNCTION,
        name=node_id.split("::")[-1],
        module=node_id.split("::")[0],
        qualname=node_id.split("::")[-1],
        **kw,
    )


def _ref(rid: int = 1, **kw) -> Refinement:
    """One refinement in its kind's valid shape: `Refinement` refuses a target that does not name
    what `_REQUIRED_BY_KIND` asks for, so every target below is complete by construction."""
    return Refinement(
        refinement_id=rid,
        run_id="run-1",
        repo_identity="/x/.git",
        kind=kw.pop("kind", RefinementKind.ADD_EDGE),
        target=kw.pop(
            "target",
            RefinementTarget(
                src="m.py::f", dst="s.py::g", edge_kind=EdgeKind.CALLS, name="g"
            ),
        ),
        status=kw.pop("status", RefinementStatus.ACTIVE),
        **kw,
    )


def _triaged(*refs, anchors=None, node_truth=None, prefix=""):
    return triage(refs, anchors or {}, node_truth or {}, prefix)


def test_zero_refinements_leave_everything_alone():
    triaged = _triaged()
    edges = apply_edge_overlay([BASE_EDGE], NODES, triaged, "")
    assert edges.edges == (BASE_EDGE,)
    assert edges.outcomes == ()
    nodes = apply_node_overlay([_node("m.py::f")], [], triaged, "")
    assert nodes.nodes == (_node("m.py::f"),)
    assert nodes.clusters == ()


def test_an_add_edge_appends_a_refined_edge():
    result = apply_edge_overlay([BASE_EDGE], NODES, _triaged(_ref()), "")
    added = [e for e in result.edges if e.dst == "s.py::g"]
    assert len(added) == 1
    assert added[0].provenance is Provenance.REFINED
    assert added[0].kind is EdgeKind.CALLS
    assert BASE_EDGE in result.edges  # the deterministic set is untouched
    assert result.outcomes[0].applied is True
    assert result.outcomes[0].status is None


def test_an_edge_the_resolver_now_produces_goes_redundant():
    deterministic = GraphEdge(src="m.py::f", dst="s.py::g", kind=EdgeKind.CALLS)
    result = apply_edge_overlay([deterministic], NODES, _triaged(_ref()), "")
    assert result.edges == (deterministic,)  # no duplicate, no provenance change
    assert result.outcomes[0].status is RefinementStatus.REDUNDANT


def test_an_add_edge_to_a_vanished_node_goes_stale():
    result = apply_edge_overlay([BASE_EDGE], {"m.py::f"}, _triaged(_ref()), "")
    assert len(result.edges) == 1
    assert result.outcomes[0].status is RefinementStatus.STALE


def test_an_edge_kind_no_proposal_may_name_goes_stale():
    """Spec 9.2 lets a proposal name five structural kinds. The overlay's collision index is built
    from structural edges only, so a similarity kind would slip past it and collapse a real row."""
    ref = _ref(
        target=RefinementTarget(
            src="m.py::f", dst="s.py::g", edge_kind=EdgeKind.NAME_SIMILAR, name="g"
        )
    )
    result = apply_edge_overlay([BASE_EDGE], NODES, _triaged(ref), "")
    assert result.edges == (BASE_EDGE,)
    assert result.outcomes[0].status is RefinementStatus.STALE


def test_resolve_ambiguous_reads_the_node_id_and_the_chosen_candidate():
    """Spec 9.2's shape is `(node_id, name, candidate_id)`; there is no `src`/`dst` on it."""
    ref = _ref(
        kind=RefinementKind.RESOLVE_AMBIGUOUS,
        target=RefinementTarget(node_id="m.py::f", name="g", edge_kind=EdgeKind.CALLS),
        payload=RefinementPayload(candidate="s.py::g"),
    )
    result = apply_edge_overlay([], NODES, _triaged(ref), "")
    assert [(e.src, e.dst, e.provenance) for e in result.edges] == [
        ("m.py::f", "s.py::g", Provenance.REFINED)
    ]
    assert result.outcomes[0].applied is True


def test_a_retarget_moves_one_edge_and_keeps_the_rest():
    ref = _ref(
        kind=RefinementKind.RETARGET_EDGE,
        target=RefinementTarget(
            src="m.py::f",
            edge_kind=EdgeKind.CALLS,
            from_dst="s.py::h",
            to_dst="s.py::g",
            name="h",
        ),
    )
    other = GraphEdge(src="s.py::g", dst="s.py::h", kind=EdgeKind.CALLS)
    result = apply_edge_overlay([BASE_EDGE, other], NODES, _triaged(ref), "")
    pairs = {(e.src, e.dst, e.provenance) for e in result.edges}
    assert ("m.py::f", "s.py::h", Provenance.DETERMINISTIC) not in pairs
    assert ("m.py::f", "s.py::g", Provenance.REFINED) in pairs
    assert ("s.py::g", "s.py::h", Provenance.DETERMINISTIC) in pairs


def test_a_retarget_with_no_edge_to_move_is_a_noop():
    ref = _ref(
        kind=RefinementKind.RETARGET_EDGE,
        noop_builds=1,
        target=RefinementTarget(
            src="m.py::f",
            edge_kind=EdgeKind.CALLS,
            from_dst="s.py::g",
            to_dst="s.py::h",
            name="g",
        ),
    )
    result = apply_edge_overlay([], NODES, _triaged(ref), "")
    assert result.outcomes[0].noop_builds == 2
    assert result.outcomes[0].status is None


def test_a_retarget_to_a_vanished_node_never_removes_the_old_edge():
    """The pop has to happen after the shared stale check, or a retarget whose new destination is
    gone deletes a deterministic edge and replaces it with nothing."""
    ref = _ref(
        kind=RefinementKind.RETARGET_EDGE,
        target=RefinementTarget(
            src="m.py::f",
            edge_kind=EdgeKind.CALLS,
            from_dst="s.py::h",
            to_dst="s.py::gone",
            name="h",
        ),
    )
    result = apply_edge_overlay([BASE_EDGE], NODES, _triaged(ref), "")
    assert result.edges == (BASE_EDGE,)
    assert result.outcomes[0].status is RefinementStatus.STALE


def test_a_half_specified_target_cannot_be_stored_at_all():
    """`Refinement` validates the target against its kind, so the overlay never sees a
    `retarget_edge` with nothing to retarget to."""
    with pytest.raises(ValidationError):
        _ref(
            kind=RefinementKind.RETARGET_EDGE,
            target=RefinementTarget(
                src="m.py::f", edge_kind=EdgeKind.CALLS, from_dst="s.py::h", name="h"
            ),
        )


def test_three_consecutive_noops_stale_the_refinement():
    ref = _ref(kind=RefinementKind.CONFIRM_EDGE, noop_builds=2)
    result = apply_edge_overlay([], NODES, _triaged(ref), "")
    assert result.outcomes[0].noop_builds == 3
    assert result.outcomes[0].status is RefinementStatus.STALE


def test_an_effective_build_resets_the_noop_counter():
    result = apply_edge_overlay([BASE_EDGE], NODES, _triaged(_ref(noop_builds=2)), "")
    assert result.outcomes[0].noop_builds == 0


def test_confirm_edge_tags_the_deterministic_edge_without_reprovenancing_it():
    ref = _ref(
        kind=RefinementKind.CONFIRM_EDGE,
        target=RefinementTarget(
            src="m.py::f", dst="s.py::h", edge_kind=EdgeKind.CALLS, name="h"
        ),
    )
    result = apply_edge_overlay([BASE_EDGE], NODES, _triaged(ref), "")
    (edge,) = result.edges
    assert edge.confirmed is True
    assert edge.provenance is Provenance.DETERMINISTIC  # provenance, not structure


def test_a_broken_anchor_stales_before_anything_is_applied():
    anchors = {
        1: (Anchor(refinement_id=1, node_id="m.py::f", path="m.py", truth_sha="old"),)
    }
    triaged = triage([_ref()], anchors, {"m.py::f": "new"}, "")
    assert triaged.kept == ()
    assert triaged.outcomes[0].status is RefinementStatus.STALE


def test_a_matching_anchor_is_kept():
    anchors = {
        1: (Anchor(refinement_id=1, node_id="m.py::f", path="m.py", truth_sha="t1"),)
    }
    triaged = triage([_ref()], anchors, {"m.py::f": "t1"}, "")
    assert [r.refinement_id for r in triaged.kept] == [1]
    assert triaged.drifted == frozenset()
    assert triaged.outcomes == ()


def test_a_pinned_refinement_with_a_broken_anchor_is_kept_and_marked_drifted():
    anchors = {
        1: (Anchor(refinement_id=1, node_id="m.py::f", path="m.py", truth_sha="old"),)
    }
    triaged = triage(
        [_ref(status=RefinementStatus.PINNED)], anchors, {"m.py::f": "new"}, ""
    )
    assert [r.refinement_id for r in triaged.kept] == [1]
    assert triaged.drifted == frozenset({1})
    assert all(o.status is None for o in triaged.outcomes)
    assert triaged.outcomes[0].drifted is True


def test_a_pinned_kind_with_no_graph_effect_still_records_its_drift():
    """Spec 5.7 says a pinned refinement's drift is surfaced. `unresolvable` reaches neither
    overlay pass, so `triage` is the only place its `drifted=1` can ever be written."""
    ref = _ref(
        kind=RefinementKind.UNRESOLVABLE,
        status=RefinementStatus.PINNED,
        noop_builds=2,
        target=RefinementTarget(node_id="m.py::f", name="handle"),
    )
    anchors = {
        1: (Anchor(refinement_id=1, node_id="m.py::f", path="m.py", truth_sha="old"),)
    }
    triaged = triage([ref], anchors, {"m.py::f": "new"}, "")
    (outcome,) = triaged.outcomes
    assert (outcome.refinement_id, outcome.status, outcome.drifted) == (1, None, True)
    assert outcome.noop_builds == 2  # carried through, not reset and not advanced


def test_an_out_of_scope_refinement_is_neither_applied_nor_staled():
    ref = _ref(
        target=RefinementTarget(
            src="apps/frontend/m.py::f",
            dst="apps/frontend/s.py::g",
            edge_kind=EdgeKind.CALLS,
            name="g",
        )
    )
    triaged = triage([ref], {}, {}, "apps/backend/")
    assert triaged.kept == ()
    assert triaged.outcomes == ()  # silence, not a status change


def test_an_out_of_scope_candidate_is_out_of_scope_too():
    """`resolve_ambiguous` keeps its dst in the payload, so scope has to look there as well."""
    ref = _ref(
        kind=RefinementKind.RESOLVE_AMBIGUOUS,
        target=RefinementTarget(
            node_id="apps/backend/m.py::f", name="g", edge_kind=EdgeKind.CALLS
        ),
        payload=RefinementPayload(candidate="apps/frontend/s.py::g"),
    )
    assert triage([ref], {}, {}, "apps/backend/").kept == ()


def test_ids_are_stripped_of_the_partition_prefix_before_they_are_applied():
    ref = _ref(
        target=RefinementTarget(
            src="apps/backend/m.py::f",
            dst="apps/backend/s.py::g",
            edge_kind=EdgeKind.CALLS,
            name="g",
        )
    )
    result = apply_edge_overlay(
        [], NODES, triage([ref], {}, {}, "apps/backend/"), "apps/backend/"
    )
    assert [(e.src, e.dst) for e in result.edges] == [("m.py::f", "s.py::g")]


def test_annotate_node_writes_the_annotation_and_flags_the_node():
    ref = _ref(
        kind=RefinementKind.ANNOTATE_NODE,
        target=RefinementTarget(node_id="m.py::f"),
        payload=RefinementPayload(annotation="the retry path"),
    )
    result = apply_node_overlay([_node("m.py::f")], [], _triaged(ref), "")
    (node,) = result.nodes
    assert (node.annotation, node.refined) == ("the retry path", True)


def test_relabel_cluster_matches_on_jaccard_and_marks_the_label_refined():
    clusters = [GraphCluster(cluster_id=1, label="cluster-1", member_count=3)]
    nodes = [_node(n, cluster_id=1) for n in ("m.py::f", "s.py::g", "s.py::h")]
    ref = _ref(
        kind=RefinementKind.RELABEL_CLUSTER,
        target=RefinementTarget(members=("m.py::f", "s.py::g")),
        payload=RefinementPayload(label="retry"),
    )
    result = apply_node_overlay(nodes, clusters, _triaged(ref), "")
    (cluster,) = result.clusters
    assert (cluster.label, cluster.label_provenance) == ("retry", Provenance.REFINED)


def test_a_cluster_that_drifted_below_the_jaccard_floor_stales_the_refinement():
    clusters = [GraphCluster(cluster_id=1, label="cluster-1", member_count=1)]
    nodes = [_node("m.py::f", cluster_id=1)]
    ref = _ref(
        kind=RefinementKind.RELABEL_CLUSTER,
        target=RefinementTarget(members=("x.py::a", "x.py::b", "x.py::c")),
        payload=RefinementPayload(label="retry"),
    )
    result = apply_node_overlay(nodes, clusters, _triaged(ref), "")
    assert result.clusters == tuple(clusters)
    assert result.outcomes[0].status is RefinementStatus.STALE


def test_move_node_repoints_the_node_and_recounts_both_clusters():
    clusters = [
        GraphCluster(cluster_id=1, label="a", member_count=2),
        GraphCluster(cluster_id=2, label="b", member_count=1),
    ]
    nodes = [
        _node("m.py::f", cluster_id=1),
        _node("s.py::g", cluster_id=1),
        _node("s.py::h", cluster_id=2),
    ]
    ref = _ref(
        kind=RefinementKind.MOVE_NODE,
        target=RefinementTarget(node_id="m.py::f", members=("s.py::h",)),
    )
    result = apply_node_overlay(nodes, clusters, _triaged(ref), "")
    moved = {n.id: n.cluster_id for n in result.nodes}
    assert moved["m.py::f"] == 2
    counts = {c.cluster_id: c.member_count for c in result.clusters}
    assert counts == {1: 1, 2: 2}


def test_two_cluster_refinements_match_against_the_same_deterministic_membership():
    """Both read the pre-overlay membership, so their result cannot depend on their order."""
    clusters = [
        GraphCluster(cluster_id=1, label="a", member_count=2),
        GraphCluster(cluster_id=2, label="b", member_count=1),
    ]
    nodes = [
        _node("m.py::f", cluster_id=1),
        _node("s.py::g", cluster_id=1),
        _node("s.py::h", cluster_id=2),
    ]
    move = _ref(
        kind=RefinementKind.MOVE_NODE,
        target=RefinementTarget(node_id="m.py::f", members=("s.py::h",)),
    )
    relabel = _ref(
        rid=2,
        kind=RefinementKind.RELABEL_CLUSTER,
        target=RefinementTarget(members=("m.py::f", "s.py::g")),
        payload=RefinementPayload(label="retry"),
    )
    forward = apply_node_overlay(nodes, clusters, _triaged(move, relabel), "")
    backward = apply_node_overlay(nodes, clusters, _triaged(relabel, move), "")
    assert {c.cluster_id: c.label for c in forward.clusters} == {
        c.cluster_id: c.label for c in backward.clusters
    }
    assert {n.id: n.cluster_id for n in forward.nodes} == {
        n.id: n.cluster_id for n in backward.nodes
    }


def test_an_unresolvable_retires_its_queue_row_and_never_counts_a_noop():
    ref = _ref(
        kind=RefinementKind.UNRESOLVABLE,
        target=RefinementTarget(node_id="m.py::f", name="handle"),
    )
    triaged = _triaged(ref)
    rows = [
        UnresolvedRow(
            node_id="m.py::f",
            fact_kind=FactKind.CALLEE,
            name="handle",
            reason=UnresolvedReason.UNIMPORTABLE_NAME,
            call_form=CallForm.BARE,
        ),
        UnresolvedRow(
            node_id="m.py::f",
            fact_kind=FactKind.CALLEE,
            name="other",
            reason=UnresolvedReason.UNIMPORTABLE_NAME,
            call_form=CallForm.BARE,
        ),
    ]
    assert [r.name for r in retire_queue_rows(rows, triaged, "")] == ["other"]
    assert apply_edge_overlay([], NODES, triaged, "").outcomes == ()


def test_an_add_edge_retires_the_queue_row_it_answers():
    """Spec 5.7's retirement rule needs `target.name` on the edge kinds too, which is why
    `_REQUIRED_BY_KIND` demands it: without it the same pair is briefed for ever."""
    ref = _ref()
    rows = [
        UnresolvedRow(
            node_id="m.py::f",
            fact_kind=FactKind.CALLEE,
            name="g",
            reason=UnresolvedReason.UNIMPORTABLE_NAME,
            call_form=CallForm.BARE,
        )
    ]
    assert retire_queue_rows(rows, _triaged(ref), "") == []


def test_a_kind_that_names_no_pair_retires_nothing():
    """Only the node kinds can carry no `name`: `annotate_node` answers no queue row."""
    ref = _ref(
        kind=RefinementKind.ANNOTATE_NODE,
        target=RefinementTarget(node_id="m.py::f"),
        payload=RefinementPayload(annotation="the retry path"),
    )
    rows = [
        UnresolvedRow(
            node_id="m.py::f",
            fact_kind=FactKind.CALLEE,
            name="g",
            reason=UnresolvedReason.UNIMPORTABLE_NAME,
            call_form=CallForm.BARE,
        )
    ]
    assert retire_queue_rows(rows, _triaged(ref), "") == rows


def test_merge_outcomes_keeps_the_last_write_per_refinement():
    early = [RefinementOutcome(refinement_id=1, noop_builds=1)]
    late = [
        RefinementOutcome(refinement_id=1, status=RefinementStatus.REDUNDANT),
        RefinementOutcome(refinement_id=2, applied=True),
    ]
    merged = merge_outcomes(early, late)
    assert [o.refinement_id for o in merged] == [1, 2]
    assert merged[0].status is RefinementStatus.REDUNDANT
