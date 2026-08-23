"""Flow search (spec §7): the per-query cache and the directed traversal.

The graph is synthetic (node and edge rows written straight into ``GraphDB.replace``) because a
real scan cannot produce a hub with more callers than ``hub_fan_in`` or ``graph_unresolved`` rows.
End-to-end coverage lives in ``test_cli_graph.py`` and ``test_mcp_graph.py``.
"""

from typing import Any

import pytest

from auditor.database import IndexStore
from auditor.graph.flow import (
    DEFAULT_FLOW_LIMIT,
    FlowDirection,
    FlowNode,
    FlowOptions,
    GraphCache,
    build_flow,
    resolve_ids,
)
from auditor.graph.model import EdgeKind, GraphEdge, GraphNode, NodeKind
from auditor.graph.query import GraphQuery


def _node(
    nid: str, kind: str = "function", *, role: str = "production"
) -> dict[str, Any]:
    return {
        "node_id": nid,
        "kind": kind,
        "name": nid.split("::")[-1],
        "module": nid.split("::")[0],
        "role": role,
        "line": 1,
        "rank": 0.0,
        "cluster_id": None,
        "abstractness": 0.0,
        "text_sparse": 0,
    }


def _edge(src: str, dst: str, kind: str) -> dict[str, Any]:
    return {"src": src, "dst": dst, "kind": kind, "weight": 1.0}


LEAVES = [f"app/leaf.py::l{i}" for i in range(6)]

NODES: list[dict[str, Any]] = [
    _node("app/cli.py", "module"),
    _node("app/cli.py::main"),
    _node("app/engine.py::run"),
    _node("app/base.py::Handler", "class"),
    _node("app/base.py::Handler.handle", "method"),
    _node("app/impl_a.py::AlphaHandler.handle", "method"),
    _node("app/impl_b.py::BetaHandler.handle", "method"),
    _node("app/cb.py::on_done"),
    _node("app/reg.py", "module"),
    _node("app/plug.py::plugin"),
    _node("app/util.py::helper"),
    _node("app/conf.py::settings"),
    _node("app/conf.py::_load"),
    _node("app/loop.py::ping"),
    _node("app/loop.py::pong"),
    _node("app/hub.py::hub"),
    _node("tests/test_cli.py::test_main", role="test"),
    *[_node(nid) for nid in LEAVES],
]

EDGES: list[dict[str, Any]] = sorted(
    [
        _edge("app/cli.py::main", "app/engine.py::run", "calls"),
        _edge("app/cli.py::main", "app/loop.py::ping", "calls"),
        _edge("app/cli.py::main", "app/hub.py::hub", "calls"),
        _edge("app/engine.py::run", "app/base.py::Handler.handle", "calls"),
        _edge("app/engine.py::run", "app/plug.py::plugin", "calls"),
        _edge("app/engine.py::run", "app/util.py::helper", "calls"),
        _edge("app/engine.py::run", "app/cb.py::on_done", "callback_arg"),
        _edge("app/base.py::Handler.handle", "app/util.py::helper", "calls"),
        _edge(
            "app/impl_a.py::AlphaHandler.handle",
            "app/base.py::Handler.handle",
            "overrides",
        ),
        _edge(
            "app/impl_b.py::BetaHandler.handle",
            "app/base.py::Handler.handle",
            "overrides",
        ),
        _edge("app/plug.py::plugin", "app/reg.py", "registered_in"),
        # the registry module itself calls helper: without _LEAF_EDGES the walk would follow it
        _edge("app/reg.py", "app/util.py::helper", "calls"),
        _edge("app/util.py::helper", "app/conf.py::settings", "calls"),
        _edge("app/conf.py::settings", "app/conf.py::_load", "calls"),
        _edge("app/loop.py::ping", "app/loop.py::pong", "calls"),
        _edge("app/loop.py::pong", "app/loop.py::ping", "calls"),
        _edge("tests/test_cli.py::test_main", "app/cli.py::main", "calls"),
        *[_edge("app/hub.py::hub", nid, "calls") for nid in LEAVES],
        # settings is called from seven places and calls one: a fan-in hub, never an expansion one
        *[_edge(nid, "app/conf.py::settings", "calls") for nid in LEAVES],
    ],
    key=lambda e: (e["src"], e["dst"], e["kind"]),
)


@pytest.fixture
def cache() -> GraphCache:
    """The synthetic graph as the cache a query would load, with no database in the way."""
    return GraphCache(NODES, EDGES)


@pytest.fixture
async def flow_store(graph_store: IndexStore) -> IndexStore:
    """The same synthetic graph, persisted, so ``GraphQuery`` has something to read."""
    await graph_store.graph.replace(
        [
            GraphNode(
                id=n["node_id"],
                kind=NodeKind(n["kind"]),
                name=n["name"],
                module=n["module"],
                qualname=n["name"],
                role=n["role"],
                line=n["line"],
            )
            for n in NODES
        ],
        [
            GraphEdge(src=e["src"], dst=e["dst"], kind=EdgeKind(e["kind"]))
            for e in EDGES
        ],
        [],
    )
    return graph_store


async def test_cache_load_matches_a_hand_built_cache(flow_store, cache):
    """``GraphCache.load`` and the constructor must agree, so the pure tests bind the real thing."""
    loaded = await GraphCache.load(flow_store)
    assert set(loaded.nodes) == set(cache.nodes)
    assert loaded.out.keys() == cache.out.keys()
    assert loaded.inc.keys() == cache.inc.keys()


def test_cache_outgoing_and_incoming_filter_by_kind(cache):
    calls = frozenset({"calls"})
    assert [e["dst"] for e in cache.outgoing("app/engine.py::run", calls)] == [
        "app/base.py::Handler.handle",
        "app/plug.py::plugin",
        "app/util.py::helper",
    ]
    assert [
        e["dst"]
        for e in cache.outgoing("app/engine.py::run", frozenset({"callback_arg"}))
    ] == ["app/cb.py::on_done"]
    assert [
        e["src"]
        for e in cache.incoming("app/base.py::Handler.handle", frozenset({"overrides"}))
    ] == [
        "app/impl_a.py::AlphaHandler.handle",
        "app/impl_b.py::BetaHandler.handle",
    ]


def test_cache_incident_covers_both_directions_once(cache):
    """ping is called by main, calls pong, and is called back by pong: three edges, no dupes."""
    incident = cache.incident("app/loop.py::ping", frozenset({"calls"}))
    assert sorted((e["src"], e["dst"]) for e in incident) == [
        ("app/cli.py::main", "app/loop.py::ping"),
        ("app/loop.py::ping", "app/loop.py::pong"),
        ("app/loop.py::pong", "app/loop.py::ping"),
    ]


def test_resolve_ids_takes_the_exact_id_then_every_suffix_match(cache):
    assert resolve_ids(cache.nodes, "app/cli.py::main") == ["app/cli.py::main"]
    assert resolve_ids(cache.nodes, "handle") == [
        "app/base.py::Handler.handle",
        "app/impl_a.py::AlphaHandler.handle",
        "app/impl_b.py::BetaHandler.handle",
    ]
    assert resolve_ids(cache.nodes, "nope") == []


def test_cache_attribute_lookups_degrade_for_unknown_ids(cache):
    assert cache.kind("app/base.py::Handler") == "class"
    assert cache.module("app/base.py::Handler.handle") == "app/base.py"
    assert cache.role("tests/test_cli.py::test_main") == "test"
    assert cache.kind("nope.py::gone") == "?"
    assert cache.module("nope.py::gone") == "nope.py"
    assert cache.role("nope.py::gone") == "production"
    assert cache.rank("nope.py::gone") == 0.0


async def test_neighbors_uses_the_cache_not_edges_of(flow_store, monkeypatch):
    """The refactor's whole point: one load per query, no per-node ``edges_of`` round trip."""

    async def boom(*_args, **_kw):
        raise AssertionError("neighbors must not call edges_of")

    monkeypatch.setattr(flow_store.graph, "edges_of", boom)
    hits = await GraphQuery(flow_store).neighbors("main", depth=1)
    assert {h["id"] for h in hits} == {
        "app/engine.py::run",
        "app/loop.py::ping",
        "app/hub.py::hub",
        "tests/test_cli.py::test_main",
    }
    assert all(h["hops"] == 1 for h in hits)
    assert {h["direction"] for h in hits} == {"out", "in"}


def _kids(node: FlowNode) -> dict[str, FlowNode]:
    return {c.id: c for c in node.children}


def test_flow_option_defaults_are_the_spec_defaults():
    options = FlowOptions()
    assert options.direction is FlowDirection.OUT
    assert options.depth == 4
    assert options.limit == DEFAULT_FLOW_LIMIT == 200


def test_flow_root_has_no_edge_and_depth_zero(cache):
    result = build_flow(
        cache, "app/cli.py::main", options=FlowOptions(depth=1, include_tests=True)
    )
    assert result.root.id == "app/cli.py::main"
    assert result.root.edge is None and result.root.depth == 0
    assert result.root.kind == "function"
    assert result.direction is FlowDirection.OUT


def test_flow_follows_calls_and_callback_arg_outward(cache):
    result = build_flow(cache, "app/engine.py::run", options=FlowOptions(depth=1))
    kids = _kids(result.root)
    assert kids["app/cb.py::on_done"].edge == "callback_arg"
    assert kids["app/util.py::helper"].edge == "calls"
    assert all(c.depth == 1 for c in result.root.children)


def test_flow_children_are_ordered_by_edge_then_id(cache):
    result = build_flow(cache, "app/engine.py::run", options=FlowOptions(depth=1))
    assert [(c.edge, c.id) for c in result.root.children] == sorted(
        (c.edge, c.id) for c in result.root.children
    )


def test_flow_respects_depth(cache):
    shallow = build_flow(cache, "app/cli.py::main", options=FlowOptions(depth=1))
    deep = build_flow(cache, "app/cli.py::main", options=FlowOptions(depth=2))
    assert all(not c.children for c in shallow.root.children)
    run_shallow = _kids(shallow.root)["app/engine.py::run"]
    run_deep = _kids(deep.root)["app/engine.py::run"]
    assert run_shallow.children == () and run_deep.children != ()


def test_flow_marks_a_revisited_node_as_seen_ref(cache):
    """helper is reached under run at depth 2 and again under Handler.handle at depth 3."""
    result = build_flow(
        cache, "app/cli.py::main", options=FlowOptions(depth=3, expand_hubs=True)
    )
    run = _kids(result.root)["app/engine.py::run"]
    handle = _kids(run)["app/base.py::Handler.handle"]
    assert _kids(run)["app/util.py::helper"].seen_ref is False
    revisit = _kids(handle)["app/util.py::helper"]
    assert revisit.seen_ref is True and revisit.children == ()


def test_flow_marks_a_cycle_and_stops_there(cache):
    result = build_flow(
        cache, "app/cli.py::main", options=FlowOptions(depth=4, expand_hubs=True)
    )
    ping = _kids(result.root)["app/loop.py::ping"]
    pong = _kids(ping)["app/loop.py::pong"]
    back = _kids(pong)["app/loop.py::ping"]
    assert back.cycle is True and back.seen_ref is False and back.children == ()


def test_flow_modules_are_ordered_by_first_appearance(cache):
    result = build_flow(
        cache, "app/cli.py::main", options=FlowOptions(depth=2, expand_hubs=True)
    )
    assert result.modules[0] == "app/cli.py"
    assert "app/engine.py" in result.modules
    assert result.modules.index("app/engine.py") < result.modules.index("app/base.py")
    assert len(set(result.modules)) == len(result.modules)


def test_flow_is_deterministic(cache):
    options = FlowOptions(depth=3, expand_hubs=True)
    assert build_flow(cache, "app/cli.py::main", options=options) == build_flow(
        cache, "app/cli.py::main", options=options
    )


def test_flow_on_an_unknown_start_id_is_a_bare_root(cache):
    result = build_flow(cache, "nope.py::gone", options=FlowOptions(depth=3))
    assert result.root.id == "nope.py::gone" and result.root.kind == "?"
    assert result.root.children == () and result.modules == ("nope.py",)
