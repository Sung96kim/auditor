import pytest

from auditor.graph.flow import FlowNode, FlowPayload
from auditor.graph.model import (
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    Provenance,
)
from auditor.graph.viz import _FLOW_DOT_STYLE, build_payload, to_dot


async def test_payload_shape_and_mapping(viz_store):
    p = await build_payload(viz_store)
    assert set(p) == {"meta", "clusters", "nodes", "edges"}
    by_id = {n["id"]: n for n in p["nodes"]}
    assert by_id["m.py::Foo"]["type"] == "class"
    assert by_id["m.py::Foo.bar"]["type"] == "method"
    assert by_id["m.py"]["type"] == "module"
    foo = by_id["m.py::Foo"]
    assert {
        "id",
        "label",
        "type",
        "lang",
        "module",
        "line",
        "rank",
        "cluster",
        "role",
        "findings",
    } <= set(foo)
    assert p["edges"][0] == {
        "source": "m.py::Foo",
        "target": "m.py::Foo.bar",
        "kind": "contains",
        "weight": 1.0,
        "provenance": "deterministic",
        "confirmed": False,
    }
    assert p["clusters"][0]["label"] == "foo"


async def test_payload_deterministic_sorted(viz_store):
    a = await build_payload(viz_store)
    b = await build_payload(viz_store)
    assert a == b
    assert [n["id"] for n in a["nodes"]] == sorted(n["id"] for n in a["nodes"])


async def test_payload_node_cap(viz_store):
    p = await build_payload(viz_store, node_cap=2)
    assert len(p["nodes"]) <= 2
    assert p["meta"]["node_cap"] == 2


async def test_to_dot_deterministic(viz_store):
    p = await build_payload(viz_store)
    d1 = to_dot(p)
    d2 = to_dot(p)
    assert d1 == d2
    assert d1.startswith("digraph") and "m.py::Foo" in d1
    assert '"m.py::Foo" -> "m.py::Foo.bar"' in d1


async def test_to_dot_symbol_ego(viz_store):
    p = await build_payload(viz_store)
    d = to_dot(p, symbol="Foo", depth=1)
    assert "Foo" in d


async def test_to_dot_cluster_filter(viz_store):
    p = await build_payload(viz_store)
    d = to_dot(p, cluster="foo")
    assert "m.py::Foo" in d
    assert "m.py::Foo.bar" in d


async def test_to_dot_overview_sorted(viz_store):
    p = await build_payload(viz_store)
    d = to_dot(p)
    lines = d.splitlines()
    node_lines = [
        ln.strip() for ln in lines if ln.strip().startswith('"') and "->" not in ln
    ]
    node_ids = [ln.split('"')[1] for ln in node_lines]
    assert node_ids == sorted(node_ids)


async def test_node_cap_keeps_top_rank_not_alphabetical(graph_store):
    nodes = [
        GraphNode(
            id=f"a{i:03d}.py::f",
            kind=NodeKind.FUNCTION,
            name="f",
            module=f"a{i:03d}.py",
            qualname="f",
            role="production",
            rank=0.001 * i,
            line=1,
        )
        for i in range(10)
    ]
    nodes.append(
        GraphNode(
            id="zzz.py::hub",
            kind=NodeKind.FUNCTION,
            name="hub",
            module="zzz.py",
            qualname="hub",
            role="production",
            rank=0.99,
            line=1,
        )
    )
    await graph_store.repos.register(0.0)
    await graph_store.graph.replace(nodes, [], [])
    p = await build_payload(graph_store, node_cap=3)
    ids = {n["id"] for n in p["nodes"]}
    assert "zzz.py::hub" in ids  # highest rank kept despite late alphabet
    assert "a000.py::f" not in ids  # lowest rank dropped despite early alphabet
    assert len(p["nodes"]) == 3


def _flow_tree(*, truncated: bool = False) -> dict:
    """One walk result as raw JSON keys, so the fixture stays hand-written rather than a dump."""
    return {
        "symbol": "entry",
        "resolved": "m.py::entry",
        "direction": "out",
        "limit": 200,
        "truncated": truncated,
        "root": {
            "id": "m.py::entry",
            "kind": "function",
            "depth": 0,
            "edge": None,
            "children": [
                {
                    "id": "m.py::middle",
                    "kind": "function",
                    "depth": 1,
                    "edge": "calls",
                    "children": [
                        {
                            "id": "m.py::leaf",
                            "kind": "function",
                            "depth": 2,
                            "edge": "calls",
                            "children": [],
                        }
                    ],
                },
                {
                    "id": "m.py::cb",
                    "kind": "function",
                    "depth": 1,
                    "edge": "callback_arg",
                    "children": [],
                },
            ],
        },
    }


def test_the_dot_marks_name_real_flow_node_fields():
    """`_flow_declare` reads each mark off the model by name, so a renamed field has to fail
    here rather than at render time."""
    assert set(_FLOW_DOT_STYLE) <= set(FlowNode.model_fields)


def _flow(tree: dict) -> FlowPayload:
    """The hand-written tree as the model `to_dot` takes: a shape the walk cannot produce
    fails here instead of rendering."""
    return FlowPayload.model_validate(tree)


async def test_to_dot_flow_mode_ranks_each_depth(viz_store):
    p = await build_payload(viz_store)
    d = to_dot(p, flow=_flow(_flow_tree()))
    assert d.startswith("digraph flow")
    assert "// out, at most 200 nodes" in d
    assert "rankdir=LR" in d
    assert '{ rank=same; "m.py::entry"; }' in d
    assert '{ rank=same; "m.py::middle"; "m.py::cb"; }' in d
    assert '"m.py::entry" -> "m.py::middle" [label="calls"];' in d
    assert '"m.py::middle" -> "m.py::leaf" [label="calls"];' in d
    assert '"m.py::entry" -> "m.py::cb" [label="callback_arg"];' in d


async def test_to_dot_flow_mode_notes_a_truncated_walk(viz_store):
    """Export has no --limit, so the DOT has to say which cap produced the picture."""
    d = to_dot(await build_payload(viz_store), flow=_flow(_flow_tree(truncated=True)))
    assert "// out, at most 200 nodes, truncated" in d


async def test_to_dot_flow_mode_ranks_a_revisited_node_once(viz_store):
    """graphviz cannot honour two rank=same rows for one node: first depth seen wins."""
    tree = _flow_tree()
    tree["root"]["children"].append(
        {
            "id": "m.py::leaf",
            "kind": "function",
            "depth": 1,
            "edge": "calls",
            "children": [],
        }
    )
    d = to_dot(await build_payload(viz_store), flow=_flow(tree))
    assert '{ rank=same; "m.py::middle"; "m.py::cb"; }' in d
    assert '{ rank=same; "m.py::leaf"; }' in d
    declared = [
        ln for ln in d.splitlines() if ln.strip().startswith('"m.py::leaf" [label=')
    ]
    assert len(declared) == 1


@pytest.mark.parametrize(
    "mark, value, expect",
    [
        ("hub", {"count": 41, "kind": "fan_in", "collapsed": True}, "peripheries=2"),
        ("stopped", True, "dashed"),
        ("cycle", True, "orange"),
        ("seen_ref", True, "dotted"),
    ],
)
async def test_to_dot_flow_mode_carries_the_walk_marks(viz_store, mark, value, expect):
    """A pruned branch and a real leaf were the same box, so the picture said the path ended."""
    tree = _flow_tree()
    tree["root"]["children"][0][mark] = value
    d = to_dot(await build_payload(viz_store), flow=_flow(tree))
    assert expect in next(
        ln for ln in d.splitlines() if ln.startswith('  "m.py::middle" [')
    )


async def test_to_dot_flow_mode_counts_unresolved_leaves_in_the_label(viz_store):
    """The tree shows `? name` per unplaced call; the DOT dropped them entirely."""
    tree = _flow_tree()
    tree["root"]["children"][0]["unresolved"] = [
        {"name": n, "fact_kind": "attr_callee", "reason": "unimportable_name"}
        for n in ("dispatch", "run")
    ]
    d = to_dot(await build_payload(viz_store), flow=_flow(tree))
    assert '"m.py::middle" [label="middle\\n? 2"' in d


async def test_to_dot_flow_mode_is_deterministic(viz_store):
    p = await build_payload(viz_store)
    assert to_dot(p, flow=_flow(_flow_tree())) == to_dot(p, flow=_flow(_flow_tree()))


async def test_to_dot_flow_mode_on_a_lone_root(viz_store):
    """A walk that reached nothing still renders a valid graph, with no edge in it."""
    p = await build_payload(viz_store)
    tree = _flow_tree()
    tree["root"]["children"] = []
    d = to_dot(p, flow=_flow(tree))
    assert d.startswith("digraph flow") and "->" not in d


async def test_payload_carries_edge_and_node_provenance(graph_store):
    await graph_store.repos.register(0.0)
    await graph_store.graph.replace(
        [
            GraphNode(
                id="m.py::f",
                kind=NodeKind.FUNCTION,
                name="f",
                module="m.py",
                qualname="f",
                annotation="entry point",
            ),
            GraphNode(
                id="m.py::g",
                kind=NodeKind.FUNCTION,
                name="g",
                module="m.py",
                qualname="g",
                refined=True,
            ),
        ],
        [
            GraphEdge(
                src="m.py::f",
                dst="m.py::g",
                kind=EdgeKind.CALLS,
                provenance=Provenance.REFINED,
            )
        ],
        [],
    )
    p = await build_payload(graph_store)
    assert p["edges"][0]["provenance"] == "refined"
    assert p["edges"][0]["confirmed"] is False
    by_id = {n["id"]: n for n in p["nodes"]}
    assert by_id["m.py::f"]["annotation"] == "entry point"
    assert by_id["m.py::g"]["refined"] is True


async def test_the_dot_export_marks_a_refined_edge(graph_store):
    """A8: `graph export` is documented as showing provenance, so an overlay edge has to be
    distinguishable from one the resolver produced."""
    await graph_store.repos.register(0.0)
    nodes = [
        GraphNode(
            id=f"m.py::{name}",
            kind=NodeKind.FUNCTION,
            name=name,
            module="m.py",
            qualname=name,
        )
        for name in ("f", "g", "h")
    ]
    await graph_store.graph.replace(
        nodes,
        [
            GraphEdge(src="m.py::f", dst="m.py::g", kind=EdgeKind.CALLS),
            GraphEdge(
                src="m.py::f",
                dst="m.py::h",
                kind=EdgeKind.CALLS,
                provenance=Provenance.REFINED,
            ),
        ],
        [],
    )
    dot = to_dot(await build_payload(graph_store))
    assert '"m.py::f" -> "m.py::h" [label="calls" style="dashed"];' in dot
    assert '"m.py::f" -> "m.py::g" [label="calls"];' in dot


async def test_the_flow_dot_export_marks_a_refined_edge(viz_store):
    """The flow tree already carries the provenance; the DOT it renders has to show it."""
    flow = {
        "symbol": "Foo",
        "resolved": "m.py::Foo",
        "direction": "out",
        "root": {
            "id": "m.py::Foo",
            "kind": "class",
            "depth": 0,
            "children": [
                {
                    "id": "m.py::Foo.bar",
                    "kind": "method",
                    "depth": 1,
                    "edge": "calls",
                    "source": "refined",
                    "children": [],
                }
            ],
        },
    }
    dot = to_dot(await build_payload(viz_store), flow=_flow(flow))
    assert '"m.py::Foo" -> "m.py::Foo.bar" [label="calls" style="dashed"];' in dot
