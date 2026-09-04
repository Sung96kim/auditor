import pytest

from auditor.graph.model import (
    CallForm,
    EdgeKind,
    FactKind,
    FileGraphFacts,
    GraphEdge,
    GraphNode,
    NodeKind,
    UnresolvedReason,
    UnresolvedRow,
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


@pytest.mark.parametrize(
    ("reason", "call_form", "expected"),
    [
        (UnresolvedReason.AMBIGUOUS_NAME, CallForm.ATTR, 1),
        (UnresolvedReason.UNIMPORTABLE_NAME, CallForm.BARE, 2),
        (UnresolvedReason.UNIMPORTABLE_NAME, CallForm.SELF, 2),
        (UnresolvedReason.UNIMPORTABLE_NAME, CallForm.ATTR, 3),
        (UnresolvedReason.TEXT_SPARSE, CallForm.BARE, 4),
    ],
)
def test_priority_is_derived_from_reason_and_call_form(reason, call_form, expected):
    """Drain order is the queue's whole contract; a row must not be constructible with a
    priority its reason and call form do not imply."""
    row = UnresolvedRow(
        node_id="m.py::f",
        fact_kind=FactKind.CALLEE,
        name="x",
        reason=reason,
        call_form=call_form,
    )
    assert row.priority == expected


def test_an_explicit_priority_survives_the_derivation():
    """S3's `flow_leaf` request bumps a row to 0; the validator fills a gap, it does not overrule
    a caller."""
    row = UnresolvedRow(
        node_id="m.py::f",
        fact_kind=FactKind.CALLEE,
        name="x",
        reason=UnresolvedReason.TEXT_SPARSE,
        priority=0,
    )
    assert row.priority == 0


def test_for_node_builds_a_build_pass_row():
    """The build pass's only constructor: a node-keyed row with no call form to speak of."""
    row = UnresolvedRow.for_node("m.py::f", "cluster-3", UnresolvedReason.GENERIC_LABEL)
    assert row.fact_kind is FactKind.NODE
    assert row.call_form is CallForm.BARE
    assert row.priority == 4
