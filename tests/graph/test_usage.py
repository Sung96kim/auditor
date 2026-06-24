from auditor.graph.model import GraphNode, NodeKind
from auditor.graph.usage import usage_similar_edges


def _n(i, callees=(), ptypes=()):
    return GraphNode(
        id=i,
        kind=NodeKind.FUNCTION,
        name=i,
        module="m.py",
        qualname=i,
        callees=tuple(callees),
        param_types=tuple(ptypes),
    )


def test_shared_callees_and_types_link():
    nodes = [
        _n("a", callees=["select", "scalars"], ptypes=["Session"]),
        _n("b", callees=["select", "scalars"], ptypes=["Session"]),  # twin of a
        _n("c", callees=["render", "markdown"]),  # unrelated
        _n("d"),  # no signal
    ]
    pairs = {
        frozenset((e.src, e.dst)) for e in usage_similar_edges(nodes, threshold=0.4)
    }
    assert frozenset(("a", "b")) in pairs
    assert frozenset(("a", "c")) not in pairs
    assert all("d" not in (e.src, e.dst) for e in usage_similar_edges(nodes))


def test_deterministic_and_undirected():
    nodes = [_n(str(i), callees=["select", f"x{i % 2}"]) for i in range(8)]
    e1 = usage_similar_edges(nodes, threshold=0.3)
    e2 = usage_similar_edges(nodes, threshold=0.3)
    assert [(e.src, e.dst) for e in e1] == [(e.src, e.dst) for e in e2]
    assert all(e.src < e.dst for e in e1)
