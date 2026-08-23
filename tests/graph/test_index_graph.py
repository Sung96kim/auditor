import pytest

from auditor.graph.model import EdgeKind, GraphCluster, GraphEdge, GraphNode, NodeKind


def _n(i, **kw):
    return GraphNode(
        id=i, kind=NodeKind.FUNCTION, name=i, module="m.py", qualname=i, **kw
    )


async def test_facts_cache_roundtrip(graph_store):
    assert await graph_store.graph.facts_hash("m.py") is None
    await graph_store.graph.set_facts("m.py", '{"path":"m.py"}', "abc")
    assert await graph_store.graph.facts_hash("m.py") == "abc"
    assert '{"path":"m.py"}' in await graph_store.graph.all_facts()


async def test_clear_facts_forces_reextraction(graph_store):
    await graph_store.graph.set_facts("a.py", "{}", "h1")
    await graph_store.graph.set_facts("b.py", "{}", "h2")
    await graph_store.graph.clear_facts()
    assert await graph_store.graph.all_facts() == []
    assert (
        await graph_store.graph.facts_hash("a.py") is None
    )  # so the next scan re-extracts


async def test_replace_graph_and_query(graph_store):
    nodes = [_n("a", rank=0.9, cluster_id=1), _n("b", cluster_id=1)]
    edges = [GraphEdge(src="a", dst="b", kind=EdgeKind.CALLS, weight=1.0)]
    clusters = [GraphCluster(cluster_id=1, label="alpha", member_count=2)]
    await graph_store.graph.replace(nodes, edges, clusters)
    assert (await graph_store.graph.node("a"))["rank"] == pytest.approx(0.9)
    assert [e["dst"] for e in await graph_store.graph.edges_of("a", None)] == ["b"]
    assert {m["id"] for m in await graph_store.graph.cluster_members(1)} == {"a", "b"}
    # replace is idempotent (clears prior rows)
    await graph_store.graph.replace([_n("a")], [], [])
    assert await graph_store.graph.node("b") is None


async def test_all_edges(graph_store):
    nodes = [_n("x"), _n("y"), _n("z")]
    edges = [
        GraphEdge(src="x", dst="y", kind=EdgeKind.CALLS, weight=1.0),
        GraphEdge(src="y", dst="z", kind=EdgeKind.IMPORTS, weight=0.5),
    ]
    await graph_store.graph.replace(nodes, edges, [])
    all_e = await graph_store.graph.all_edges()
    assert len(all_e) == 2
    assert {(e["src"], e["dst"]) for e in all_e} == {("x", "y"), ("y", "z")}
