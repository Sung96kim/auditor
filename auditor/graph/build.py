"""Repo-level graph build (spec §6). Needs numpy + scikit-learn (via naming/rank/cluster)."""

import sqlite3
import time
from collections import defaultdict
from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from auditor.config import AuditorSettings, GraphConfig
from auditor.database import IndexStore
from auditor.graph.cluster import cluster_concepts
from auditor.graph.detectors import run_graph_detectors
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
from auditor.graph.naming import NamingPass, name_similar_edges
from auditor.graph.payloads import GraphBuildReport
from auditor.graph.rank import pagerank
from auditor.graph.refine.lock import rebuild_lock
from auditor.graph.refine.models import (
    RefinementOutcome,
    Snapshot,
    SnapshotPhase,
    TuningStatus,
)
from auditor.graph.refine.overlay import Overlay, anchor_truth
from auditor.graph.refine.tuning import candidate_stopwords, tuned
from auditor.graph.resolve_edges import resolve_structural
from auditor.graph.textmodel import TextModel
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


def _symbol_nodes(nodes: list[GraphNode]) -> list[GraphNode]:
    return [n for n in nodes if n.kind is not NodeKind.MODULE]


def _concept_nodes(nodes: list[GraphNode]) -> list[GraphNode]:
    return [
        n for n in nodes if n.kind is not NodeKind.MODULE and n.role not in TEST_ROLES
    ]


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


class SimilarityPass(BaseModel):
    """The similarity half of a build: the naming pass whole, and the usage edges beside it
    (spec section 6 step 3)."""

    model_config = ConfigDict(frozen=True)

    naming: NamingPass = NamingPass()
    usage_edges: tuple[GraphEdge, ...] = ()

    @classmethod
    def of(
        cls, symbols: list[GraphNode], cfg: GraphConfig, report: Callable[[str], None]
    ) -> "SimilarityPass":
        """Name and usage similarity over the symbol nodes, both driven by the same knobs."""
        report("computing naming similarity")
        naming = name_similar_edges(
            symbols,
            threshold=cfg.name_similarity_threshold,
            knn_k=cfg.knn_k,
            extra_stopwords=tuple(cfg.stopwords),
        )
        report("computing usage similarity")
        return cls(
            naming=naming,
            usage_edges=tuple(usage_similar_edges(symbols, knn_k=cfg.knn_k)),
        )


class ClusterPass(BaseModel):
    """One ranking and clustering of a build: the nodes it stamped and the cluster rows it
    produced. The lookups below are derived from those two, so nothing can disagree."""

    model_config = ConfigDict(frozen=True)

    nodes: tuple[GraphNode, ...] = ()
    clusters: tuple[GraphCluster, ...] = ()

    @classmethod
    def of(
        cls,
        nodes: list[GraphNode],
        edges: Sequence[GraphEdge],
        cfg: GraphConfig,
        report: Callable[[str], None],
        *,
        sparse: frozenset[str],
        proto: set[str],
    ) -> "ClusterPass":
        """Rank and cluster one edge list, stamping every node with what it produced.

        ``sparse`` and ``proto`` are edge-independent, so a second pass over another edge list
        reproduces them exactly (spec section 6 steps 4 and 7).
        """
        report("ranking (PageRank)")
        ranks = pagerank(
            [n.id for n in nodes],
            edges,
            personalization={n.id for n in nodes if n.role not in TEST_ROLES},
        )
        report("clustering concepts")
        labels, label_names = cluster_concepts(
            _concept_nodes(nodes), edges, floor=cfg.cluster_floor
        )
        return cls(
            nodes=tuple(
                n.model_copy(
                    update={
                        "abstractness": compute_abstractness(n, proto),
                        "rank": ranks.get(n.id, 0.0),
                        "cluster_id": labels.get(n.id),
                        "text_sparse": n.id in sparse,
                    }
                )
                for n in nodes
            ),
            clusters=tuple(_clusters_for(labels, label_names)),
        )

    @property
    def labels(self) -> dict[int, str]:
        return {c.cluster_id: c.label for c in self.clusters}

    @property
    def sizes(self) -> dict[int, int]:
        return {c.cluster_id: c.member_count for c in self.clusters}

    def quality_rows(self, sparse: frozenset[str]) -> list[UnresolvedRow]:
        """The build-pass queue rows: symbols with too little text to cluster on, clusters that
        fell back to a ``cluster-N`` label, and clusters of one. Both cluster rows anchor on the
        highest-rank member so every row is node-keyed; test-role symbols are gated out."""
        role_by_id = {n.id: n.role for n in self.nodes}
        rows = [
            UnresolvedRow.for_node(
                nid, nid.split("::")[-1], UnresolvedReason.TEXT_SPARSE
            )
            for nid in sorted(sparse)
            if role_by_id.get(nid) not in TEST_ROLES
        ]
        labels, sizes = self.labels, self.sizes
        rank_by_id = {n.id: n.rank for n in self.nodes}
        members: dict[int, list[str]] = defaultdict(list)
        for node in self.nodes:
            if node.cluster_id is not None:
                members[node.cluster_id].append(node.id)
        for cid, member_ids in sorted(members.items()):
            head = max(sorted(member_ids), key=lambda nid: rank_by_id.get(nid, 0.0))
            label = labels.get(cid, f"cluster-{cid}")
            if label == f"cluster-{cid}":
                rows.append(
                    UnresolvedRow.for_node(head, label, UnresolvedReason.GENERIC_LABEL)
                )
            if sizes.get(cid, 0) == 1:
                rows.append(
                    UnresolvedRow.for_node(
                        head, label, UnresolvedReason.SINGLETON_CLUSTER
                    )
                )
        return rows


class GraphWrite(BaseModel):
    """One build's whole persisted result, so the write is a single argument and a new output is
    a field rather than another parameter.

    ``detect`` distinguishes "the detectors ran and found nothing" from "leave the findings
    alone": only the first clears the previous build's `GRAPH-*` rows. ``outcomes`` is what the
    build decided about each refinement it looked at, written beside the graph it describes, and
    ``decided_at`` is when the build decided it rather than when the writer thread got to the row.
    """

    model_config = ConfigDict(frozen=True)

    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = ()
    clusters: tuple[GraphCluster, ...] = ()
    unresolved: tuple[UnresolvedRow, ...] = ()
    findings: dict[str, list[Finding]] = Field(default_factory=dict)
    detect: bool = False
    outcomes: tuple[RefinementOutcome, ...] = ()
    decided_at: float = Field(default_factory=time.time)
    text_model: TextModel | None = None

    def apply(self, conn: sqlite3.Connection, index: IndexStore) -> None:
        """The whole build write, on one open connection (spec section 6 step 8)."""
        index.graph.write_graph(conn, self.nodes, self.edges, self.clusters)
        index.graph.write_text_model(conn, self.text_model)
        index.graph.write_unresolved(conn, self.unresolved)
        index.refinements.write_outcomes(conn, self.outcomes, self.decided_at)
        if not self.detect:
            return
        index.findings.write_clear_for_rules(conn, _GRAPH_RULE_IDS)
        for path, findings in self.findings.items():
            index.findings.write_add(conn, path, findings)

    async def persist(
        self, index: IndexStore, snapshot: Snapshot | None = None
    ) -> GraphBuildReport:
        """Land this build as one commit and report what it wrote (spec section 6 step 8).

        ``snapshot`` sees the queue immediately before and immediately after that commit, which
        is the only window in which one build's delta is observable.
        """
        if snapshot is not None:
            await snapshot(SnapshotPhase.BEFORE)
        await index.transaction(lambda conn: self.apply(conn, index))
        if snapshot is not None:
            await snapshot(SnapshotPhase.AFTER)
        return self.summary()

    def summary(self) -> GraphBuildReport:
        """What the CLI and the MCP tool report; the one place the counts are named."""
        return GraphBuildReport(
            nodes=len(self.nodes),
            edges=len(self.edges),
            clusters=len(self.clusters),
            unresolved=len(self.unresolved),
            findings=sum(len(f) for f in self.findings.values()),
            refined=sum(1 for o in self.outcomes if o.applied),
            expired=sum(1 for o in self.outcomes if o.status is not None),
        )


class GraphBuilder:
    """Loads cached per-file facts and materializes the repo graph into the index."""

    async def run(
        self,
        index: IndexStore,
        settings: AuditorSettings,
        *,
        progress: Callable[[str], None] | None = None,
        snapshot: Snapshot | None = None,
    ) -> GraphBuildReport:
        """Build this checkout's graph and land it as one commit.

        The tuning read is here and nowhere else, which is what makes Invariant 1's "one merge
        point" a property of the call graph: `shape` builds the settings it is given.
        """
        # spec 11's precedence: repo policy beats an active tuning row, which beats the default
        write = await self.shape(
            index, await tuned_settings(index, settings), progress=progress
        )
        (progress or (lambda _m: None))("persisting graph")
        return await write.persist(index, snapshot)

    async def shape(
        self,
        index: IndexStore,
        settings: AuditorSettings,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> GraphWrite:
        """Everything one build produces, as a value, before anything is written.

        The write is the last line of :meth:`run` and nothing else here touches the index, so a
        caller that wants the graph a config *would* produce calls this and never persists it.
        That is what makes a tuning trial provably footprint free (spec 11, Invariant 6).
        """
        cfg = settings.graph
        report = progress or (lambda _m: None)
        report("loading cached facts")
        facts = [
            FileGraphFacts.model_validate_json(b) for b in await index.graph.all_facts()
        ]
        nodes = _deduped(facts)
        if not nodes:
            # one write path: the empty graph also clears the last build's GRAPH-* findings
            return GraphWrite(detect=cfg.detect)

        report("resolving structural edges")
        structural = resolve_structural(nodes)
        report("applying refinements")
        overlay = await _overlay_for(index, cfg, nodes, {f.path for f in facts})
        similar = SimilarityPass.of(_symbol_nodes(nodes), cfg, report)
        # captured before the merge: `retarget_edge` deletes from the merged list, so a filter
        # over `all_edges` would hand the detectors a graph missing an edge nothing replaced
        deterministic_edges = [
            *structural.edges,
            *similar.naming.edges,
            *similar.usage_edges,
        ]
        all_edges = [
            *overlay.edges(structural.edges, {n.id for n in nodes}),
            *similar.naming.edges,
            *similar.usage_edges,
        ]

        proto = _protocol_method_ids(nodes)
        merged = ClusterPass.of(
            nodes, all_edges, cfg, report, sparse=similar.naming.sparse, proto=proto
        )
        # the queue's cluster rows describe the graph that ships, so a relabelled cluster stops
        # emitting `generic_label` and a moved node stops emitting `singleton_cluster`
        out_nodes, clusters = overlay.nodes(merged.nodes, merged.clusters)
        served = ClusterPass(nodes=out_nodes, clusters=clusters)
        unresolved = overlay.queue_rows(
            [*structural.unresolved, *served.quality_rows(similar.naming.sparse)]
        )

        per_file: dict[str, list[Finding]] = {}
        if cfg.detect:
            report("running detectors on the deterministic graph")
            # a graph no refinement touched: the pre-overlay edge list and a second pass over
            # it (spec section 6 step 7). Skipping that pass when the overlay placed no edge and
            # moved no node is exact, because it would read the arguments the merged one did
            det = (
                ClusterPass.of(
                    nodes,
                    deterministic_edges,
                    cfg,
                    report,
                    sparse=similar.naming.sparse,
                    proto=proto,
                )
                if overlay.moved_findings
                else merged
            )
            per_file = run_graph_detectors(
                det.nodes, deterministic_edges, det.clusters, settings
            )
        return GraphWrite(
            nodes=served.nodes,
            edges=tuple(all_edges),
            clusters=served.clusters,
            unresolved=tuple(unresolved),
            findings=per_file,
            detect=cfg.detect,
            outcomes=overlay.outcomes,
            text_model=similar.naming.text_model,
        )

    async def rebuild(
        self,
        index: IndexStore,
        settings: AuditorSettings,
        *,
        progress: Callable[[str], None] | None = None,
        lock_held: bool = False,
        snapshot: Snapshot | None = None,
        timeout: float | None = None,
    ) -> GraphBuildReport:
        """:meth:`run` under this checkout's rebuild lock. Pass ``lock_held`` when the caller
        already holds it, ``snapshot`` to see the queue immediately before and after the write
        without another build landing in between, and ``timeout`` to give up with
        `RebuildLockTimeout` rather than wait for another build for ever."""
        report = progress or (lambda _m: None)
        async with rebuild_lock(
            index.partition.identity,
            held=lock_held,
            waiting=lambda: report("waiting for the observer's rebuild"),
            poll=settings.graph.rebuild_lock_poll_seconds,
            timeout=timeout,
        ):
            return await self.run(index, settings, progress=progress, snapshot=snapshot)


async def tuned_settings(
    index: IndexStore,
    settings: AuditorSettings,
    *,
    extra: Sequence[str] = (),
) -> AuditorSettings:
    """``settings`` with this checkout's active tuning rows applied to ``graph`` (spec 11).

    ``extra`` is the token a trial is measuring, unioned with the active set here rather than
    copied onto a config first, so precedence is decided against the repo's own object.
    """
    rows = await index.tuning.tuning(statuses=[TuningStatus.ACTIVE])
    cfg = tuned(settings.graph, candidate_stopwords(rows, extra))
    if cfg is settings.graph:
        return settings
    return settings.model_copy(update={"graph": cfg})


async def _overlay_for(
    index: IndexStore,
    cfg: GraphConfig,
    nodes: list[GraphNode],
    facts_paths: set[str],
) -> Overlay:
    """This build's refinement pass, triaged against the anchors it can still check and the files
    it actually holds facts for."""
    prefix = index.partition.prefix
    active = await index.refinements.active()
    anchors = await index.refinements.anchors([r.refinement_id for r in active])
    return Overlay.for_build(
        active,
        anchors,
        anchor_truth(nodes, anchors, prefix),
        prefix,
        facts_paths=facts_paths,
        config=cfg,
    )


def _deduped(facts: list[FileGraphFacts]) -> list[GraphNode]:
    """Every node the cached facts hold, first definition winning on a duplicate id."""
    seen: set[str] = set()
    nodes: list[GraphNode] = []
    for node in (n for f in facts for n in f.nodes):
        if node.id not in seen:
            seen.add(node.id)
            nodes.append(node)
    return nodes
