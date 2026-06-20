from auditor.graph.model import (
    EdgeKind,
    FileGraphFacts,
    GraphEdge,
    GraphNode,
    NodeKind,
)


def _node(**kw) -> GraphNode:
    base = dict(
        id="m.py::f",
        kind=NodeKind.FUNCTION,
        name="f",
        module="m.py",
        qualname="f",
        doc_tokens=("read", "user"),
        callees=("get",),
        param_types=("User",),
        decorators=(),
        bases=(),
        method_names=(),
        is_hof=False,
        is_stub=False,
        line=1,
        role="production",
    )
    base.update(kw)
    return GraphNode(**base)


def test_node_is_frozen_and_serializes():
    n = _node()
    assert n.id == "m.py::f" and n.kind == NodeKind.FUNCTION
    assert n.model_dump(mode="json")["doc_tokens"] == ["read", "user"]


def test_edge_defaults_weight_one():
    e = GraphEdge(src="a", dst="b", kind=EdgeKind.CALLS)
    assert e.weight == 1.0 and e.kind == "calls"


def test_facts_roundtrip_json():
    facts = FileGraphFacts(path="m.py", role="production", nodes=[_node()])
    dumped = facts.model_dump_json()
    back = FileGraphFacts.model_validate_json(dumped)
    assert back.nodes[0].id == "m.py::f"
