"""The pure merge: what an active refinement does to a build's edges, nodes and clusters, and what
status it earns for trying. No database anywhere in this module."""

import time

import pytest

from auditor.config import GraphConfig
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
    RefinementPayload,
    RefinementStatus,
    RefinementTarget,
)
from auditor.graph.refine.overlay import Overlay

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
        reason=kw.pop("reason", "the call resolves there"),
        target=kw.pop(
            "target",
            RefinementTarget(
                src="m.py::f", dst="s.py::g", edge_kind=EdgeKind.CALLS, name="g"
            ),
        ),
        status=kw.pop("status", RefinementStatus.ACTIVE),
        **kw,
    )


def _overlay(*refs, anchors=None, node_truth=None, prefix="", **kw) -> Overlay:
    return Overlay.for_build(refs, anchors or {}, node_truth or {}, prefix, **kw)


def _retarget(**kw) -> RefinementTarget:
    return RefinementTarget(
        src=kw.pop("src", "m.py::f"),
        edge_kind=EdgeKind.CALLS,
        from_dst=kw.pop("from_dst", "s.py::h"),
        to_dst=kw.pop("to_dst", "s.py::g"),
        name=kw.pop("name", "h"),
    )


def _row(name: str, node_id: str = "m.py::f") -> UnresolvedRow:
    return UnresolvedRow(
        node_id=node_id,
        fact_kind=FactKind.CALLEE,
        name=name,
        reason=UnresolvedReason.UNIMPORTABLE_NAME,
        call_form=CallForm.BARE,
    )


def test_zero_refinements_leave_everything_alone():
    overlay = _overlay()
    assert overlay.edges([BASE_EDGE], NODES) == (BASE_EDGE,)
    assert overlay.nodes([_node("m.py::f")], []) == ((_node("m.py::f"),), ())
    assert overlay.outcomes == ()
    assert overlay.moved_findings is False


def test_every_pass_contributes_to_one_outcome_set():
    """F5: outcomes accumulate on the overlay, so no call site can drop a pass's verdicts."""
    annotate = _ref(
        rid=2,
        kind=RefinementKind.ANNOTATE_NODE,
        target=RefinementTarget(node_id="m.py::f"),
        payload=RefinementPayload(annotation="the retry path"),
    )
    overlay = _overlay(_ref(), annotate)
    overlay.edges([], NODES)
    overlay.nodes([_node("m.py::f")], [])
    assert [o.refinement_id for o in overlay.outcomes] == [1, 2]
    assert all(o.applied for o in overlay.outcomes)


def test_an_add_edge_appends_a_refined_edge():
    overlay = _overlay(_ref())
    edges = overlay.edges([BASE_EDGE], NODES)
    added = [e for e in edges if e.dst == "s.py::g"]
    assert len(added) == 1
    assert added[0].provenance is Provenance.REFINED
    assert added[0].kind is EdgeKind.CALLS
    assert BASE_EDGE in edges  # the deterministic set is untouched
    assert overlay.outcomes[0].applied is True
    assert overlay.outcomes[0].status is None
    assert overlay.moved_findings is True


def test_an_edge_the_resolver_now_produces_goes_redundant():
    deterministic = GraphEdge(src="m.py::f", dst="s.py::g", kind=EdgeKind.CALLS)
    overlay = _overlay(_ref())
    assert overlay.edges([deterministic], NODES) == (deterministic,)
    assert overlay.outcomes[0].status is RefinementStatus.REDUNDANT


def test_a_refinement_colliding_with_another_refinement_is_applied_not_terminal():
    """A1: `redundant` is decided against the resolver's edges only. Reverting the first must not
    silently lose the second, and `redundant` is terminal."""
    overlay = _overlay(_ref(), _ref(rid=2))
    edges = overlay.edges([], NODES)
    assert [(e.src, e.dst) for e in edges] == [("m.py::f", "s.py::g")]
    assert [(o.refinement_id, o.status, o.applied) for o in overlay.outcomes] == [
        (1, None, True),
        (2, None, True),
    ]


def test_an_add_edge_beside_a_retarget_onto_it_still_removes_the_old_edge():
    """A1 and spec 5.7: `add_edge A->C` plus `retarget_edge A: B->C` in one build. The retarget
    has nothing left to add, but the deterministic `A->B` still has to go."""
    retarget = _ref(rid=2, kind=RefinementKind.RETARGET_EDGE, target=_retarget())
    overlay = _overlay(_ref(), retarget)
    pairs = {(e.src, e.dst, e.provenance) for e in overlay.edges([BASE_EDGE], NODES)}
    assert ("m.py::f", "s.py::h", Provenance.DETERMINISTIC) not in pairs
    assert ("m.py::f", "s.py::g", Provenance.REFINED) in pairs
    assert [(o.refinement_id, o.status, o.applied) for o in overlay.outcomes] == [
        (1, None, True),
        (2, None, True),
    ]


def test_an_add_edge_to_a_vanished_node_goes_stale():
    overlay = _overlay(_ref())
    assert len(overlay.edges([BASE_EDGE], {"m.py::f"})) == 1
    assert overlay.outcomes[0].status is RefinementStatus.STALE


def test_resolve_ambiguous_reads_the_node_id_and_the_chosen_candidate():
    """Spec 9.2's shape is `(node_id, name, candidate_id)`; there is no `src`/`dst` on it."""
    ref = _ref(
        kind=RefinementKind.RESOLVE_AMBIGUOUS,
        target=RefinementTarget(node_id="m.py::f", name="g", edge_kind=EdgeKind.CALLS),
        payload=RefinementPayload(candidate="s.py::g"),
    )
    overlay = _overlay(ref)
    assert [(e.src, e.dst, e.provenance) for e in overlay.edges([], NODES)] == [
        ("m.py::f", "s.py::g", Provenance.REFINED)
    ]
    assert overlay.outcomes[0].applied is True


def test_a_retarget_moves_one_edge_and_keeps_the_rest():
    ref = _ref(kind=RefinementKind.RETARGET_EDGE, target=_retarget())
    other = GraphEdge(src="s.py::g", dst="s.py::h", kind=EdgeKind.CALLS)
    pairs = {
        (e.src, e.dst, e.provenance)
        for e in _overlay(ref).edges([BASE_EDGE, other], NODES)
    }
    assert ("m.py::f", "s.py::h", Provenance.DETERMINISTIC) not in pairs
    assert ("m.py::f", "s.py::g", Provenance.REFINED) in pairs
    assert ("s.py::g", "s.py::h", Provenance.DETERMINISTIC) in pairs


def test_a_retarget_with_no_edge_to_move_is_a_noop():
    ref = _ref(
        kind=RefinementKind.RETARGET_EDGE,
        noop_builds=1,
        target=_retarget(from_dst="s.py::g", to_dst="s.py::h", name="g"),
    )
    overlay = _overlay(ref)
    overlay.edges([], NODES)
    assert overlay.outcomes[0].noop_builds == 2
    assert overlay.outcomes[0].status is None
    assert overlay.moved_findings is False


def test_a_retarget_to_a_vanished_node_never_removes_the_old_edge():
    """The tombstone has to happen after the shared stale check, or a retarget whose new
    destination is gone deletes a deterministic edge and replaces it with nothing."""
    ref = _ref(kind=RefinementKind.RETARGET_EDGE, target=_retarget(to_dst="s.py::gone"))
    overlay = _overlay(ref)
    assert overlay.edges([BASE_EDGE], NODES) == (BASE_EDGE,)
    assert overlay.outcomes[0].status is RefinementStatus.STALE


def test_a_thousand_retargets_stay_linear():
    """A10: tombstoning keeps every surviving position valid, so the index is never rebuilt.

    Rebuilding it per retarget is `retargets x edges`; the budget is loose enough to be stable on
    the linear path and far under what the rebuild costs on this shape.
    """
    edges = [
        GraphEdge(src=f"m.py::f{i}", dst=f"s.py::h{i % 7}", kind=EdgeKind.CALLS)
        for i in range(20000)
    ]
    node_ids = {e.src for e in edges} | {e.dst for e in edges} | {"s.py::g"}
    refs = [
        _ref(
            rid=i + 1,
            kind=RefinementKind.RETARGET_EDGE,
            target=_retarget(src=f"m.py::f{i}", from_dst=f"s.py::h{i % 7}"),
        )
        for i in range(1000)
    ]
    overlay = _overlay(*refs)
    started = time.perf_counter()
    merged = overlay.edges(edges, node_ids)
    elapsed = time.perf_counter() - started
    assert len(merged) == 20000
    assert sum(e.provenance is Provenance.REFINED for e in merged) == 1000
    assert all(o.applied for o in overlay.outcomes)
    assert elapsed < 1.0


def test_three_consecutive_noops_stale_the_refinement():
    ref = _ref(kind=RefinementKind.CONFIRM_EDGE, noop_builds=2)
    overlay = _overlay(ref)
    overlay.edges([], NODES)
    assert overlay.outcomes[0].noop_builds == 3
    assert overlay.outcomes[0].status is RefinementStatus.STALE


@pytest.mark.parametrize("budget, expected", [(1, RefinementStatus.STALE), (9, None)])
def test_the_noop_budget_is_configurable(budget, expected):
    """F9: how many ineffective builds retire a correction is policy, not an invariant."""
    config = GraphConfig(refine_max_noop_builds=budget)
    overlay = _overlay(_ref(kind=RefinementKind.CONFIRM_EDGE), config=config)
    overlay.edges([], NODES)
    assert overlay.outcomes[0].status is expected


def test_an_effective_build_resets_the_noop_counter():
    overlay = _overlay(_ref(noop_builds=2))
    overlay.edges([BASE_EDGE], NODES)
    assert overlay.outcomes[0].noop_builds == 0


def test_confirm_edge_tags_the_deterministic_edge_without_reprovenancing_it():
    ref = _ref(
        kind=RefinementKind.CONFIRM_EDGE,
        target=RefinementTarget(
            src="m.py::f", dst="s.py::h", edge_kind=EdgeKind.CALLS, name="h"
        ),
    )
    overlay = _overlay(ref)
    (edge,) = overlay.edges([BASE_EDGE], NODES)
    assert edge.confirmed is True
    assert edge.provenance is Provenance.DETERMINISTIC  # provenance, not structure
    assert overlay.moved_findings is False  # nothing a detector reads has moved


def test_a_broken_anchor_stales_before_anything_is_applied():
    anchors = {
        1: (Anchor(refinement_id=1, node_id="m.py::f", path="m.py", truth_sha="old"),)
    }
    overlay = _overlay(_ref(), anchors=anchors, node_truth={"m.py::f": "new"})
    assert overlay.kept == ()
    assert overlay.outcomes[0].status is RefinementStatus.STALE


def test_a_matching_anchor_is_kept():
    anchors = {
        1: (Anchor(refinement_id=1, node_id="m.py::f", path="m.py", truth_sha="t1"),)
    }
    overlay = _overlay(_ref(), anchors=anchors, node_truth={"m.py::f": "t1"})
    assert [r.refinement_id for r in overlay.kept] == [1]
    assert overlay.drifted == frozenset()
    (outcome,) = overlay.outcomes
    assert (outcome.status, outcome.drifted, outcome.applied) == (None, False, False)


def test_a_pinned_refinement_with_a_broken_anchor_is_kept_and_marked_drifted():
    anchors = {
        1: (Anchor(refinement_id=1, node_id="m.py::f", path="m.py", truth_sha="old"),)
    }
    overlay = _overlay(
        _ref(status=RefinementStatus.PINNED),
        anchors=anchors,
        node_truth={"m.py::f": "new"},
    )
    assert [r.refinement_id for r in overlay.kept] == [1]
    assert overlay.drifted == frozenset({1})
    assert all(o.status is None for o in overlay.outcomes)
    assert overlay.outcomes[0].drifted is True


def test_a_pinned_kind_with_no_graph_effect_still_records_its_drift():
    """Spec 5.7 says a pinned refinement's drift is surfaced. `unresolvable` reaches neither
    overlay pass, so triage is the only place its `drifted=1` can ever be written."""
    ref = _ref(
        kind=RefinementKind.UNRESOLVABLE,
        status=RefinementStatus.PINNED,
        noop_builds=2,
        target=RefinementTarget(node_id="m.py::f", name="handle"),
    )
    anchors = {
        1: (Anchor(refinement_id=1, node_id="m.py::f", path="m.py", truth_sha="old"),)
    }
    overlay = _overlay(ref, anchors=anchors, node_truth={"m.py::f": "new"})
    (outcome,) = overlay.outcomes
    assert (outcome.refinement_id, outcome.status, outcome.drifted) == (1, None, True)
    assert outcome.noop_builds == 2  # carried through, not reset and not advanced


def test_a_restored_anchor_clears_the_drift_flag_on_a_kind_no_overlay_touches():
    """F19: `unresolvable` reaches neither pass, so triage is the only place that can clear it."""
    ref = _ref(
        kind=RefinementKind.UNRESOLVABLE,
        drifted=True,
        target=RefinementTarget(node_id="m.py::f", name="handle"),
    )
    anchors = {
        1: (Anchor(refinement_id=1, node_id="m.py::f", path="m.py", truth_sha="t1"),)
    }
    overlay = _overlay(ref, anchors=anchors, node_truth={"m.py::f": "t1"})
    (outcome,) = overlay.outcomes
    assert outcome.drifted is False


@pytest.mark.parametrize(
    "kw, node_ids, clusters, nodes",
    [
        ({}, {"m.py::f"}, [], []),
        ({"kind": RefinementKind.CONFIRM_EDGE, "noop_builds": 2}, NODES, [], []),
        (
            {
                "kind": RefinementKind.RELABEL_CLUSTER,
                "target": RefinementTarget(members=("x.py::a", "x.py::b", "x.py::c")),
                "payload": RefinementPayload(label="retry"),
            },
            NODES,
            [GraphCluster(cluster_id=1, label="cluster-1", member_count=1)],
            [("m.py::f", 1)],
        ),
        (
            {
                "kind": RefinementKind.ANNOTATE_NODE,
                "target": RefinementTarget(node_id="gone.py::x"),
                "payload": RefinementPayload(annotation="the retry path"),
            },
            NODES,
            [],
            [("m.py::f", None)],
        ),
    ],
    ids=["vanished-dst", "third-noop", "jaccard-floor", "missing-node"],
)
def test_a_pinned_refinement_is_never_auto_staled(kw, node_ids, clusters, nodes):
    """F2, spec 5.7 and docs/references/graph.md: a pin only ever earns `drifted`, never `stale`,
    by any path. The no-op counter still advances so a long-dead pin stays visible."""
    overlay = _overlay(_ref(status=RefinementStatus.PINNED, **kw))
    overlay.edges([], node_ids)
    overlay.nodes([_node(nid, cluster_id=cid) for nid, cid in nodes], clusters)
    assert overlay.outcomes[0].status is not RefinementStatus.STALE


def test_an_out_of_scope_refinement_is_neither_applied_nor_staled():
    ref = _ref(
        target=RefinementTarget(
            src="apps/frontend/m.py::f",
            dst="apps/frontend/s.py::g",
            edge_kind=EdgeKind.CALLS,
            name="g",
        )
    )
    overlay = _overlay(ref, prefix="apps/backend/")
    assert overlay.kept == ()
    assert overlay.outcomes == ()  # silence, not a status change


def test_an_out_of_scope_candidate_is_out_of_scope_too():
    """`resolve_ambiguous` keeps its dst in the payload, so scope has to look there as well."""
    ref = _ref(
        kind=RefinementKind.RESOLVE_AMBIGUOUS,
        target=RefinementTarget(
            node_id="apps/backend/m.py::f", name="g", edge_kind=EdgeKind.CALLS
        ),
        payload=RefinementPayload(candidate="apps/frontend/s.py::g"),
    )
    assert _overlay(ref, prefix="apps/backend/").kept == ()


def test_a_refinement_into_a_file_this_build_has_no_facts_for_gets_no_verdict():
    """F1: a rescan in flight is not a deleted symbol. The refinement is neither applied nor
    staled, so a build landing mid-`--rebuild` cannot expire a correction."""
    overlay = _overlay(_ref(), facts_paths=frozenset({"m.py"}))
    assert overlay.kept == ()
    assert overlay.outcomes == ()


def test_a_refinement_whose_files_are_all_present_is_still_triaged():
    overlay = _overlay(_ref(), facts_paths=frozenset({"m.py", "s.py"}))
    assert [r.refinement_id for r in overlay.kept] == [1]


def test_a_cluster_target_is_matched_even_when_a_member_is_out_of_scope():
    """A6: spec 5.4 matches a cluster by overlap, so a member another partition owns lowers the
    score rather than dropping the whole refinement in silence."""
    clusters = [GraphCluster(cluster_id=1, label="cluster-1", member_count=2)]
    nodes = [_node(n, cluster_id=1) for n in ("m.py::f", "s.py::g")]
    ref = _ref(
        kind=RefinementKind.RELABEL_CLUSTER,
        target=RefinementTarget(
            members=(
                "apps/backend/m.py::f",
                "apps/backend/s.py::g",
                "apps/frontend/x.py::z",
            )
        ),
        payload=RefinementPayload(label="retry"),
    )
    overlay = _overlay(ref, prefix="apps/backend/")
    _, relabelled = overlay.nodes(nodes, clusters)
    assert [(c.label, c.label_provenance) for c in relabelled] == [
        ("retry", Provenance.REFINED)
    ]


def test_ids_are_stripped_of_the_partition_prefix_before_they_are_applied():
    ref = _ref(
        target=RefinementTarget(
            src="apps/backend/m.py::f",
            dst="apps/backend/s.py::g",
            edge_kind=EdgeKind.CALLS,
            name="g",
        )
    )
    overlay = _overlay(ref, prefix="apps/backend/")
    assert [(e.src, e.dst) for e in overlay.edges([], NODES)] == [
        ("m.py::f", "s.py::g")
    ]


def test_annotate_node_writes_the_annotation_and_flags_the_node():
    ref = _ref(
        kind=RefinementKind.ANNOTATE_NODE,
        target=RefinementTarget(node_id="m.py::f"),
        payload=RefinementPayload(annotation="the retry path"),
    )
    overlay = _overlay(ref)
    (node,), _ = overlay.nodes([_node("m.py::f")], [])
    assert (node.annotation, node.refined) == ("the retry path", True)
    assert overlay.moved_findings is False  # no detector reads an annotation


def test_relabel_cluster_matches_on_jaccard_and_marks_the_label_refined():
    clusters = [GraphCluster(cluster_id=1, label="cluster-1", member_count=3)]
    nodes = [_node(n, cluster_id=1) for n in ("m.py::f", "s.py::g", "s.py::h")]
    ref = _ref(
        kind=RefinementKind.RELABEL_CLUSTER,
        target=RefinementTarget(members=("m.py::f", "s.py::g")),
        payload=RefinementPayload(label="retry"),
    )
    _, (cluster,) = _overlay(ref).nodes(nodes, clusters)
    assert (cluster.label, cluster.label_provenance) == ("retry", Provenance.REFINED)


@pytest.mark.parametrize(
    "members, applies",
    [
        (("m.py::f", "s.py::g"), True),
        (("m.py::f", "s.py::g", "x.py::a", "x.py::b"), True),
        (("m.py::f", "x.py::a", "x.py::b"), False),
    ],
    ids=["above", "exactly-half", "below"],
)
def test_the_jaccard_floor_is_inclusive(members, applies):
    """Exactly 0.5 applies: `_best_cluster` compares with `>=`, and the boundary is a decision."""
    clusters = [GraphCluster(cluster_id=1, label="cluster-1", member_count=2)]
    nodes = [_node(n, cluster_id=1) for n in ("m.py::f", "s.py::g")]
    ref = _ref(
        kind=RefinementKind.RELABEL_CLUSTER,
        target=RefinementTarget(members=members),
        payload=RefinementPayload(label="retry"),
    )
    _, (cluster,) = _overlay(ref).nodes(nodes, clusters)
    assert (cluster.label == "retry") is applies


def test_a_jaccard_tie_breaks_on_the_larger_cluster_id():
    """Arbitrary but stable: `max` compares the `(score, cluster_id)` pairs."""
    clusters = [
        GraphCluster(cluster_id=1, label="a", member_count=1),
        GraphCluster(cluster_id=2, label="b", member_count=1),
    ]
    nodes = [_node("m.py::f", cluster_id=1), _node("s.py::g", cluster_id=2)]
    ref = _ref(
        kind=RefinementKind.RELABEL_CLUSTER,
        target=RefinementTarget(members=("m.py::f", "s.py::g")),
        payload=RefinementPayload(label="retry"),
    )
    _, relabelled = _overlay(ref).nodes(nodes, clusters)
    assert [(c.cluster_id, c.label) for c in relabelled] == [(1, "a"), (2, "retry")]


def test_a_cluster_that_drifted_below_the_jaccard_floor_stales_the_refinement():
    clusters = [GraphCluster(cluster_id=1, label="cluster-1", member_count=1)]
    nodes = [_node("m.py::f", cluster_id=1)]
    ref = _ref(
        kind=RefinementKind.RELABEL_CLUSTER,
        target=RefinementTarget(members=("x.py::a", "x.py::b", "x.py::c")),
        payload=RefinementPayload(label="retry"),
    )
    overlay = _overlay(ref)
    assert overlay.nodes(nodes, clusters)[1] == tuple(clusters)
    assert overlay.outcomes[0].status is RefinementStatus.STALE


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
    overlay = _overlay(ref)
    moved, recounted = overlay.nodes(nodes, clusters)
    assert {n.id: n.cluster_id for n in moved}["m.py::f"] == 2
    assert {c.cluster_id: c.member_count for c in recounted} == {1: 1, 2: 2}
    assert overlay.moved_findings is True


def test_moving_the_last_member_out_of_a_cluster_drops_the_cluster():
    """F10: an empty cluster is not a cluster; `graph clusters` must not list one."""
    clusters = [
        GraphCluster(cluster_id=1, label="alpha", member_count=1),
        GraphCluster(cluster_id=2, label="beta", member_count=1),
    ]
    nodes = [_node("m.py::f", cluster_id=1), _node("s.py::h", cluster_id=2)]
    ref = _ref(
        kind=RefinementKind.MOVE_NODE,
        target=RefinementTarget(node_id="m.py::f", members=("s.py::h",)),
    )
    _, recounted = _overlay(ref).nodes(nodes, clusters)
    assert [(c.cluster_id, c.member_count) for c in recounted] == [(2, 2)]


def test_moving_a_node_into_the_cluster_it_is_already_in_is_a_noop():
    clusters = [GraphCluster(cluster_id=1, label="a", member_count=2)]
    nodes = [_node("m.py::f", cluster_id=1), _node("s.py::g", cluster_id=1)]
    ref = _ref(
        kind=RefinementKind.MOVE_NODE,
        noop_builds=1,
        target=RefinementTarget(node_id="m.py::f", members=("m.py::f", "s.py::g")),
    )
    overlay = _overlay(ref)
    overlay.nodes(nodes, clusters)
    assert overlay.outcomes[0].noop_builds == 2
    assert overlay.outcomes[0].applied is False
    assert overlay.moved_findings is False


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
    forward = _overlay(move, relabel).nodes(nodes, clusters)
    backward = _overlay(relabel, move).nodes(nodes, clusters)
    assert {c.cluster_id: c.label for c in forward[1]} == {
        c.cluster_id: c.label for c in backward[1]
    }
    assert {n.id: n.cluster_id for n in forward[0]} == {
        n.id: n.cluster_id for n in backward[0]
    }


def test_an_unresolvable_retires_its_queue_row_and_never_counts_a_noop():
    ref = _ref(
        kind=RefinementKind.UNRESOLVABLE,
        target=RefinementTarget(node_id="m.py::f", name="handle"),
    )
    overlay = _overlay(ref)
    overlay.edges([], NODES)
    rows = [_row("handle"), _row("other")]
    assert [r.name for r in overlay.queue_rows(rows)] == ["other"]
    (outcome,) = overlay.outcomes
    assert (outcome.status, outcome.noop_builds, outcome.applied) == (None, 0, False)


def test_an_add_edge_retires_the_queue_row_it_answers():
    """Spec 5.7's retirement rule needs `target.name` on the edge kinds too, which is why
    `_REQUIRED_BY_KIND` demands it: without it the same pair is briefed for ever."""
    overlay = _overlay(_ref())
    overlay.edges([], NODES)
    assert overlay.queue_rows([_row("g")]) == []


@pytest.mark.parametrize(
    "kw, node_ids, why",
    [
        ({}, {"m.py::f"}, "staled: the destination is gone"),
        (
            {
                "kind": RefinementKind.RETARGET_EDGE,
                "target": _retarget(from_dst="s.py::gone", name="g"),
            },
            NODES,
            "no-op: there is no edge to move",
        ),
    ],
    ids=["staled", "noop"],
)
def test_a_refinement_that_answered_nothing_leaves_its_queue_row(kw, node_ids, why):
    """F3: the fact has to stay briefable while no edge replaces it."""
    overlay = _overlay(_ref(**kw))
    overlay.edges([], node_ids)
    rows = [_row(kw.get("target", _ref(**kw).target).name or "g")]
    assert overlay.queue_rows(rows) == rows, why


def test_a_kind_that_names_no_pair_retires_nothing():
    """Only the node kinds can carry no `name`: `annotate_node` answers no queue row."""
    ref = _ref(
        kind=RefinementKind.ANNOTATE_NODE,
        target=RefinementTarget(node_id="m.py::f"),
        payload=RefinementPayload(annotation="the retry path"),
    )
    overlay = _overlay(ref)
    overlay.nodes([_node("m.py::f")], [])
    rows = [_row("g")]
    assert overlay.queue_rows(rows) == rows


def test_the_outcome_set_is_id_ordered_and_last_pass_wins():
    """The build writes one verdict per refinement, in a deterministic order."""
    annotate = _ref(
        rid=2,
        kind=RefinementKind.ANNOTATE_NODE,
        target=RefinementTarget(node_id="gone.py::x"),
        payload=RefinementPayload(annotation="the retry path"),
    )
    overlay = _overlay(annotate, _ref())
    overlay.edges([BASE_EDGE], NODES)
    overlay.nodes([_node("m.py::f")], [])
    assert [(o.refinement_id, o.status) for o in overlay.outcomes] == [
        (1, None),
        (2, RefinementStatus.STALE),
    ]
