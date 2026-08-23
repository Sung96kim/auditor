"""The refinement overlay (spec section 6 steps 2, 5 and 6, and section 5.7's expiry rules).

Pure functions over frozen models: the build does the I/O, hands these the deterministic result,
and writes back both the merged graph and one outcome per refinement it looked at.
"""

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet

from pydantic import BaseModel, ConfigDict

from auditor.graph.model import (
    EdgeKind,
    GraphCluster,
    GraphEdge,
    GraphNode,
    Provenance,
    UnresolvedRow,
)
from auditor.graph.refine.models import (
    Anchor,
    Refinement,
    RefinementKind,
    RefinementOutcome,
    RefinementStatus,
)
from auditor.graph.refine.namespace import to_partition

MIN_CLUSTER_JACCARD = 0.5
MAX_NOOP_BUILDS = 3

#: the only edge kinds a proposal may name (spec 9.2). The collision index below is built from
#: structural edges alone, so a similarity kind would slip past it and collapse a real row.
REFINABLE_EDGE_KINDS = frozenset(
    {
        EdgeKind.CALLS,
        EdgeKind.REFERENCES_TYPE,
        EdgeKind.CALLBACK_ARG,
        EdgeKind.INHERITS,
        EdgeKind.OVERRIDES,
    }
)

_EDGE_KINDS = frozenset(
    {
        RefinementKind.ADD_EDGE,
        RefinementKind.RESOLVE_AMBIGUOUS,
        RefinementKind.RETARGET_EDGE,
        RefinementKind.CONFIRM_EDGE,
    }
)
_NODE_KINDS = frozenset(
    {
        RefinementKind.ANNOTATE_NODE,
        RefinementKind.RELABEL_CLUSTER,
        RefinementKind.MOVE_NODE,
    }
)


class Triaged(BaseModel):
    """One build's first pass over the active refinements: which survive their anchors, which
    drifted but are pinned, and the statuses the rest already earned."""

    model_config = ConfigDict(frozen=True)

    kept: tuple[Refinement, ...] = ()
    drifted: frozenset[int] = frozenset()
    outcomes: tuple[RefinementOutcome, ...] = ()


class EdgeOverlay(BaseModel):
    model_config = ConfigDict(frozen=True)

    edges: tuple[GraphEdge, ...] = ()
    outcomes: tuple[RefinementOutcome, ...] = ()


class NodeOverlay(BaseModel):
    model_config = ConfigDict(frozen=True)

    nodes: tuple[GraphNode, ...] = ()
    clusters: tuple[GraphCluster, ...] = ()
    outcomes: tuple[RefinementOutcome, ...] = ()


def _local(node_id: str | None, prefix: str) -> str:
    """One stored id as this partition sees it, empty when it is absent or out of scope.

    Empty is an id no build has a node for, which is what the membership checks below reject. It
    never comes from a stored row: `Refinement` validates its target per kind and `triage` has
    already dropped everything another partition owns.
    """
    if not node_id:
        return ""
    return to_partition(node_id, prefix) or ""


def _named_ids(refinement: Refinement) -> tuple[str, ...]:
    """Every node id the refinement names, in the toplevel-relative form it was stored in.

    ``payload.candidate`` is in here because `resolve_ambiguous` keeps its dst there (spec 9.2),
    and an out-of-scope dst has to make the whole refinement out of scope.
    """
    target = refinement.target
    named = (
        target.src,
        target.dst,
        target.from_dst,
        target.to_dst,
        target.node_id,
        refinement.payload.candidate,
    )
    return tuple(i for i in named if i is not None) + target.members


def triage(
    refinements: Sequence[Refinement],
    anchors: Mapping[int, tuple[Anchor, ...]],
    node_truth: Mapping[str, str],
    prefix: str,
) -> Triaged:
    """Split the active refinements into what this partition may apply and what just expired.

    An id outside ``prefix`` belongs to another partition of the same checkout: it is skipped in
    silence, never staled. A `pinned` refinement whose anchor moved is kept and marked drifted.
    """
    kept: list[Refinement] = []
    drifted: set[int] = set()
    outcomes: list[RefinementOutcome] = []
    for refinement in refinements:
        anchor_rows = anchors.get(refinement.refinement_id, ())
        named = (*_named_ids(refinement), *(a.node_id for a in anchor_rows))
        if any(to_partition(i, prefix) is None for i in named):
            continue
        broken = any(
            node_truth.get(to_partition(a.node_id, prefix) or "") != a.truth_sha
            for a in anchor_rows
        )
        if broken and refinement.status is not RefinementStatus.PINNED:
            outcomes.append(
                RefinementOutcome(
                    refinement_id=refinement.refinement_id,
                    status=RefinementStatus.STALE,
                    noop_builds=refinement.noop_builds,
                )
            )
            continue
        if broken:
            drifted.add(refinement.refinement_id)
            # the only place a pinned kind with no graph effect can record its drift (spec 5.7)
            outcomes.append(
                RefinementOutcome(
                    refinement_id=refinement.refinement_id,
                    noop_builds=refinement.noop_builds,
                    drifted=True,
                )
            )
        kept.append(refinement)
    return Triaged(
        kept=tuple(kept), drifted=frozenset(drifted), outcomes=tuple(outcomes)
    )


def _outcome(
    refinement: Refinement,
    triaged: Triaged,
    *,
    status: RefinementStatus | None = None,
    applied: bool = False,
    noop: bool = False,
) -> RefinementOutcome:
    """One verdict. A no-op advances the counter and stales at three; anything effective resets it."""
    counter = refinement.noop_builds + 1 if noop else 0
    if status is None and noop and counter >= MAX_NOOP_BUILDS:
        status = RefinementStatus.STALE
    return RefinementOutcome(
        refinement_id=refinement.refinement_id,
        status=status,
        noop_builds=counter,
        drifted=refinement.refinement_id in triaged.drifted,
        applied=applied,
    )


def _edge_ends(refinement: Refinement, prefix: str) -> tuple[str, str]:
    """The ``(src, dst)`` an `add_edge`, `confirm_edge` or `resolve_ambiguous` points at.

    `resolve_ambiguous` stores spec 5.4's ``{node_id, name}`` plus spec 9.2's chosen candidate in
    ``payload.candidate``; the other two use ``src`` and ``dst``. `retarget_edge` reads ``to_dst``
    itself, so it is not handled here.
    """
    target = refinement.target
    src, dst = (
        (target.node_id, refinement.payload.candidate)
        if refinement.kind is RefinementKind.RESOLVE_AMBIGUOUS
        else (target.src, target.dst)
    )
    return _local(src, prefix), _local(dst, prefix)


def apply_edge_overlay(
    edges: Sequence[GraphEdge],
    node_ids: AbstractSet[str],
    triaged: Triaged,
    prefix: str,
) -> EdgeOverlay:
    """Merge the edge-shaped refinements into one build's deterministic edge list."""
    merged = list(edges)
    index = {(e.src, e.dst, e.kind): position for position, e in enumerate(merged)}
    outcomes: list[RefinementOutcome] = []
    for refinement in triaged.kept:
        if refinement.kind not in _EDGE_KINDS:
            continue
        target = refinement.target
        kind = target.edge_kind
        src, dst = _edge_ends(refinement, prefix)
        # the kind is always set (spec 9.2 via `_REQUIRED_BY_KIND`); it may still be one no
        # proposal may name, and the collision index below would not catch that
        if kind not in REFINABLE_EDGE_KINDS:
            outcomes.append(
                _outcome(refinement, triaged, status=RefinementStatus.STALE)
            )
            continue
        if refinement.kind is RefinementKind.CONFIRM_EDGE:
            position = index.get((src, dst, kind))
            if position is None:
                outcomes.append(_outcome(refinement, triaged, noop=True))
                continue
            merged[position] = merged[position].model_copy(update={"confirmed": True})
            outcomes.append(_outcome(refinement, triaged, applied=True))
            continue
        if refinement.kind is RefinementKind.RETARGET_EDGE:
            dst = _local(target.to_dst, prefix)
        # both checks precede the retarget's pop: a `stale` or `redundant` verdict reached after
        # it would leave the graph missing a deterministic edge nothing replaced
        if src not in node_ids or dst not in node_ids:
            outcomes.append(
                _outcome(refinement, triaged, status=RefinementStatus.STALE)
            )
            continue
        if (src, dst, kind) in index:
            outcomes.append(
                _outcome(refinement, triaged, status=RefinementStatus.REDUNDANT)
            )
            continue
        if refinement.kind is RefinementKind.RETARGET_EDGE:
            old = index.get((src, _local(target.from_dst, prefix), kind))
            if old is None:
                outcomes.append(_outcome(refinement, triaged, noop=True))
                continue
            merged.pop(old)
            index = {(e.src, e.dst, e.kind): p for p, e in enumerate(merged)}
        index[(src, dst, kind)] = len(merged)
        merged.append(
            GraphEdge(src=src, dst=dst, kind=kind, provenance=Provenance.REFINED)
        )
        outcomes.append(_outcome(refinement, triaged, applied=True))
    return EdgeOverlay(edges=tuple(merged), outcomes=tuple(outcomes))


def _jaccard(left: AbstractSet[str], right: AbstractSet[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _best_cluster(
    members: AbstractSet[str], by_cluster: Mapping[int, frozenset[str]]
) -> int | None:
    """The cluster whose membership overlaps ``members`` most, if it clears the floor (spec 5.4).

    Ties break on the larger ``cluster_id``: arbitrary, but stable, because ``max`` compares the
    ``(score, cluster_id)`` pairs.
    """
    scored = [(_jaccard(members, ids), cid) for cid, ids in by_cluster.items()]
    if not scored:
        return None
    score, cluster_id = max(scored)
    return cluster_id if score >= MIN_CLUSTER_JACCARD else None


def apply_node_overlay(
    nodes: Sequence[GraphNode],
    clusters: Sequence[GraphCluster],
    triaged: Triaged,
    prefix: str,
) -> NodeOverlay:
    """Merge the node- and cluster-shaped refinements into one build's clustered result."""
    by_id = {n.id: n for n in nodes}
    by_cluster: dict[int, set[str]] = {}
    for node in nodes:
        if node.cluster_id is not None:
            by_cluster.setdefault(node.cluster_id, set()).add(node.id)
    # matched once against the deterministic membership, so two cluster refinements cannot
    # depend on each other's order
    frozen = {cid: frozenset(ids) for cid, ids in by_cluster.items()}
    labels = {c.cluster_id: c for c in clusters}
    outcomes: list[RefinementOutcome] = []
    for refinement in triaged.kept:
        if refinement.kind not in _NODE_KINDS:
            continue
        target = refinement.target
        node_id = _local(target.node_id, prefix)
        if refinement.kind is RefinementKind.ANNOTATE_NODE:
            if node_id not in by_id:
                outcomes.append(
                    _outcome(refinement, triaged, status=RefinementStatus.STALE)
                )
                continue
            by_id[node_id] = by_id[node_id].model_copy(
                update={"annotation": refinement.payload.annotation, "refined": True}
            )
            outcomes.append(_outcome(refinement, triaged, applied=True))
            continue
        members = {
            local
            for local in (to_partition(m, prefix) for m in target.members)
            if local is not None
        }
        cluster_id = _best_cluster(members, frozen)
        if cluster_id is None or cluster_id not in labels:
            outcomes.append(
                _outcome(refinement, triaged, status=RefinementStatus.STALE)
            )
            continue
        if refinement.kind is RefinementKind.RELABEL_CLUSTER:
            labels[cluster_id] = labels[cluster_id].model_copy(
                update={
                    "label": refinement.payload.label or labels[cluster_id].label,
                    "label_provenance": Provenance.REFINED,
                }
            )
            outcomes.append(_outcome(refinement, triaged, applied=True))
            continue
        if node_id not in by_id:
            outcomes.append(
                _outcome(refinement, triaged, status=RefinementStatus.STALE)
            )
            continue
        previous = by_id[node_id].cluster_id
        if previous == cluster_id:
            outcomes.append(_outcome(refinement, triaged, noop=True))
            continue
        by_cluster.setdefault(cluster_id, set()).add(node_id)
        if previous is not None:
            by_cluster[previous].discard(node_id)
        by_id[node_id] = by_id[node_id].model_copy(
            update={"cluster_id": cluster_id, "refined": True}
        )
        outcomes.append(_outcome(refinement, triaged, applied=True))
    recounted = [
        labels[cid].model_copy(update={"member_count": len(by_cluster.get(cid, ()))})
        if cid in by_cluster
        else labels[cid]
        for cid in sorted(labels)
    ]
    return NodeOverlay(
        nodes=tuple(by_id[n.id] for n in nodes),
        clusters=tuple(recounted),
        outcomes=tuple(outcomes),
    )


def retire_queue_rows(
    rows: Sequence[UnresolvedRow], triaged: Triaged, prefix: str
) -> list[UnresolvedRow]:
    """Drop the queue rows a kept refinement already answered (spec 5.7).

    A refinement retires exactly its own ``(node_id, name)`` pair, so it needs ``target.name``.
    Every kind that answers a queue row is required to carry it (`_REQUIRED_BY_KIND`); the cluster
    and annotation kinds answer none and retire nothing.
    """
    settled: set[tuple[str, str]] = set()
    for refinement in triaged.kept:
        target = refinement.target
        node_id = _local(target.node_id or target.src, prefix)
        if node_id and target.name:
            settled.add((node_id, target.name))
    return [row for row in rows if (row.node_id, row.name) not in settled]


def merge_outcomes(
    *groups: Sequence[RefinementOutcome],
) -> tuple[RefinementOutcome, ...]:
    """Later groups win per refinement id, ordered by id so a build's writes are deterministic."""
    merged: dict[int, RefinementOutcome] = {}
    for group in groups:
        for outcome in group:
            merged[outcome.refinement_id] = outcome
    return tuple(merged[key] for key in sorted(merged))
