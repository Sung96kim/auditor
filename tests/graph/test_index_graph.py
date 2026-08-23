import inspect
import re

import pytest

from auditor.database.graph import GraphDB
from auditor.graph.hashes import FileHashes
from auditor.graph.model import (
    CallForm,
    EdgeKind,
    EdgeSource,
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


def test_edge_provenance_reads_a_column_the_loader_selects():
    """`flow._source` degrades to "deterministic" on a missing key, so adding the S4 `source`
    column without widening this SELECT would look like a walk regression."""
    selected = set(
        re.search(
            r"SELECT ([\w, ]+) FROM graph_edges", inspect.getsource(GraphDB.all_edges)
        )
        .group(1)
        .split(", ")
    )
    declared = {c.name for c in GraphDB.TABLES["graph_edges"].cols}
    assert ("source" in selected) == ("source" in declared)


async def test_edges_of_is_ordered_like_all_edges(graph_store):
    """`neighbors` reads one node at depth 1 and the whole partition above it; unordered rows
    made the reported ``edge``/``direction`` depend on which path was taken."""
    nodes = [_n("a"), _n("b"), _n("c")]
    edges = [
        GraphEdge(src="a", dst="c", kind=EdgeKind.CALLS, weight=1.0),
        GraphEdge(src="a", dst="b", kind=EdgeKind.IMPORTS, weight=1.0),
        GraphEdge(src="b", dst="a", kind=EdgeKind.CALLS, weight=1.0),
    ]
    await graph_store.graph.replace(nodes, edges, [])
    rows = await graph_store.graph.edges_of("a", None)
    assert [(r["src"], r["dst"], r["kind"]) for r in rows] == [
        ("a", "b", "imports"),
        ("a", "c", "calls"),
        ("b", "a", "calls"),
    ]
    assert rows == await graph_store.graph.edges_of("a", None)


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


async def test_an_externally_bound_row_sinks_below_an_equal_priority_real_one(
    graph_store,
):
    """Those rows are display only, so they must not push a briefable row off the first page."""
    await graph_store.graph.replace_unresolved(
        [
            _row("a.py::f", "dimmed", priority=2, externally_bound=True),
            _row("z.py::f", "real", priority=2),
        ]
    )
    rows = await graph_store.graph.unresolved()
    assert [r["name"] for r in rows] == ["real", "dimmed"]
    kept = await graph_store.graph.unresolved(external=False)
    assert [r["name"] for r in kept] == ["real"]


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


async def test_unresolved_applies_the_limit_after_both_filters(graph_store):
    """The limit has to count rows the caller sees; filtering in Python after an unbounded read
    also means the whole partition lands in memory."""
    await graph_store.graph.replace_unresolved(
        [
            _row(
                "a.py::f",
                "one",
                call_form=CallForm.ATTR,
                reason=UnresolvedReason.AMBIGUOUS_NAME,
            ),
            _row("b.py::g", "two", call_form=CallForm.BARE),
            _row("c.py::h", "three", call_form=CallForm.BARE),
            _row("d.py::i", "four", call_form=CallForm.BARE),
        ]
    )
    assert [r["name"] for r in await graph_store.graph.unresolved(limit=2)] == [
        "one",
        "two",
    ]
    rows = await graph_store.graph.unresolved(call_forms=["bare"], limit=2)
    assert [r["name"] for r in rows] == ["two", "three"]


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


async def test_replace_unresolved_tolerates_a_duplicate_key(graph_store):
    """A build must not abort because two row sources agreed on a key; last write wins."""
    dup = _row("m.py::f", "handle")
    await graph_store.graph.replace_unresolved([dup, dup])
    assert len(await graph_store.graph.unresolved()) == 1


async def test_replace_tolerates_a_duplicate_node_and_cluster_key(graph_store):
    """Same hardening on the node/cluster swap: a repeated key is a last write, not an
    IntegrityError that takes the whole build down. `graph_edges` has no key to collide on."""
    node = _n("x")
    cluster = GraphCluster(cluster_id=1, label="user", member_count=1)
    await graph_store.graph.replace([node, node], [], [cluster, cluster])
    assert len(await graph_store.graph.nodes()) == 1
    assert len(await graph_store.graph.clusters()) == 1


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


async def test_set_facts_stores_and_returns_the_file_hashes(graph_store):
    assert await graph_store.graph.hashes("m.py") is None
    hashes = FileHashes(truth="t1", facts="f1")
    await graph_store.graph.set_facts("m.py", '{"path":"m.py"}', "abc", hashes)
    assert await graph_store.graph.hashes("m.py") == hashes


async def test_set_facts_without_hashes_stores_nothing_to_compare(graph_store):
    """Callers that hold no parsed facts (ad-hoc writes, tests) leave the columns NULL."""
    await graph_store.graph.set_facts("m.py", '{"path":"m.py"}', "abc")
    assert await graph_store.graph.hashes("m.py") is None
    assert (
        await graph_store.graph.facts_hash("m.py") == "abc"
    )  # content hash still there


async def test_edges_round_trip_their_provenance(graph_store):
    edges = [
        GraphEdge(src="a", dst="b", kind=EdgeKind.CALLS),
        GraphEdge(
            src="a",
            dst="c",
            kind=EdgeKind.CALLS,
            source=EdgeSource.REFINED,
            confirmed=True,
        ),
    ]
    await graph_store.graph.replace([_n("a"), _n("b"), _n("c")], edges, [])
    by_dst = {e["dst"]: e for e in await graph_store.graph.all_edges()}
    assert by_dst["b"]["source"] == "deterministic"
    assert by_dst["b"]["confirmed"] == 0
    assert by_dst["c"]["source"] == "refined"
    assert by_dst["c"]["confirmed"] == 1
    # edges_of has to carry it too: `graph neighbors` and the flow tree both read that shape
    hop = {e["dst"]: e["source"] for e in await graph_store.graph.edges_of("a", None)}
    assert hop == {"b": "deterministic", "c": "refined"}


async def test_a_repeated_edge_key_collapses_to_one_row(graph_store):
    """The unique index is what lets a refinement overwrite a deterministic edge in place."""
    await graph_store.graph.replace(
        [_n("a"), _n("b")],
        [
            GraphEdge(src="a", dst="b", kind=EdgeKind.CALLS),
            GraphEdge(src="a", dst="b", kind=EdgeKind.CALLS, source=EdgeSource.REFINED),
        ],
        [],
    )
    rows = await graph_store.graph.all_edges()
    assert len(rows) == 1
    assert rows[0]["source"] == "refined"  # last write wins


async def test_node_and_cluster_provenance_round_trip(graph_store):
    await graph_store.graph.replace(
        [_n("a", cluster_id=1, refined=True, annotation="the retry path")],
        [],
        [
            GraphCluster(
                cluster_id=1,
                label="retry",
                member_count=1,
                label_source=EdgeSource.REFINED,
            )
        ],
    )
    node = await graph_store.graph.node("a")
    assert (node["refined"], node["annotation"]) == (1, "the retry path")
    (cluster,) = await graph_store.graph.clusters()
    assert cluster["label_source"] == "refined"
    (member,) = await graph_store.graph.cluster_members(1)
    assert (member["refined"], member["annotation"]) == (1, "the retry path")
