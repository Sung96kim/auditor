"""Repo-level graph build (spec §6). Needs numpy + scikit-learn (via naming/rank/cluster)."""

from collections.abc import Callable

from auditor.graph.cluster import cluster_concepts
from auditor.graph.detectors import run_graph_detectors
from auditor.graph.model import TEST_ROLES, FileGraphFacts, GraphCluster, GraphNode
from auditor.graph.naming import name_similar_edges
from auditor.graph.rank import pagerank
from auditor.graph.resolve_edges import resolve_structural
from auditor.graph.usage import usage_similar_edges
from auditor.languages.python.detectors.graph_rules import (
    GOD_CONCEPT_RULE,
    NAMING_INCONSISTENCY_RULE,
    SCATTERED_CONCEPT_RULE,
)

_GRAPH_RULE_IDS = [GOD_CONCEPT_RULE, SCATTERED_CONCEPT_RULE, NAMING_INCONSISTENCY_RULE]


def compute_abstractness(node: GraphNode, proto_method_ids: set[str]) -> float:
    score = 0.0
    if node.is_stub:
        score += 0.4
    if "abstractmethod" in node.decorators or node.id in proto_method_ids:
        score += 0.3
    if node.is_hof:
        score += 0.2
    if not node.callees and node.kind in ("function", "method") and node.callback_names:
        score += 0.2
    return min(1.0, score)


class GraphBuilder:
    """Loads cached per-file facts and materializes the repo graph into the index."""

    @staticmethod
    def _symbol_nodes(nodes: list[GraphNode]) -> list[GraphNode]:
        return [n for n in nodes if n.kind != "module"]

    @staticmethod
    def _concept_nodes(nodes: list[GraphNode]) -> list[GraphNode]:
        return [n for n in nodes if n.kind != "module" and n.role not in TEST_ROLES]

    async def run(
        self,
        index,
        settings,
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
            await index.graph.replace([], [], [])
            return {"nodes": 0, "edges": 0, "clusters": 0, "findings": 0}

        symbols = self._symbol_nodes(nodes)
        report("resolving structural edges")
        structural = resolve_structural(nodes, follow_reexports=cfg.follow_reexports)
        report("computing naming similarity")
        name_edges, sparse = name_similar_edges(
            symbols,
            threshold=cfg.name_similarity_threshold,
            knn_k=cfg.knn_k,
            extra_stopwords=tuple(cfg.stopwords),
        )
        report("computing usage similarity")
        usage_edges = usage_similar_edges(symbols, knn_k=cfg.knn_k)
        all_edges = structural + name_edges + usage_edges

        proto = _protocol_method_ids(nodes)
        nonrank_test = {n.id for n in nodes if n.role not in TEST_ROLES}
        report("ranking (PageRank)")
        ranks = pagerank([n.id for n in nodes], all_edges, personalization=nonrank_test)
        report("clustering concepts")
        labels, label_names = cluster_concepts(
            self._concept_nodes(nodes), all_edges, floor=cfg.cluster_floor
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
        sizes: dict[int, int] = {}
        for cid in labels.values():
            sizes[cid] = sizes.get(cid, 0) + 1
        clusters = [
            GraphCluster(
                cluster_id=cid,
                label=label_names.get(cid, f"cluster-{cid}"),
                member_count=sz,
            )
            for cid, sz in sorted(sizes.items())
        ]
        report("persisting graph")
        await index.graph.replace(out_nodes, all_edges, clusters)
        findings_count = 0
        if cfg.detect:
            report("running detectors")
            await index.findings.clear_for_rules(_GRAPH_RULE_IDS)
            per_file = run_graph_detectors(out_nodes, all_edges, clusters, settings)
            for path, findings in per_file.items():
                await index.findings.add(path, findings)
                findings_count += len(findings)
        return {
            "nodes": len(out_nodes),
            "edges": len(all_edges),
            "clusters": len(clusters),
            "findings": findings_count,
        }


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
