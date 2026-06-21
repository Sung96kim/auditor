"""Repo-level graph build (spec §6). Needs numpy + scikit-learn (via naming/rank/cluster)."""

from auditor.graph.cluster import cluster_concepts
from auditor.graph.model import FileGraphFacts, GraphCluster, GraphNode
from auditor.graph.naming import name_similar_edges
from auditor.graph.rank import pagerank
from auditor.graph.resolve_edges import resolve_structural
from auditor.graph.usage import usage_similar_edges

_TEST_ROLES = ("test", "test_support")


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
        return [n for n in nodes if n.kind != "module" and n.role not in _TEST_ROLES]

    async def run(self, index, settings) -> dict[str, int]:
        cfg = settings.graph
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
            return {"nodes": 0, "edges": 0, "clusters": 0}

        symbols = self._symbol_nodes(nodes)
        structural = resolve_structural(nodes)
        name_edges, sparse = name_similar_edges(
            symbols,
            threshold=cfg.name_similarity_threshold,
            knn_k=cfg.knn_k,
            extra_stopwords=tuple(cfg.stopwords),
        )
        usage_edges = usage_similar_edges(symbols, knn_k=cfg.knn_k)
        all_edges = structural + name_edges + usage_edges

        proto = _protocol_method_ids(nodes)
        ranks = pagerank([n.id for n in nodes], all_edges)
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
        await index.graph.replace(out_nodes, all_edges, clusters)
        return {
            "nodes": len(out_nodes),
            "edges": len(all_edges),
            "clusters": len(clusters),
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
