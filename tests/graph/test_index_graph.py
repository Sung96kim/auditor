import pytest

from auditor.graph.model import (
    CallForm,
    EdgeKind,
    FactKind,
    GraphCluster,
    GraphEdge,
    GraphNode,
    NodeKind,
    UnresolvedReason,
    UnresolvedRow,
)
from auditor.models import FileRole, IndexEntry


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


def _row(node_id: str, name: str, **kw) -> UnresolvedRow:
    return UnresolvedRow(
        node_id=node_id,
        fact_kind=kw.pop("fact_kind", FactKind.CALLEE),
        name=name,
        reason=kw.pop("reason", UnresolvedReason.UNIMPORTABLE_NAME),
        **kw,
    )


async def test_unresolved_roundtrip_decodes_json_columns(graph_store):
    await graph_store.graph.replace_unresolved(
        [
            _row(
                "m.py::f",
                "handle",
                receiver_root="svc",
                call_form=CallForm.ATTR,
                candidates=("a.py::handle",),
                definers=("a.py::handle", "b.py::handle"),
                resolution_path=("pkg/__init__.py",),
                priority=3,
                externally_bound=True,
            )
        ]
    )
    (row,) = await graph_store.graph.unresolved()
    assert row["node_id"] == "m.py::f"
    assert row["fact_kind"] == "callee"
    assert row["call_form"] == "attr"
    assert row["receiver_root"] == "svc"
    assert row["candidates"] == ["a.py::handle"]
    assert row["definers"] == ["a.py::handle", "b.py::handle"]
    assert row["resolution_path"] == ["pkg/__init__.py"]
    assert row["externally_bound"] is True
    assert "repo" not in row


async def test_unresolved_orders_by_priority_then_node_and_name(graph_store):
    await graph_store.graph.replace_unresolved(
        [
            _row("z.py::f", "late", priority=4),
            _row("a.py::f", "b_name", priority=1),
            _row("a.py::f", "a_name", priority=1),
        ]
    )
    rows = await graph_store.graph.unresolved()
    assert [(r["node_id"], r["name"]) for r in rows] == [
        ("a.py::f", "a_name"),
        ("a.py::f", "b_name"),
        ("z.py::f", "late"),
    ]


async def test_unresolved_filters_and_limit(graph_store):
    await graph_store.graph.replace_unresolved(
        [
            _row("a.py::f", "one", reason=UnresolvedReason.AMBIGUOUS_NAME, priority=1),
            _row("b.py::g", "two"),
            _row("c.py::h", "three"),
        ]
    )
    by_reason = await graph_store.graph.unresolved(reasons=["ambiguous_name"])
    assert [r["name"] for r in by_reason] == ["one"]
    by_node = await graph_store.graph.unresolved(node_ids=["b.py::g", "c.py::h"])
    assert {r["name"] for r in by_node} == {"two", "three"}
    assert len(await graph_store.graph.unresolved(limit=2)) == 2


async def test_replace_unresolved_swaps_the_whole_queue(graph_store):
    await graph_store.graph.replace_unresolved([_row("a.py::f", "gone")])
    await graph_store.graph.replace_unresolved([_row("b.py::g", "kept")])
    assert [r["name"] for r in await graph_store.graph.unresolved()] == ["kept"]


async def test_same_node_and_name_under_two_reasons_both_persist(graph_store):
    """The build emits a generic-label and a singleton-cluster row for the same cluster head, so
    the reason is part of the key."""
    await graph_store.graph.replace_unresolved(
        [
            _row(
                "m.py::f",
                "cluster-3",
                fact_kind=FactKind.NODE,
                reason=UnresolvedReason.GENERIC_LABEL,
            ),
            _row(
                "m.py::f",
                "cluster-3",
                fact_kind=FactKind.NODE,
                reason=UnresolvedReason.SINGLETON_CLUSTER,
            ),
        ]
    )
    assert len(await graph_store.graph.unresolved()) == 2


async def test_facts_returns_the_cached_json_or_none(graph_store):
    assert await graph_store.graph.facts("m.py") is None
    await graph_store.graph.set_facts("m.py", '{"path":"m.py"}', "abc")
    assert await graph_store.graph.facts("m.py") == '{"path":"m.py"}'


async def test_prune_leaves_the_unresolved_queue_alone(graph_store):
    """The queue is node-keyed and rebuilt wholesale by every build, so it is deliberately not in
    prune's per-path delete list. `gone.py` has to be a real indexed file or prune has nothing to
    prune and the test passes for the wrong reason."""
    await graph_store.files.upsert(
        IndexEntry(
            path="gone.py",
            sha256="abc",
            lines=1,
            language="python",
            role=FileRole.PRODUCTION,
            last_scanned=1.0,
        )
    )
    await graph_store.graph.set_facts("gone.py", "{}", "h1")
    await graph_store.graph.replace_unresolved([_row("gone.py::f", "handle")])
    assert await graph_store.prune(set()) == [
        "gone.py"
    ]  # the file row and its facts go
    assert await graph_store.graph.facts("gone.py") is None
    assert len(await graph_store.graph.unresolved()) == 1  # the queue row stays
