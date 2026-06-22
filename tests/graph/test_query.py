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


async def test_concept_no_match_returns_empty(store):
    """A term matching no label and no member name must return {} — not the largest cluster."""
    assert await GraphQuery(store).concept("zzznotaconcept") == {}


async def test_concept_matches_by_member_name_when_no_label(store):
    """No cluster is labelled 'fetch', but the 'user' cluster has a fetch_user member, so a
    member-name match selects it rather than falling back to the biggest cluster."""
    out = await GraphQuery(store).concept("fetch")
    assert out["label"] == "user" and out["cluster_id"] == 1


async def test_partial_symbol_resolves_by_suffix(store):
    assert (await GraphQuery(store).related("get_user")) == (
        await GraphQuery(store).related("m.py::get_user")
    )


async def test_unknown_symbol_safe(store):
    out = await GraphQuery(store).related("does_not_exist")
    assert out == []


async def test_search_finds_by_substring_ranked(store):
    out = await GraphQuery(store).search("user")
    assert [r["id"] for r in out] == ["m.py::get_user", "m.py::fetch_user"]
    assert out[0]["kind"] == "function" and out[0]["rank"] == 0.5


async def test_usages_groups_in_and_out_with_counts(store):
    # charge is called by get_user → one incoming structural edge, nothing outgoing.
    u = await GraphQuery(store).usages("charge")
    assert (
        u["resolved"] == "m.py::charge" and u["total_in"] == 1 and u["total_out"] == 0
    )
    assert u["used_by"]["calls"] == {"count": 1, "sample": ["m.py::get_user"]}
    assert u["depends_on"] == {}
    # name_similar is semantic, not structural — it must not appear in usages.
    out = await GraphQuery(store).usages("get_user")
    assert out["depends_on"]["calls"]["sample"] == ["m.py::charge"]
    assert out["used_by"] == {} and "name_similar" not in out["depends_on"]


async def test_usages_unknown_returns_empty(store):
    assert await GraphQuery(store).usages("does_not_exist") == {}


async def test_usages_disambiguates_same_name(tmp_path):
    s = await IndexStore.connect(tmp_path / "i.db", repo="r")
    nodes = [
        GraphNode(
            id="a.py::Thing",
            kind=NodeKind.CLASS,
            name="Thing",
            module="a.py",
            qualname="Thing",
            rank=0.9,
            cluster_id=1,
        ),
        GraphNode(
            id="b.py::Thing",
            kind=NodeKind.CLASS,
            name="Thing",
            module="b.py",
            qualname="Thing",
            rank=0.1,
            cluster_id=1,
        ),
        GraphNode(
            id="a.py::user",
            kind=NodeKind.FUNCTION,
            name="user",
            module="a.py",
            qualname="user",
            rank=0.2,
            cluster_id=1,
        ),
    ]
    edges = [GraphEdge(src="a.py::user", dst="a.py::Thing", kind=EdgeKind.CALLS)]
    await s.graph.replace(
        nodes, edges, [GraphCluster(cluster_id=1, label="x", member_count=3)]
    )
    u = await GraphQuery(s).usages("Thing")
    assert u["resolved"] == "a.py::Thing"  # highest-rank match is primary
    assert u["ambiguous"] == ["b.py::Thing"]
    assert u["used_by"]["calls"]["count"] == 1
    await s.aclose()
