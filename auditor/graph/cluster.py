"""Concept clustering: greedy modularity communities (networkx) for anti-hairball property."""

from collections import Counter

import networkx as nx

from auditor.graph.model import GraphEdge, GraphNode


def cluster_concepts(
    nodes: list[GraphNode], edges: list[GraphEdge], *, floor: float = 0.45
) -> tuple[dict[str, int], dict[int, str]]:
    ids = [n.id for n in nodes]
    present = set(ids)
    g = nx.Graph()
    g.add_nodes_from(sorted(ids))
    if g.number_of_nodes() == 0:
        return {}, {}
    weights: dict[tuple[str, str], float] = {}
    for e in edges:
        if e.src not in present or e.dst not in present or e.src == e.dst:
            continue
        if e.kind in ("name_similar", "usage_similar") and e.weight >= floor:
            w = e.weight
        elif e.kind in ("calls", "overrides"):
            w = 0.5
        else:
            continue
        a, b = sorted((e.src, e.dst))
        weights[(a, b)] = max(weights.get((a, b), 0.0), w)
    for (a, b), w in sorted(weights.items()):
        g.add_edge(a, b, weight=w)
    communities = nx.community.greedy_modularity_communities(g, weight="weight")
    communities = sorted(communities, key=lambda c: (-len(c), min(c)))
    labels: dict[str, int] = {}
    for cid, comm in enumerate(communities):
        for nid in comm:
            labels[nid] = cid
    toks_by_id = {n.id: n.doc_tokens for n in nodes}
    label_names: dict[int, str] = {}
    members: dict[int, list[str]] = {}
    for nid, cid in labels.items():
        members.setdefault(cid, []).append(nid)
    for cid, mem in members.items():
        counter: Counter[str] = Counter()
        for m in mem:
            counter.update(set(toks_by_id.get(m, ())))
        label_names[cid] = counter.most_common(1)[0][0] if counter else f"cluster-{cid}"
    return labels, label_names
