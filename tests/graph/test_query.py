import pytest

from auditor.graph.model import EdgeKind, GraphCluster, GraphEdge, GraphNode, NodeKind
from auditor.graph.payloads import UsageGroup
from auditor.graph.query import GraphQuery


async def test_related_returns_name_similar_neighbor(query_store):
    out = await GraphQuery(query_store).related("get_user")
    assert (
        out.root
        and out.root[0].id == "m.py::fetch_user"
        and out.root[0].weight == pytest.approx(0.8)
    )


async def test_neighbors_follows_structural(query_store):
    out = await GraphQuery(query_store).neighbors("get_user", depth=1)
    assert any(
        n.id == "m.py::charge" and n.edge == "calls" and n.kind == "function"
        for n in out.root
    )


async def test_concept_matches_by_label(query_store):
    out = await GraphQuery(query_store).concept("user")
    assert out is not None
    assert out.label == "user" and {m.id for m in out.members} == {
        "m.py::get_user",
        "m.py::fetch_user",
    }


async def test_concept_no_match_returns_empty(query_store):
    """A term matching no label and no member name must return None, not the largest cluster."""
    assert await GraphQuery(query_store).concept("zzznotaconcept") is None


async def test_concept_matches_by_member_name_when_no_label(query_store):
    """No cluster is labelled 'fetch', but the 'user' cluster has a fetch_user member, so a
    member-name match selects it rather than falling back to the biggest cluster."""
    out = await GraphQuery(query_store).concept("fetch")
    assert out is not None and out.label == "user" and out.cluster_id == 1


async def test_partial_symbol_resolves_by_suffix(query_store):
    assert (await GraphQuery(query_store).related("get_user")) == (
        await GraphQuery(query_store).related("m.py::get_user")
    )


async def test_unknown_symbol_safe(query_store):
    out = await GraphQuery(query_store).related("does_not_exist")
    assert out.root == ()


async def test_search_finds_by_substring_ranked(query_store):
    out = await GraphQuery(query_store).search("user")
    assert [r.id for r in out.root] == ["m.py::get_user", "m.py::fetch_user"]
    assert out.root[0].kind == "function" and out.root[0].rank == 0.5


async def test_usages_groups_in_and_out_with_counts(query_store):
    # charge is called by get_user → one incoming structural edge, nothing outgoing.
    u = await GraphQuery(query_store).usages("charge")
    assert u is not None
    assert u.resolved == "m.py::charge" and u.total_in == 1 and u.total_out == 0
    assert u.used_by["calls"] == UsageGroup(count=1, sample=("m.py::get_user",))
    assert u.depends_on == {}
    # name_similar is semantic, not structural — it must not appear in usages.
    out = await GraphQuery(query_store).usages("get_user")
    assert out is not None
    assert out.depends_on["calls"].sample == ("m.py::charge",)
    assert out.used_by == {} and "name_similar" not in out.depends_on


async def test_usages_unknown_returns_empty(query_store):
    assert await GraphQuery(query_store).usages("does_not_exist") is None


async def test_usages_disambiguates_same_name(graph_store):
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
    await graph_store.graph.replace(
        nodes, edges, [GraphCluster(cluster_id=1, label="x", member_count=3)]
    )
    u = await GraphQuery(graph_store).usages("Thing")
    assert u is not None
    assert u.resolved == "a.py::Thing"  # highest-rank match is primary
    assert u.ambiguous == ("b.py::Thing",)
    assert u.used_by["calls"].count == 1
