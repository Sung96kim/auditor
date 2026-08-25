"""The refinement overlay (spec section 6 steps 2, 5 and 6, and section 5.7's expiry rules).

One frozen `Overlay` per build, built from what triage kept. The passes are its methods and every
verdict lands on the object, so a call site cannot drop one; the build does all the I/O.
"""

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from auditor.config import GraphConfig
from auditor.graph.hashes import node_truth_sha
from auditor.graph.model import (
    EdgeKind,
    GraphCluster,
    GraphEdge,
    GraphNode,
    Provenance,
    UnresolvedRow,
)
from auditor.graph.refine.models import (
    REFINABLE_EDGE_KINDS,
    Anchor,
    Refinement,
    RefinementKind,
    RefinementOutcome,
    RefinementStatus,
)
from auditor.graph.refine.namespace import in_scope, to_partition

_EDGE_KINDS = frozenset(
    {
        RefinementKind.ADD_EDGE,
        RefinementKind.RESOLVE_AMBIGUOUS,
        RefinementKind.RETARGET_EDGE,
        RefinementKind.CONFIRM_EDGE,
    }
)

#: one edge as the merge keys it: the pair plus the relation, never the weight or the provenance
EdgeKey = tuple[str, str, EdgeKind]


def anchor_truth(
    nodes: Sequence[GraphNode],
    anchors: Mapping[int, tuple[Anchor, ...]],
    prefix: str,
) -> dict[str, str]:
    """Current ``truth_sha`` for exactly the nodes some anchor names.

    Hashing every node is a measurable share of a build, and a build with no refinements must not
    pay any of it.
    """
    wanted = {
        local
        for rows in anchors.values()
        for row in rows
        if (local := to_partition(row.node_id, prefix)) is not None
    }
    by_id = {n.id: n for n in nodes}
    return {nid: node_truth_sha(by_id[nid]) for nid in wanted if nid in by_id}


def _refinable_kind(refinement: Refinement) -> EdgeKind | None:
    """The relation the refinement may put in the graph, ``None`` for one no proposal may name.

    `Refinement` refuses that at construction (spec 9.2), so this only ever answers ``None`` for a
    row stored before the rule existed.
    """
    kind = refinement.target.edge_kind
    return kind if kind in REFINABLE_EDGE_KINDS else None


def _path_of(node_id: str) -> str:
    """The file a node id names: ``path::qualname`` for a symbol, the path itself for a module."""
    return node_id.split("::")[0]


def _named_ids(refinement: Refinement) -> tuple[str, ...]:
    """Every node id the refinement names outright, in the toplevel-relative form it is stored in.

    ``payload.candidate`` is in here because `resolve_ambiguous` keeps its dst there (spec 9.2).
    ``target.members`` is not: spec 5.4 matches a cluster by overlap, so a member this partition
    cannot see just does not count.
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
    return tuple(i for i in named if i is not None)


def _this_build_can_see(
    named: Sequence[str], prefix: str, facts_paths: AbstractSet[str] | None
) -> bool:
    """Whether every id the refinement names is this partition's and comes from a file this build
    holds facts for. Either miss means silence: another partition's row, or a rescan in flight."""
    if any(not in_scope(i, prefix) for i in named):
        return False
    if facts_paths is None:
        return True
    return all(_path_of(to_partition(i, prefix) or "") in facts_paths for i in named)


def _anchors_hold(
    rows: Sequence[Anchor], node_truth: Mapping[str, str], prefix: str
) -> bool:
    """Whether every anchored node still hashes to what it did when the refinement was made."""
    return all(
        node_truth.get(to_partition(row.node_id, prefix) or "") == row.truth_sha
        for row in rows
    )


def _jaccard(left: AbstractSet[str], right: AbstractSet[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


class _EdgePass(BaseModel):
    """The edge kinds' working state: the list they merge into, where each edge sits in it, the
    keys the resolver produced, and the keys this build's own refinements added."""

    model_config = ConfigDict(frozen=False)

    merged: list[GraphEdge]
    deterministic: frozenset[EdgeKey]
    position: dict[EdgeKey, int]
    dropped: set[int] = Field(default_factory=set)
    placed: set[EdgeKey] = Field(default_factory=set)

    @classmethod
    def of(cls, edges: Sequence[GraphEdge]) -> "_EdgePass":
        merged = list(edges)
        keys = [(e.src, e.dst, e.kind) for e in merged]
        return cls(
            merged=merged,
            deterministic=frozenset(keys),
            position={key: at for at, key in enumerate(keys)},
        )

    def confirm(self, key: EdgeKey) -> bool:
        """Tag the edge as confirmed, reporting ``False`` when there is none to tag."""
        at = self.position.get(key)
        if at is None:
            return False
        self.merged[at] = self.merged[at].model_copy(update={"confirmed": True})
        return True

    def retarget(self, key: EdgeKey) -> bool:
        """Tombstone the edge a `retarget_edge` moves away from, ``False`` when it is not there.

        Tombstoning keeps every surviving position valid, so a thousand retargets cost a thousand
        lookups instead of a thousand rebuilds of the index.
        """
        at = self.position.pop(key, None)
        if at is None:
            return False
        self.dropped.add(at)
        return True

    def add(self, key: EdgeKey) -> None:
        """Append a `refined` edge, unless another refinement placed that same edge this build."""
        if key in self.placed:
            return
        src, dst, kind = key
        self.position[key] = len(self.merged)
        self.placed.add(key)
        self.merged.append(
            GraphEdge(src=src, dst=dst, kind=kind, provenance=Provenance.REFINED)
        )

    def result(self) -> tuple[GraphEdge, ...]:
        """The merged list with the tombstones compacted out, order otherwise untouched."""
        return tuple(e for at, e in enumerate(self.merged) if at not in self.dropped)


class _NodePass(BaseModel):
    """The node and cluster kinds' working state: the build's nodes and its cluster membership as
    they rewrite them, plus the membership the clusterer produced to match targets against."""

    model_config = ConfigDict(frozen=False)

    by_id: dict[str, GraphNode]
    by_cluster: dict[int, set[str]]
    labels: dict[int, GraphCluster]
    deterministic: dict[int, frozenset[str]]

    @classmethod
    def of(
        cls, nodes: Sequence[GraphNode], clusters: Sequence[GraphCluster]
    ) -> "_NodePass":
        by_cluster: dict[int, set[str]] = {}
        for node in nodes:
            if node.cluster_id is not None:
                by_cluster.setdefault(node.cluster_id, set()).add(node.id)
        return cls(
            by_id={n.id: n for n in nodes},
            by_cluster=by_cluster,
            labels={c.cluster_id: c for c in clusters},
            # matched once against the deterministic membership, so two cluster refinements
            # cannot depend on each other's order
            deterministic={cid: frozenset(ids) for cid, ids in by_cluster.items()},
        )

    def best_cluster(self, members: AbstractSet[str], floor: float) -> int | None:
        """The cluster whose membership overlaps ``members`` most, if it clears ``floor`` and
        still has a label row (spec 5.4).

        Ties break on the larger ``cluster_id``: arbitrary, but stable, because ``max`` compares
        the ``(score, cluster_id)`` pairs.
        """
        scored = [
            (_jaccard(members, ids), cid) for cid, ids in self.deterministic.items()
        ]
        if not scored:
            return None
        score, cluster_id = max(scored)
        if score < floor or cluster_id not in self.labels:
            return None
        return cluster_id

    def annotate(self, node_id: str, annotation: str | None) -> None:
        self.by_id[node_id] = self.by_id[node_id].model_copy(
            update={"annotation": annotation, "refined": True}
        )

    def relabel(self, cluster_id: int, label: str | None) -> None:
        current = self.labels[cluster_id]
        self.labels[cluster_id] = current.model_copy(
            update={
                "label": label or current.label,
                "label_provenance": Provenance.REFINED,
            }
        )

    def move(self, node_id: str, cluster_id: int) -> None:
        previous = self.by_id[node_id].cluster_id
        self.by_cluster.setdefault(cluster_id, set()).add(node_id)
        if previous is not None:
            self.by_cluster[previous].discard(node_id)
        self.by_id[node_id] = self.by_id[node_id].model_copy(
            update={"cluster_id": cluster_id, "refined": True}
        )

    def result(
        self, nodes: Sequence[GraphNode]
    ) -> tuple[tuple[GraphNode, ...], tuple[GraphCluster, ...]]:
        """The rewritten nodes in their original order and the cluster rows recounted.

        A cluster a `move_node` emptied is dropped: `graph clusters` must never list a label with
        no members behind it.
        """
        emptied = {cid for cid, ids in self.by_cluster.items() if not ids}
        return (
            tuple(self.by_id[n.id] for n in nodes),
            tuple(
                self.labels[cid].model_copy(
                    update={"member_count": len(self.by_cluster[cid])}
                )
                if cid in self.by_cluster
                else self.labels[cid]
                for cid in sorted(self.labels)
                if cid not in emptied
            ),
        )


class Overlay(BaseModel):
    """One build's refinement pass: what survived triage, and the merges it produces.

    Frozen and pure. Each pass records its verdicts on the object, so `outcomes` is whole however
    many passes ran, and `queue_rows` can read what the build actually applied.
    """

    model_config = ConfigDict(frozen=True)

    kept: tuple[Refinement, ...] = ()
    drifted: frozenset[int] = frozenset()
    prefix: str = ""
    config: GraphConfig = Field(default_factory=GraphConfig)

    _outcomes: dict[int, RefinementOutcome] = PrivateAttr(default_factory=dict)
    _edges_changed: bool = PrivateAttr(default=False)
    _nodes_moved: bool = PrivateAttr(default=False)

    @classmethod
    def for_build(
        cls,
        refinements: Sequence[Refinement],
        anchors: Mapping[int, tuple[Anchor, ...]],
        node_truth: Mapping[str, str],
        prefix: str = "",
        *,
        facts_paths: AbstractSet[str] | None = None,
        config: GraphConfig | None = None,
    ) -> "Overlay":
        """Triage the active refinements into what this partition may apply this build (spec 5.7).

        An id outside ``prefix`` is another partition's and is skipped in silence; so is one whose
        file this build holds no facts for, which is a rescan in flight rather than a deleted
        symbol. ``facts_paths`` of ``None`` means the caller is not tracking that.
        """
        kept: list[Refinement] = []
        drifted: set[int] = set()
        expired: list[Refinement] = []
        for refinement in refinements:
            rows = anchors.get(refinement.refinement_id, ())
            named = (*_named_ids(refinement), *(a.node_id for a in rows))
            if not _this_build_can_see(named, prefix, facts_paths):
                continue
            if not _anchors_hold(rows, node_truth, prefix):
                if refinement.status is not RefinementStatus.PINNED:
                    expired.append(refinement)
                    continue
                drifted.add(refinement.refinement_id)
            kept.append(refinement)
        overlay = cls(
            kept=tuple(kept),
            drifted=frozenset(drifted),
            prefix=prefix,
            config=config or GraphConfig(),
        )
        for refinement in expired:
            overlay._carry(refinement, status=RefinementStatus.STALE)
        for refinement in kept:
            # every kept refinement earns a verdict, so `drifted` is rewritten each build instead
            # of only when it is set (spec 5.7); the passes overwrite whatever they touch
            overlay._carry(refinement)
        return overlay

    @property
    def outcomes(self) -> tuple[RefinementOutcome, ...]:
        """Every verdict this build recorded, id-ordered so the write is deterministic."""
        return tuple(self._outcomes[key] for key in sorted(self._outcomes))

    @property
    def moved_findings(self) -> bool:
        """Whether this build's merge can move a `GRAPH-*` finding (spec section 6 step 7).

        Exactly two things reach the detectors: an edge the overlay placed or removed, and a node
        whose cluster it changed. An annotation, a label or a `confirmed` flag does not.
        """
        return self._edges_changed or self._nodes_moved

    def edges(
        self, edges: Sequence[GraphEdge], node_ids: AbstractSet[str]
    ) -> tuple[GraphEdge, ...]:
        """The deterministic edge list with the edge-shaped refinements merged in (spec 6 step 2)."""
        merging = _EdgePass.of(edges)
        for refinement in self.kept:
            if refinement.kind in _EDGE_KINDS:
                self._merge_edge(refinement, merging, node_ids)
        return merging.result()

    def nodes(
        self, nodes: Sequence[GraphNode], clusters: Sequence[GraphCluster]
    ) -> tuple[tuple[GraphNode, ...], tuple[GraphCluster, ...]]:
        """The clustered nodes and cluster rows with the node-shaped refinements merged in
        (spec section 6 steps 5 and 6)."""
        merging = _NodePass.of(nodes, clusters)
        for refinement in self.kept:
            if refinement.kind is RefinementKind.ANNOTATE_NODE:
                self._annotate(refinement, merging)
            elif refinement.kind is RefinementKind.RELABEL_CLUSTER:
                self._relabel(refinement, merging)
            elif refinement.kind is RefinementKind.MOVE_NODE:
                self._move(refinement, merging)
        return merging.result(nodes)

    def queue_rows(self, rows: Sequence[UnresolvedRow]) -> list[UnresolvedRow]:
        """The queue with the rows this build answered dropped (spec 5.7).

        A refinement retires exactly its own ``(node_id, name)`` pair, and only when this build
        applied it or it answers by declaring itself `unresolvable`: a row the build staled or
        scored a no-op has to stay briefable. Call it after the passes, which is where the
        applied verdicts come from.
        """
        settled: set[tuple[str, str]] = set()
        for refinement in self.kept:
            target = refinement.target
            node_id = self._local(target.node_id or target.src)
            if self._answered(refinement) and node_id and target.name:
                settled.add((node_id, target.name))
        return [row for row in rows if (row.node_id, row.name) not in settled]

    def _answered(self, refinement: Refinement) -> bool:
        """Whether this build settled the queue row the refinement names."""
        if refinement.kind is RefinementKind.UNRESOLVABLE:
            return True
        outcome = self._outcomes.get(refinement.refinement_id)
        return outcome is not None and outcome.applied

    def _local(self, node_id: str | None) -> str:
        """One stored id as this partition sees it, empty when it is absent or out of scope.

        Empty is an id no build has a node for, which is what the membership checks below reject.
        It never comes from a stored row: `Refinement` validates its target per kind and
        `for_build` has already dropped everything another partition owns.
        """
        if not node_id:
            return ""
        return to_partition(node_id, self.prefix) or ""

    def _members(self, refinement: Refinement) -> frozenset[str]:
        """The target's cluster members this partition can see.

        Spec 5.4 matches a cluster target by overlap, so a member another partition owns lowers
        the score rather than putting the whole refinement out of scope.
        """
        return frozenset(
            local
            for local in (
                to_partition(m, self.prefix) for m in refinement.target.members
            )
            if local is not None
        )

    def _write(
        self,
        refinement: Refinement,
        status: RefinementStatus | None,
        applied: bool,
        noop_builds: int,
    ) -> None:
        pinned = refinement.status is RefinementStatus.PINNED
        # spec 5.7: a pin outlives a refactor by every path, and still counts its dead builds
        if pinned and status is RefinementStatus.STALE:
            status = None
        self._outcomes[refinement.refinement_id] = RefinementOutcome(
            refinement_id=refinement.refinement_id,
            status=status,
            noop_builds=noop_builds,
            drifted=refinement.refinement_id in self.drifted,
            applied=applied,
        )

    def _decide(
        self,
        refinement: Refinement,
        *,
        status: RefinementStatus | None = None,
        applied: bool = False,
    ) -> None:
        """An effective verdict: the no-op counter resets because the build had something to say."""
        self._write(refinement, status, applied, 0)

    def _noop(self, refinement: Refinement) -> None:
        """A build that found nothing to do: the counter advances and stales at the budget."""
        counter = refinement.noop_builds + 1
        spent = counter >= self.config.refine_max_noop_builds
        self._write(
            refinement, RefinementStatus.STALE if spent else None, False, counter
        )

    def _carry(
        self, refinement: Refinement, *, status: RefinementStatus | None = None
    ) -> None:
        """A verdict from triage, before any pass looked: the no-op counter is untouched."""
        self._write(refinement, status, False, refinement.noop_builds)

    def _merge_edge(
        self, refinement: Refinement, merging: _EdgePass, node_ids: AbstractSet[str]
    ) -> None:
        """One edge-shaped refinement against the merged list, and the verdict it earns."""
        kind = _refinable_kind(refinement)
        src, dst = (self._local(i) for i in refinement.edge_pair())
        if kind is None:
            # defensive: `Refinement` refuses an unnameable kind at construction (spec 9.2)
            self._decide(refinement, status=RefinementStatus.STALE)
            return
        if refinement.kind is RefinementKind.CONFIRM_EDGE:
            self._confirm(refinement, merging, (src, dst, kind))
            return
        # both checks precede the retarget's tombstone: a `stale` or `redundant` verdict reached
        # after it would leave the graph missing a deterministic edge nothing replaced
        if src not in node_ids or dst not in node_ids:
            self._decide(refinement, status=RefinementStatus.STALE)
            return
        # decided against the resolver's own edges only: one this build's other refinement placed
        # makes this one applied with nothing to add, never terminal (spec 5.7)
        if (src, dst, kind) in merging.deterministic:
            self._decide(refinement, status=RefinementStatus.REDUNDANT)
            return
        if refinement.kind is RefinementKind.RETARGET_EDGE and not merging.retarget(
            (src, self._local(refinement.target.from_dst), kind)
        ):
            self._noop(refinement)
            return
        merging.add((src, dst, kind))
        self._edges_changed = True
        self._decide(refinement, applied=True)

    def _confirm(
        self, refinement: Refinement, merging: _EdgePass, key: EdgeKey
    ) -> None:
        if merging.confirm(key):
            self._decide(refinement, applied=True)
        else:
            self._noop(refinement)

    def _annotate(self, refinement: Refinement, merging: _NodePass) -> None:
        node_id = self._local(refinement.target.node_id)
        if node_id not in merging.by_id:
            self._decide(refinement, status=RefinementStatus.STALE)
            return
        merging.annotate(node_id, refinement.payload.annotation)
        self._decide(refinement, applied=True)

    def _relabel(self, refinement: Refinement, merging: _NodePass) -> None:
        cluster_id = merging.best_cluster(
            self._members(refinement), self.config.refine_cluster_jaccard
        )
        if cluster_id is None:
            self._decide(refinement, status=RefinementStatus.STALE)
            return
        merging.relabel(cluster_id, refinement.payload.label)
        self._decide(refinement, applied=True)

    def _move(self, refinement: Refinement, merging: _NodePass) -> None:
        cluster_id = merging.best_cluster(
            self._members(refinement), self.config.refine_cluster_jaccard
        )
        node_id = self._local(refinement.target.node_id)
        if cluster_id is None or node_id not in merging.by_id:
            self._decide(refinement, status=RefinementStatus.STALE)
            return
        if merging.by_id[node_id].cluster_id == cluster_id:
            self._noop(refinement)
            return
        merging.move(node_id, cluster_id)
        self._nodes_moved = True
        self._decide(refinement, applied=True)
