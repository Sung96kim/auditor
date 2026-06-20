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
