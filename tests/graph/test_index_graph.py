import pytest

from auditor.graph.model import EdgeKind, GraphCluster, GraphEdge, GraphNode, NodeKind
from auditor.index import IndexStore


@pytest.fixture
async def store(tmp_path):
    s = await IndexStore.connect(tmp_path / "i.db", repo="r")
    yield s
    await s.aclose()


def _n(i, **kw):
    return GraphNode(
        id=i, kind=NodeKind.FUNCTION, name=i, module="m.py", qualname=i, **kw
    )


async def test_facts_cache_roundtrip(store):
    assert await store.graph_facts_hash("m.py") is None
    await store.set_graph_facts("m.py", '{"path":"m.py"}', "abc")
    assert await store.graph_facts_hash("m.py") == "abc"
    assert '{"path":"m.py"}' in await store.all_graph_facts()


async def test_replace_graph_and_query(store):
    nodes = [_n("a", rank=0.9, cluster_id=1), _n("b", cluster_id=1)]
    edges = [GraphEdge(src="a", dst="b", kind=EdgeKind.CALLS, weight=1.0)]
    clusters = [GraphCluster(cluster_id=1, label="alpha", member_count=2)]
    await store.replace_graph(nodes, edges, clusters)
    assert (await store.graph_node("a"))["rank"] == pytest.approx(0.9)
    assert [e["dst"] for e in await store.graph_edges_of("a", None)] == ["b"]
    assert {m["id"] for m in await store.graph_cluster_members(1)} == {"a", "b"}
    # replace is idempotent (clears prior rows)
    await store.replace_graph([_n("a")], [], [])
    assert await store.graph_node("b") is None
