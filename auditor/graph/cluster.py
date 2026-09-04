"""Concept clustering: greedy modularity communities (networkx) for anti-hairball property."""

import math
from collections import Counter
from collections.abc import Mapping, Sequence

import networkx as nx

from auditor.graph.model import GraphEdge, GraphNode


def weighted_graph(
    node_ids: Sequence[str], edges: Sequence[GraphEdge], *, floor: float
) -> nx.Graph:
    """The weighted graph the clustering runs on: similarity edges above ``floor`` at their own
    weight, calls and overrides at a flat 0.5, everything else dropped.

    Over the node ids the caller hands in, which is a build's concept nodes and a trial's whole
    node set. Shared so both sides of a trial weight the same way (spec 11).
    """
    present = set(node_ids)
    g = nx.Graph()
    g.add_nodes_from(sorted(present))
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
    return g


def modularity(
    node_ids: Sequence[str],
    edges: Sequence[GraphEdge],
    assignment: Mapping[str, int],
    *,
    floor: float,
) -> float:
    """Weighted modularity of ``assignment`` on the clustering graph (spec 11's first metric).

    Nodes the assignment does not name form their own community, which is what an unclustered
    symbol is. An empty graph scores 0.0 rather than raising.
    """
    g = weighted_graph(node_ids, edges, floor=floor)
    if g.number_of_nodes() == 0 or g.number_of_edges() == 0:
        return 0.0
    groups: dict[int, set[str]] = {}
    loose = -1
    for nid in g.nodes:
        cid = assignment.get(nid)
        if cid is None:
            cid, loose = loose, loose - 1
        groups.setdefault(cid, set()).add(nid)
    return float(nx.community.modularity(g, list(groups.values()), weight="weight"))


def cluster_concepts(
    nodes: Sequence[GraphNode], edges: Sequence[GraphEdge], *, floor: float = 0.45
) -> tuple[dict[str, int], dict[int, str]]:
    g = weighted_graph([n.id for n in nodes], edges, floor=floor)
    if g.number_of_nodes() == 0:
        return {}, {}
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
