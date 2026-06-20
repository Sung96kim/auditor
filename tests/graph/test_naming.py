from auditor.graph.model import GraphNode, NodeKind
from auditor.graph.naming import name_similar_edges


def _n(i, toks):
    return GraphNode(
        id=i,
        kind=NodeKind.FUNCTION,
        name=i,
        module="m.py",
        qualname=i,
        doc_tokens=tuple(toks),
    )


def test_similar_names_linked_sparse_excluded():
    nodes = [
        _n("a", ["read", "user", "profile", "account"]),
        _n("b", ["read", "user", "account", "record"]),  # near-twin of a
        _n("c", ["write", "invoice", "payment", "charge"]),  # unrelated
        _n("d", ["x"]),  # text-sparse
    ]
    edges, sparse = name_similar_edges(nodes, threshold=0.3, knn_k=8)
    pairs = {frozenset((e.src, e.dst)) for e in edges}
    assert frozenset(("a", "b")) in pairs  # related linked
    assert frozenset(("a", "c")) not in pairs  # unrelated not linked
    assert "d" in sparse  # sparse flagged
    assert all("d" not in (e.src, e.dst) for e in edges)  # sparse gets no edges


def test_stemming_links_morphological_variants():
    # snowball stemming collapses review/reviewer, submission/submissions, approve/approval so the
    # two inflected-but-same-concept nodes link; the unrelated payment node does not.
    nodes = [
        _n("a", ["review", "submission", "approve", "comment"]),
        _n("b", ["reviewer", "submissions", "approval", "comments"]),
        _n("c", ["payment", "charge", "invoice", "refund"]),
    ]
    edges, _ = name_similar_edges(nodes, threshold=0.3, knn_k=8)
    pairs = {frozenset((e.src, e.dst)) for e in edges}
    assert frozenset(("a", "b")) in pairs
    assert frozenset(("a", "c")) not in pairs


def test_deterministic():
    nodes = [_n(str(i), ["read", "user", f"f{i % 3}"]) for i in range(12)]
    a = name_similar_edges(nodes)
    b = name_similar_edges(nodes)
    assert [(e.src, e.dst, round(e.weight, 6)) for e in a[0]] == [
        (e.src, e.dst, round(e.weight, 6)) for e in b[0]
    ]


def test_empty_and_singleton_safe():
    assert name_similar_edges([]) == ([], set())
    one = name_similar_edges([_n("a", ["read", "user", "account", "profile"])])
    assert one[0] == []
