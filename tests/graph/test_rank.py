import pytest

from auditor.graph.model import EdgeKind, GraphEdge
from auditor.graph.rank import pagerank


def test_hub_ranks_above_leaf():
    ids = ["hub", "a", "b", "c"]
    edges = [GraphEdge(src=x, dst="hub", kind=EdgeKind.CALLS) for x in ("a", "b", "c")]
    pr = pagerank(ids, edges)
    assert pr["hub"] > pr["a"]
    assert sum(pr.values()) == pytest.approx(1.0, abs=1e-6)


def test_ignores_non_structural_edges():
    ids = ["a", "b"]
    # name_similar must NOT count toward rank (it's not in the default kinds)
    edges = [GraphEdge(src="a", dst="b", kind=EdgeKind.NAME_SIMILAR)]
    pr = pagerank(ids, edges)
    assert pr["a"] == pytest.approx(pr["b"])


def test_empty_graph_uniform():
    pr = pagerank(["a", "b", "c", "d"], [])
    assert all(v == pytest.approx(0.25) for v in pr.values())


def test_personalization_sinks_excluded_nodes():
    ids = ["prod", "t1", "t2"]
    # both test nodes call prod; with uniform teleport prod wins anyway, so make tests
    # cite each other and NOT prod to prove teleport (not edges) is what sinks them.
    edges = [
        GraphEdge(src="t1", dst="t2", kind=EdgeKind.CALLS),
        GraphEdge(src="t2", dst="t1", kind=EdgeKind.CALLS),
    ]
    uniform = pagerank(ids, edges)
    personalized = pagerank(ids, edges, personalization={"prod"})
    # uniform: the t1<->t2 cycle hoards rank; personalized: teleport only feeds prod
    assert personalized["prod"] > uniform["prod"]
    assert personalized["prod"] > personalized["t1"]


def test_personalization_none_matches_uniform():
    ids = ["a", "b", "c"]
    edges = [GraphEdge(src="a", dst="b", kind=EdgeKind.CALLS)]
    assert pagerank(ids, edges) == pagerank(ids, edges, personalization=None)
