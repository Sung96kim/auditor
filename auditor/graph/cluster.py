"""Concept clustering: greedy modularity communities (networkx) for anti-hairball property."""

import math
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
    members: dict[int, list[str]] = {}
    for nid, cid in labels.items():
        members.setdefault(cid, []).append(nid)
    num_clusters = len(members)
    # token -> number of clusters containing it (document frequency)
    doc_freq: Counter[str] = Counter()
    cluster_counts: dict[int, Counter[str]] = {}
    for cid, mem in members.items():
        counts: Counter[str] = Counter()
        for m in mem:
            counts.update(set(toks_by_id.get(m, ())))
        cluster_counts[cid] = counts
        doc_freq.update(counts.keys())
    label_names: dict[int, str] = {}
    for cid, counts in cluster_counts.items():
        if not counts:
            label_names[cid] = f"cluster-{cid}"
            continue
        # smoothed idf so it is always positive; ubiquitous tokens -> near 0
        best = max(
            sorted(counts),  # sorted() => deterministic tie-break by token
            key=lambda t: (
                counts[t] * math.log((1 + num_clusters) / (1 + doc_freq[t])) + 1e-9
            ),
        )
        label_names[cid] = best
    return labels, label_names
