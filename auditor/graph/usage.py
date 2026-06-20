"""Usage-similarity edges: callee + operand-type Jaccard with blocking (spec §9b). Stdlib only."""

from collections import defaultdict

from auditor.graph.model import EdgeKind, GraphEdge, GraphNode

_FN_KINDS = ("function", "method")


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def usage_similar_edges(
    nodes: list[GraphNode], *, threshold: float = 0.5, knn_k: int = 8
) -> list[GraphEdge]:
    fns = [n for n in nodes if n.kind in _FN_KINDS and (n.callees or n.param_types)]
    callees = {n.id: frozenset(n.callees) for n in fns}
    ptypes = {n.id: frozenset(n.param_types) for n in fns}

    # blocking: index by token so we only score pairs that share a callee or a type
    buckets: dict[str, list[str]] = defaultdict(list)
    for n in fns:
        for tok in callees[n.id] | ptypes[n.id]:
            buckets[tok].append(n.id)

    cand: dict[str, set[str]] = defaultdict(set)
    for ids in buckets.values():
        for i in ids:
            cand[i].update(x for x in ids if x != i)

    scored: dict[str, list[tuple[float, str]]] = {}
    for nid, others in cand.items():
        ranked = sorted(
            (
                (
                    round(
                        0.6 * _jaccard(callees[nid], callees[o])
                        + 0.4 * _jaccard(ptypes[nid], ptypes[o]),
                        6,
                    ),
                    o,
                )
                for o in others
            ),
            reverse=True,
        )
        scored[nid] = [(s, o) for s, o in ranked if s >= threshold][:knn_k]

    edges: list[GraphEdge] = []
    seen: set[tuple[str, str]] = set()
    for nid, ranked in scored.items():
        for score, o in ranked:
            a, b = sorted((nid, o))
            if (a, b) not in seen:
                seen.add((a, b))
                edges.append(
                    GraphEdge(src=a, dst=b, kind=EdgeKind.USAGE_SIMILAR, weight=score)
                )
    edges.sort(key=lambda e: (e.src, e.dst))
    return edges
