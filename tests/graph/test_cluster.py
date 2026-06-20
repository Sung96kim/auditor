from auditor.graph.cluster import cluster_concepts
from auditor.graph.model import EdgeKind, GraphEdge, GraphNode


def _n(i, toks=()):
    return GraphNode(
        id=i, kind="function", name=i, module="m.py", qualname=i, doc_tokens=tuple(toks)
    )


def _edge(a, b):
    return GraphEdge(src=a, dst=b, kind=EdgeKind.NAME_SIMILAR, weight=0.9)


def test_two_disjoint_cliques_form_two_clusters():
    nodes = [_n(x, ["user"]) for x in ("a", "b", "c")] + [
        _n(x, ["pay"]) for x in ("d", "e", "f")
    ]
    edges = [_edge("a", "b"), _edge("b", "c"), _edge("d", "e"), _edge("e", "f")]
    labels, names = cluster_concepts(nodes, edges)
    assert labels["a"] == labels["b"] == labels["c"]
    assert labels["d"] == labels["e"] == labels["f"]
    assert labels["a"] != labels["d"]
    assert names[labels["a"]] == "user" and names[labels["d"]] == "pay"


def test_weak_edges_below_floor_do_not_merge():
    nodes = [_n("a"), _n("b")]
    edges = [GraphEdge(src="a", dst="b", kind=EdgeKind.NAME_SIMILAR, weight=0.2)]
    labels, _ = cluster_concepts(nodes, edges, floor=0.45)
    assert labels["a"] != labels["b"]  # below floor -> not merged


def test_deterministic():
    nodes = [_n(str(i), ["t"]) for i in range(10)]
    edges = [_edge(str(i), str(i + 1)) for i in range(9)]
    assert cluster_concepts(nodes, edges)[0] == cluster_concepts(nodes, edges)[0]
