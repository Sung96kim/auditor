"""Flow search (spec §7): ``graph/cache.py``'s per-query index and ``graph/flow.py``'s walk.

The graph is synthetic (node and edge rows written straight into ``GraphDB.replace``) because a
real scan cannot produce a hub with more callers than ``hub_fan_in`` or ``graph_unresolved`` rows.
End-to-end coverage lives in ``test_cli_graph.py`` and ``test_mcp_graph.py``.
"""

import ast
from pathlib import Path
from typing import Any

import pytest

from auditor.config import GraphConfig
from auditor.database import IndexStore
from auditor.graph.cache import GraphCache, resolve_ids
from auditor.graph.flow import (
    DEFAULT_FLOW_LIMIT,
    DEFAULT_HUB_FAN_IN,
    FlowDirection,
    FlowNode,
    FlowOptions,
    build_flow,
)
from auditor.graph.model import (
    CallForm,
    EdgeKind,
    FactKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    UnresolvedReason,
    UnresolvedRow,
)
from auditor.graph.query import GraphQuery

_PACKAGE = Path(__file__).resolve().parents[2] / "auditor"


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


def test_the_query_api_does_not_import_the_flow_feature_for_its_cache():
    """``neighbors``/``_resolve_all`` are not flow queries; their index must not live in flow.py."""
    source = _PACKAGE / "graph" / "query.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    flow_imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "auditor.graph.flow"
        for alias in node.names
    }
    assert flow_imports == {
        "DEFAULT_OPTIONS",
        "FlowOptions",
        "FlowPayload",
        "build_flow",
    }


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


def test_dispatch_expansion_outward_lists_overriders(cache):
    result = build_flow(
        cache, "app/base.py::Handler.handle", options=FlowOptions(depth=1)
    )
    kids = _kids(result.root)
    assert kids["app/impl_a.py::AlphaHandler.handle"].edge == "dispatches_to"
    assert kids["app/impl_b.py::BetaHandler.handle"].edge == "dispatches_to"
    assert kids["app/util.py::helper"].edge == "calls"


def test_dispatch_expansion_inward_walks_to_the_base(cache):
    result = build_flow(
        cache,
        "app/impl_a.py::AlphaHandler.handle",
        options=FlowOptions(direction=FlowDirection.IN, depth=1),
    )
    assert _kids(result.root)["app/base.py::Handler.handle"].edge == "dispatches_to"


def test_registered_in_is_a_leaf_outward(cache):
    """app/reg.py calls helper, so only the _LEAF_EDGES guard keeps the registry a leaf."""
    result = build_flow(cache, "app/plug.py::plugin", options=FlowOptions(depth=3))
    registry = _kids(result.root)["app/reg.py"]
    assert registry.edge == "registered_in"
    assert registry.children == ()
    assert registry.kind == "module"


def test_registered_in_expands_as_dispatch_inward(cache):
    result = build_flow(
        cache, "app/reg.py", options=FlowOptions(direction=FlowDirection.IN, depth=2)
    )
    plugin = _kids(result.root)["app/plug.py::plugin"]
    assert plugin.edge == "dispatches_to"
    assert _kids(plugin)["app/engine.py::run"].edge == "calls"


def test_children_are_deduped_to_one_edge_per_child(cache):
    """--in --kinds registered_in reaches plugin twice: as a registrant and as its own dispatch."""
    result = build_flow(
        cache,
        "app/reg.py",
        options=FlowOptions(
            direction=FlowDirection.IN, depth=1, kinds=("registered_in",)
        ),
    )
    assert [(c.edge, c.id) for c in result.root.children] == [
        ("dispatches_to", "app/plug.py::plugin")
    ]


def test_in_direction_reverses_the_walk(cache):
    result = build_flow(
        cache,
        "app/util.py::helper",
        options=FlowOptions(direction=FlowDirection.IN, depth=2),
    )
    assert result.direction is FlowDirection.IN
    kids = _kids(result.root)
    assert set(kids) == {
        "app/engine.py::run",
        "app/base.py::Handler.handle",
        "app/reg.py",
    }
    assert _kids(kids["app/engine.py::run"])["app/cli.py::main"].edge == "calls"


def test_extra_kinds_are_followed(cache):
    """--kinds adds to the base set; it never replaces calls/callback_arg."""
    without = build_flow(
        cache, "app/impl_a.py::AlphaHandler.handle", options=FlowOptions(depth=1)
    )
    with_overrides = build_flow(
        cache,
        "app/impl_a.py::AlphaHandler.handle",
        options=FlowOptions(depth=1, kinds=("overrides",)),
    )
    assert without.root.children == ()
    assert [c.id for c in with_overrides.root.children] == [
        "app/base.py::Handler.handle"
    ]
    assert with_overrides.root.children[0].edge == "overrides"


def test_hub_fan_in_default_matches_the_config_default():
    """One number, two homes: a drift here silently changes every flow query."""
    assert GraphConfig().flow_hub_fan_in == DEFAULT_HUB_FAN_IN == 40


def test_tests_are_excluded_unless_asked_for(cache):
    hidden = build_flow(
        cache,
        "app/cli.py::main",
        options=FlowOptions(direction=FlowDirection.IN, depth=1),
    )
    shown = build_flow(
        cache,
        "app/cli.py::main",
        options=FlowOptions(direction=FlowDirection.IN, depth=1, include_tests=True),
    )
    assert hidden.root.children == ()
    assert [c.id for c in shown.root.children] == ["tests/test_cli.py::test_main"]


def test_stop_at_emits_the_node_marks_it_and_refuses_to_expand_it(cache):
    result = build_flow(
        cache,
        "app/cli.py::main",
        options=FlowOptions(depth=3, stop_at=("app/engine.py",)),
    )
    run = _kids(result.root)["app/engine.py::run"]
    assert run.stopped is True and run.children == ()
    assert _kids(result.root)["app/loop.py::ping"].stopped is False
    assert _kids(result.root)["app/loop.py::ping"].children != ()


def test_stop_at_matches_a_glob(cache):
    result = build_flow(
        cache, "app/cli.py::main", options=FlowOptions(depth=3, stop_at=("app/l*.py",))
    )
    assert _kids(result.root)["app/loop.py::ping"].children == ()
    assert _kids(result.root)["app/engine.py::run"].children != ()


def test_hub_is_elided_on_the_expansion_fan(cache):
    result = build_flow(
        cache, "app/cli.py::main", options=FlowOptions(depth=2, hub_fan_in=6)
    )
    hub = _kids(result.root)["app/hub.py::hub"]
    assert (hub.hub, hub.hub_kind) == (6, "expansion") and hub.children == ()
    assert _kids(result.root)["app/engine.py::run"].children != ()


def test_hub_is_elided_on_the_incoming_fan_even_when_the_expansion_is_small(cache):
    """settings is called from seven places and calls one, so only the fan-in count catches it."""
    result = build_flow(
        cache, "app/cli.py::main", options=FlowOptions(depth=4, hub_fan_in=7)
    )
    run = _kids(result.root)["app/engine.py::run"]
    settings = _kids(_kids(run)["app/util.py::helper"])["app/conf.py::settings"]
    assert (settings.hub, settings.hub_kind) == (7, "fan_in")
    assert settings.children == ()


def test_expand_hubs_keeps_the_count_and_expands_anyway(cache):
    result = build_flow(
        cache,
        "app/cli.py::main",
        options=FlowOptions(depth=2, hub_fan_in=6, expand_hubs=True),
    )
    hub = _kids(result.root)["app/hub.py::hub"]
    assert (hub.hub, hub.hub_kind) == (6, "expansion") and len(hub.children) == 6


def test_hub_fan_counts_dispatch_children(cache):
    """Handler.handle is called once and overridden twice, so a floor of 3 elides it."""
    result = build_flow(
        cache, "app/engine.py::run", options=FlowOptions(depth=2, hub_fan_in=3)
    )
    handle = _kids(result.root)["app/base.py::Handler.handle"]
    assert (handle.hub, handle.hub_kind) == (3, "fan_in") and handle.children == ()


def test_the_root_is_never_elided(cache):
    """A wide symbol is the usual reason to run a flow query; the start expands whatever its fan."""
    result = build_flow(
        cache, "app/conf.py::settings", options=FlowOptions(depth=1, hub_fan_in=2)
    )
    assert (result.root.hub, result.root.hub_kind) == (7, "fan_in")
    assert [c.id for c in result.root.children] == ["app/conf.py::_load"]


def test_limit_completes_shallow_levels_first_and_flags_truncation(cache):
    result = build_flow(
        cache,
        "app/cli.py::main",
        options=FlowOptions(depth=3, limit=3, expand_hubs=True),
    )
    assert result.truncated is True and result.limit == 3
    assert len(result.root.children) == 3
    assert all(c.children == () for c in result.root.children)


def test_an_unhit_limit_leaves_truncated_false(cache):
    result = build_flow(
        cache, "app/engine.py::run", options=FlowOptions(depth=1, limit=50)
    )
    assert result.truncated is False


def _queue_row(node_id: str, name: str, *, external: bool = False) -> UnresolvedRow:
    return UnresolvedRow(
        node_id=node_id,
        fact_kind=FactKind.ATTR_CALLEE,
        name=name,
        call_form=CallForm.ATTR,
        reason=UnresolvedReason.UNIMPORTABLE_NAME,
        externally_bound=external,
    )


def test_with_unresolved_hangs_rows_off_their_node(cache):
    result = build_flow(cache, "app/engine.py::run", options=FlowOptions(depth=1))
    marked = result.with_unresolved(
        {
            "app/engine.py::run": [
                _queue_row("app/engine.py::run", "dispatch").model_dump(mode="json"),
                _queue_row("app/engine.py::run", "search", external=True).model_dump(
                    mode="json"
                ),
            ]
        }
    )
    assert [(u.name, u.external) for u in marked.root.unresolved] == [
        ("dispatch", False),
        ("search", True),
    ]
    assert marked.root.unresolved[0].reason == "unimportable_name"
    assert all(c.unresolved == () for c in marked.root.children)
    assert result.root.unresolved == ()  # the walk's own result is untouched


def test_node_ids_lists_every_node_in_the_tree(cache):
    result = build_flow(cache, "app/engine.py::run", options=FlowOptions(depth=1))
    assert result.node_ids()[0] == "app/engine.py::run"
    assert set(result.node_ids()) == {"app/engine.py::run"} | {
        c.id for c in result.root.children
    }


async def test_query_flow_returns_the_payload_shape(flow_store):
    payload = await GraphQuery(flow_store).flow(
        "app/cli.py::main", FlowOptions(depth=2)
    )
    assert payload["symbol"] == "app/cli.py::main"
    assert payload["resolved"] == "app/cli.py::main"
    assert payload["ambiguous"] == []
    assert payload["direction"] == "out"
    assert payload["truncated"] is False and payload["limit"] == 200
    assert payload["modules"][0] == "app/cli.py"
    assert payload["root"]["id"] == "app/cli.py::main"
    assert isinstance(payload["root"]["children"], list)
    assert {c["id"] for c in payload["root"]["children"]} == {
        "app/engine.py::run",
        "app/hub.py::hub",
        "app/loop.py::ping",
    }


async def test_query_flow_resolves_a_bare_name_and_reports_ambiguity(flow_store):
    """Three methods are named handle; every rank is 0.0, so the sorted first one wins."""
    payload = await GraphQuery(flow_store).flow("handle", FlowOptions(depth=1))
    assert payload["resolved"] == "app/base.py::Handler.handle"
    assert payload["ambiguous"] == [
        "app/impl_a.py::AlphaHandler.handle",
        "app/impl_b.py::BetaHandler.handle",
    ]


async def test_query_flow_unknown_symbol_is_empty(flow_store):
    assert await GraphQuery(flow_store).flow("does_not_exist") == {}


async def test_query_flow_reads_the_queue_for_the_nodes_it_reached(flow_store):
    await flow_store.graph.replace_unresolved(
        [
            _queue_row("app/engine.py::run", "dispatch"),
            _queue_row("app/util.py::helper", "search", external=True),
            _queue_row("app/conf.py::_load", "offscreen"),
        ]
    )
    payload = await GraphQuery(flow_store).flow(
        "app/engine.py::run", FlowOptions(depth=1)
    )
    assert payload["root"]["unresolved"] == [
        {
            "name": "dispatch",
            "fact_kind": "attr_callee",
            "reason": "unimportable_name",
            "external": False,
        }
    ]
    helper = next(
        c for c in payload["root"]["children"] if c["id"] == "app/util.py::helper"
    )
    assert [(u["name"], u["external"]) for u in helper["unresolved"]] == [
        ("search", True)
    ]


async def test_query_flow_scopes_the_queue_read_to_the_reached_nodes(
    flow_store, monkeypatch
):
    """A whole-partition read would carry rows for nodes the tree never shows."""
    captured: dict[str, Any] = {}
    reader = flow_store.graph.unresolved

    async def spy(**kw):
        captured.update(kw)
        return await reader(**kw)

    monkeypatch.setattr(flow_store.graph, "unresolved", spy)
    payload = await GraphQuery(flow_store).flow(
        "app/engine.py::run", FlowOptions(depth=1)
    )
    assert set(captured["node_ids"]) == {"app/engine.py::run"} | {
        c["id"] for c in payload["root"]["children"]
    }


async def test_query_flow_loads_the_node_table_once(flow_store, monkeypatch):
    """Resolution reads the loaded cache, not a second nodes() round trip."""
    loads = 0
    nodes = flow_store.graph.nodes

    async def counted():
        nonlocal loads
        loads += 1
        return await nodes()

    monkeypatch.setattr(flow_store.graph, "nodes", counted)
    await GraphQuery(flow_store).flow("handle", FlowOptions(depth=1))
    assert loads == 1


async def test_query_flow_in_direction(flow_store):
    payload = await GraphQuery(flow_store).flow(
        "app/util.py::helper", FlowOptions(direction=FlowDirection.IN, depth=1)
    )
    assert payload["direction"] == "in"
    assert {c["id"] for c in payload["root"]["children"]} == {
        "app/engine.py::run",
        "app/base.py::Handler.handle",
        "app/reg.py",
    }
