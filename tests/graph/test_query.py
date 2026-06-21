import pytest

from auditor.database import IndexStore
from auditor.graph.model import EdgeKind, GraphCluster, GraphEdge, GraphNode, NodeKind
from auditor.graph.query import GraphQuery


@pytest.fixture
async def store(tmp_path):
    s = await IndexStore.connect(tmp_path / "i.db", repo="r")
    nodes = [
        GraphNode(
            id="m.py::get_user",
            kind=NodeKind.FUNCTION,
            name="get_user",
            module="m.py",
            qualname="get_user",
            rank=0.5,
            cluster_id=1,
        ),
        GraphNode(
            id="m.py::fetch_user",
            kind=NodeKind.FUNCTION,
            name="fetch_user",
            module="m.py",
            qualname="fetch_user",
            rank=0.3,
            cluster_id=1,
        ),
        GraphNode(
            id="m.py::charge",
            kind=NodeKind.FUNCTION,
            name="charge",
            module="m.py",
            qualname="charge",
            rank=0.2,
            cluster_id=2,
        ),
    ]
    edges = [
        GraphEdge(
            src="m.py::fetch_user",
            dst="m.py::get_user",
            kind=EdgeKind.NAME_SIMILAR,
            weight=0.8,
        ),
        GraphEdge(src="m.py::get_user", dst="m.py::charge", kind=EdgeKind.CALLS),
    ]
    clusters = [
        GraphCluster(cluster_id=1, label="user", member_count=2),
        GraphCluster(cluster_id=2, label="charge", member_count=1),
    ]
    await s.graph.replace(nodes, edges, clusters)
    yield s
    await s.aclose()


async def test_related_returns_name_similar_neighbor(store):
    out = await GraphQuery(store).related("get_user")
    assert (
        out
        and out[0]["id"] == "m.py::fetch_user"
        and out[0]["weight"] == pytest.approx(0.8)
    )


async def test_neighbors_follows_structural(store):
    out = await GraphQuery(store).neighbors("get_user", depth=1)
    assert any(
        n["id"] == "m.py::charge" and n["edge"] == "calls" and n["kind"] == "function"
        for n in out
    )


async def test_concept_matches_by_label(store):
    out = await GraphQuery(store).concept("user")
    assert out["label"] == "user" and {m["id"] for m in out["members"]} == {
        "m.py::get_user",
        "m.py::fetch_user",
    }


async def test_partial_symbol_resolves_by_suffix(store):
    assert (await GraphQuery(store).related("get_user")) == (
        await GraphQuery(store).related("m.py::get_user")
    )


async def test_unknown_symbol_safe(store):
    out = await GraphQuery(store).related("does_not_exist")
    assert out == []
