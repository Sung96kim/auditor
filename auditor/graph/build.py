"""Repo-level graph build (spec §6). Needs numpy + scikit-learn (via naming/rank/cluster)."""

import sqlite3
import time
from collections import defaultdict
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from auditor.config import AuditorSettings
from auditor.database import IndexStore
from auditor.graph.cluster import cluster_concepts
from auditor.graph.detectors import run_graph_detectors
from auditor.graph.hashes import node_truth_sha
from auditor.graph.model import (
    FUNCTION_KINDS,
    TEST_ROLES,
    FileGraphFacts,
    GraphCluster,
    GraphEdge,
    GraphNode,
    NodeKind,
    UnresolvedReason,
    UnresolvedRow,
)
from auditor.graph.naming import name_similar_edges
from auditor.graph.rank import pagerank
from auditor.graph.refine.models import Anchor, RefinementOutcome
from auditor.graph.refine.namespace import to_partition
from auditor.graph.refine.overlay import (
    apply_edge_overlay,
    apply_node_overlay,
    merge_outcomes,
    retire_queue_rows,
    triage,
)
from auditor.graph.resolve_edges import resolve_structural
from auditor.graph.usage import usage_similar_edges
from auditor.languages.python.detectors.graph_rules import (
    GOD_CONCEPT_RULE,
    NAMING_INCONSISTENCY_RULE,
    SCATTERED_CONCEPT_RULE,
)
from auditor.models import Finding

_GRAPH_RULE_IDS = [GOD_CONCEPT_RULE, SCATTERED_CONCEPT_RULE, NAMING_INCONSISTENCY_RULE]


def compute_abstractness(node: GraphNode, proto_method_ids: set[str]) -> float:
    score = 0.0
    if node.is_stub:
        score += 0.4
    if "abstractmethod" in node.decorators or node.id in proto_method_ids:
        score += 0.3
    if node.is_hof:
        score += 0.2
    if not node.callees and node.kind in FUNCTION_KINDS and node.callback_names:
        score += 0.2
    return min(1.0, score)


def _quality_rows(
    nodes: list[GraphNode],
    sparse: set[str],
    labels: dict[str, int],
    label_names: dict[int, str],
    sizes: dict[int, int],
) -> list[UnresolvedRow]:
    """The build-pass queue rows: symbols with too little text to cluster on, clusters that fell
    back to a ``cluster-N`` label, and clusters of one. Both cluster rows anchor on the highest-
    rank member so every row is node-keyed; test-role symbols are gated out, as the resolver does."""
    role_by_id = {n.id: n.role for n in nodes}
    rows = [
        UnresolvedRow.for_node(nid, nid.split("::")[-1], UnresolvedReason.TEXT_SPARSE)
        for nid in sorted(sparse)
        if role_by_id.get(nid) not in TEST_ROLES
    ]
    rank_by_id = {n.id: n.rank for n in nodes}
    members: dict[int, list[str]] = defaultdict(list)
    for nid, cid in labels.items():
        members[cid].append(nid)
    for cid, member_ids in sorted(members.items()):
        head = max(sorted(member_ids), key=lambda nid: rank_by_id.get(nid, 0.0))
        label = label_names.get(cid, f"cluster-{cid}")
        if label == f"cluster-{cid}":
            rows.append(
                UnresolvedRow.for_node(head, label, UnresolvedReason.GENERIC_LABEL)
            )
        if sizes.get(cid, 0) == 1:
            rows.append(
                UnresolvedRow.for_node(head, label, UnresolvedReason.SINGLETON_CLUSTER)
            )
    return rows


def _clusters_for(
    labels: dict[str, int], label_names: dict[int, str]
) -> list[GraphCluster]:
    """One cluster row per label, sized by membership and named by the clusterer."""
    sizes: dict[int, int] = {}
    for cid in labels.values():
        sizes[cid] = sizes.get(cid, 0) + 1
    return [
        GraphCluster(
            cluster_id=cid,
            label=label_names.get(cid, f"cluster-{cid}"),
            member_count=size,
        )
        for cid, size in sorted(sizes.items())
    ]


def _anchor_truth(
    nodes: list[GraphNode],
    anchors: dict[int, tuple[Anchor, ...]],
    prefix: str,
) -> dict[str, str]:
    """Current ``truth_sha`` for exactly the nodes some anchor names.

    Hashing all 3.9k nodes of this repo costs 88 ms, and a build with no refinements must not pay
    any of it.
    """
    wanted = {
        local
        for rows in anchors.values()
        for row in rows
        if (local := to_partition(row.node_id, prefix)) is not None
    }
    by_id = {n.id: n for n in nodes}
    return {nid: node_truth_sha(by_id[nid]) for nid in wanted if nid in by_id}


def _symbol_nodes(nodes: list[GraphNode]) -> list[GraphNode]:
    return [n for n in nodes if n.kind is not NodeKind.MODULE]


def _concept_nodes(nodes: list[GraphNode]) -> list[GraphNode]:
    return [
        n for n in nodes if n.kind is not NodeKind.MODULE and n.role not in TEST_ROLES
    ]


def _deterministic_findings(
    nodes: list[GraphNode],
    out_nodes: list[GraphNode],
    edges: list[GraphEdge],
    settings: AuditorSettings,
) -> dict[str, list[Finding]]:
    """Detector findings over the pre-overlay graph (spec section 6 step 7, section 2).

    Re-ranks and re-clusters over ``edges`` so no `GRAPH-*` finding can move because a refinement
    added one: measured 1.4 s for the clustering and 0.06 s for the rank on this repo's 3.9k
    nodes, about 15 % of a warm build.
    """
    ranks = pagerank(
        [n.id for n in nodes],
        edges,
        personalization={n.id for n in nodes if n.role not in TEST_ROLES},
    )
    labels, label_names = cluster_concepts(
        _concept_nodes(nodes), edges, floor=settings.graph.cluster_floor
    )
    det_nodes = [
        n.model_copy(
            update={"rank": ranks.get(n.id, 0.0), "cluster_id": labels.get(n.id)}
        )
        for n in out_nodes
    ]
    return run_graph_detectors(
        det_nodes, edges, _clusters_for(labels, label_names), settings
    )


class GraphWrite(BaseModel):
    """One build's whole persisted result, so the write is a single argument and a new output is
    a field rather than another parameter.

    ``detect`` distinguishes "the detectors ran and found nothing" from "leave the findings
    alone": only the first clears the previous build's `GRAPH-*` rows. ``outcomes`` is what the
    build decided about each refinement it looked at, written beside the graph it describes.
    """

    model_config = ConfigDict(frozen=True)

    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = ()
    clusters: tuple[GraphCluster, ...] = ()
    unresolved: tuple[UnresolvedRow, ...] = ()
    findings: dict[str, list[Finding]] = Field(default_factory=dict)
    detect: bool = False
    outcomes: tuple[RefinementOutcome, ...] = ()

    def apply(self, conn: sqlite3.Connection, index: IndexStore) -> None:
        """The whole build write, on one open connection (spec section 6 step 8)."""
        index.graph.write_graph(conn, self.nodes, self.edges, self.clusters)
        index.graph.write_unresolved(conn, self.unresolved)
        index.refinements.write_outcomes(conn, self.outcomes, time.time())
        if not self.detect:
            return
        index.findings.write_clear_for_rules(conn, _GRAPH_RULE_IDS)
        for path, findings in self.findings.items():
            index.findings.write_add(conn, path, findings)

    def summary(self) -> dict[str, int]:
        """What the CLI and the MCP tool report; the one place the counts are named."""
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "clusters": len(self.clusters),
            "unresolved": len(self.unresolved),
            "findings": sum(len(f) for f in self.findings.values()),
        }


class GraphBuilder:
    """Loads cached per-file facts and materializes the repo graph into the index."""

    async def run(
        self,
        index: IndexStore,
        settings: AuditorSettings,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, int]:
        cfg = settings.graph
        report = progress or (lambda _m: None)
        report("loading cached facts")
        facts = [
            FileGraphFacts.model_validate_json(b) for b in await index.graph.all_facts()
        ]
        raw = [n for f in facts for n in f.nodes]
        seen: set[str] = set()
        nodes = []
        for n in raw:
            if n.id not in seen:
                seen.add(n.id)
                nodes.append(n)
        if not nodes:
            # one write path: the empty graph also clears the last build's GRAPH-* findings
            return await self._persist(index, GraphWrite(detect=cfg.detect))

        symbols = _symbol_nodes(nodes)
        report("resolving structural edges")
        structural = resolve_structural(nodes)
        report("applying refinements")
        prefix = index.partition.prefix
        active = await index.refinements.active()
        anchors = await index.refinements.anchors([r.refinement_id for r in active])
        triaged = triage(active, anchors, _anchor_truth(nodes, anchors, prefix), prefix)
        edge_overlay = apply_edge_overlay(
            structural.edges, {n.id for n in nodes}, triaged, prefix
        )
        report("computing naming similarity")
        name_edges, sparse = name_similar_edges(
            symbols,
            threshold=cfg.name_similarity_threshold,
            knn_k=cfg.knn_k,
            extra_stopwords=tuple(cfg.stopwords),
        )
        report("computing usage similarity")
        usage_edges = usage_similar_edges(symbols, knn_k=cfg.knn_k)
        # captured before the merge: `retarget_edge` deletes from the merged list, so a filter
        # over `all_edges` would hand the detectors a graph missing an edge nothing replaced
        deterministic_edges = structural.edges + name_edges + usage_edges
        all_edges = list(edge_overlay.edges) + name_edges + usage_edges

        proto = _protocol_method_ids(nodes)
        nonrank_test = {n.id for n in nodes if n.role not in TEST_ROLES}
        report("ranking (PageRank)")
        ranks = pagerank([n.id for n in nodes], all_edges, personalization=nonrank_test)
        report("clustering concepts")
        labels, label_names = cluster_concepts(
            _concept_nodes(nodes), all_edges, floor=cfg.cluster_floor
        )

        out_nodes = [
            n.model_copy(
                update={
                    "abstractness": compute_abstractness(n, proto),
                    "rank": ranks.get(n.id, 0.0),
                    "cluster_id": labels.get(n.id),
                    "text_sparse": n.id in sparse,
                }
            )
            for n in nodes
        ]
        node_overlay = apply_node_overlay(
            out_nodes, _clusters_for(labels, label_names), triaged, prefix
        )
        out_nodes = list(node_overlay.nodes)
        clusters = list(node_overlay.clusters)
        # the queue's cluster rows describe the graph that ships, so a relabelled cluster stops
        # emitting `generic_label` and a moved node stops emitting `singleton_cluster`
        labels = {n.id: n.cluster_id for n in out_nodes if n.cluster_id is not None}
        label_names = {c.cluster_id: c.label for c in clusters}
        sizes = {c.cluster_id: c.member_count for c in clusters}
        unresolved = retire_queue_rows(
            [
                *structural.unresolved,
                *_quality_rows(out_nodes, sparse, labels, label_names, sizes),
            ],
            triaged,
            prefix,
        )

        per_file: dict[str, list[Finding]] = {}
        if cfg.detect:
            report("running detectors on the deterministic graph")
            per_file = _deterministic_findings(
                nodes, out_nodes, deterministic_edges, settings
            )
        report("persisting graph")
        return await self._persist(
            index,
            GraphWrite(
                nodes=tuple(out_nodes),
                edges=tuple(all_edges),
                clusters=tuple(clusters),
                unresolved=tuple(unresolved),
                findings=per_file,
                detect=cfg.detect,
                outcomes=merge_outcomes(
                    triaged.outcomes, edge_overlay.outcomes, node_overlay.outcomes
                ),
            ),
        )

    @staticmethod
    async def _persist(index: IndexStore, write: GraphWrite) -> dict[str, int]:
        """Land one build as a single commit and report what it wrote."""
        await index.transaction(lambda conn: write.apply(conn, index))
        return write.summary()


def _protocol_method_ids(nodes: list[GraphNode]) -> set[str]:
    proto = {
        n.id
        for n in nodes
        if n.kind == "class" and ({"Protocol", "ABC"} & set(n.bases))
    }
    return {
        f"{cid}.{m}"
        for cid in proto
        for n in nodes
        if n.kind == "class" and n.id == cid
        for m in n.method_names
    }
