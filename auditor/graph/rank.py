"""In-house PageRank over the structural graph (spec §9d). Needs numpy."""

import numpy as np

from auditor.graph.model import GraphEdge

_STRUCTURAL = ("calls", "overrides", "references_type")


def pagerank(
    node_ids: list[str],
    edges: list[GraphEdge],
    *,
    kinds: tuple[str, ...] = _STRUCTURAL,
    damping: float = 0.85,
    iters: int = 50,
    personalization: set[str] | None = None,
) -> dict[str, float]:
    n = len(node_ids)
    if n == 0:
        return {}
    idx = {nid: i for i, nid in enumerate(node_ids)}
    out: list[list[int]] = [[] for _ in range(n)]
    kset = set(kinds)
    for e in edges:
        if e.kind in kset and e.src in idx and e.dst in idx:
            out[idx[e.src]].append(idx[e.dst])

    p = np.zeros(n)
    if personalization:
        for nid in node_ids:
            if nid in personalization:
                p[idx[nid]] = 1.0
        if (
            p.sum() == 0
        ):  # personalization set disjoint from nodes -> fall back to uniform
            p[:] = 1.0
    else:
        p[:] = 1.0
    p /= p.sum()

    rank = p.copy()
    for _ in range(iters):
        nxt = (1.0 - damping) * p
        dangling = 0.0
        for i in range(n):
            if out[i]:
                share = damping * rank[i] / len(out[i])
                for j in out[i]:
                    nxt[j] += share
            else:
                dangling += damping * rank[i]
        rank = nxt + dangling * p
    rank /= rank.sum()
    return {nid: float(rank[idx[nid]]) for nid in node_ids}
