from auditor.graph.cluster import cluster_concepts
from auditor.graph.model import EdgeKind, GraphEdge, GraphNode, NodeKind


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


def test_distinctive_labels_downweight_ubiquitous_tokens():
    # Two cliques. "orion" appears in EVERY node (ubiquitous); each clique has a distinctive token.
    pay = [
        GraphNode(
            id=f"a{i}",
            kind=NodeKind.FUNCTION,
            name=f"a{i}",
            module="m",
            qualname=f"a{i}",
            doc_tokens=("orion", "payment"),
        )
        for i in range(3)
    ]
    cur = [
        GraphNode(
            id=f"b{i}",
            kind=NodeKind.FUNCTION,
            name=f"b{i}",
            module="m",
            qualname=f"b{i}",
            doc_tokens=("orion", "cursor"),
        )
        for i in range(3)
    ]
    edges = [
        GraphEdge(src="a0", dst="a1", kind=EdgeKind.NAME_SIMILAR, weight=0.9),
        GraphEdge(src="a1", dst="a2", kind=EdgeKind.NAME_SIMILAR, weight=0.9),
        GraphEdge(src="b0", dst="b1", kind=EdgeKind.NAME_SIMILAR, weight=0.9),
        GraphEdge(src="b1", dst="b2", kind=EdgeKind.NAME_SIMILAR, weight=0.9),
    ]
    labels, names = cluster_concepts([*pay, *cur], edges, floor=0.45)
    chosen = set(names.values())
    assert "orion" not in chosen  # ubiquitous token never wins
    assert chosen == {"payment", "cursor"}
