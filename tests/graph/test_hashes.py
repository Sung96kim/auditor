"""What the two node hashes move on, and what they deliberately ignore (spec 5.5, 8.6 stage 1)."""

import pytest

from auditor.graph.extract import extract_file_facts
from auditor.graph.hashes import (
    FACTS_FACT_FIELDS,
    TRUTH_FACT_FIELDS,
    file_hashes,
    node_facts_sha,
    node_truth_sha,
)
from auditor.graph.model import UNION_FACT_FIELDS, GraphNode, NodeKind

SRC = 'def handle(job):\n    """Run one job."""\n    return job.start()\n'

#: one real module, edited below the way a working day edits it
BASE = '''import json


class Registry:
    pass


def handle(job, retries: int) -> str:
    """Run one job and report."""
    ledger = Registry()
    ledger.record(job)
    return json.dumps(job.start(retries))
'''

_OVERLAY_FIELDS = ("refined", "annotation")


def _node(**kw) -> GraphNode:
    return GraphNode(
        id="m.py::handle",
        kind=NodeKind.FUNCTION,
        name="handle",
        module="m.py",
        qualname="handle",
        **kw,
    )


def _handle(source: str) -> GraphNode:
    """The `handle` node as the extractor really produces it, not a hand-built model."""
    nodes = extract_file_facts("m.py", source, "production").nodes
    return next(n for n in nodes if n.id == "m.py::handle")


def test_the_two_field_lists_nest_inside_the_union_fields():
    """`facts_sha` is the truth inputs plus `doc_tokens`; `local_names` is in neither."""
    assert set(UNION_FACT_FIELDS) - set(FACTS_FACT_FIELDS) == {"local_names"}
    assert set(FACTS_FACT_FIELDS) - set(TRUTH_FACT_FIELDS) == {"doc_tokens"}
    assert set(TRUTH_FACT_FIELDS) < set(FACTS_FACT_FIELDS) < set(UNION_FACT_FIELDS)


def test_the_overlay_fields_are_outside_both_hashes():
    """A refinement that writes `annotation` must not move the truth_sha it anchors on, or the
    next build sees drift against the anchor the refinement itself produced."""
    assert not set(_OVERLAY_FIELDS) & set(UNION_FACT_FIELDS)


@pytest.mark.parametrize(
    "field, value",
    [
        ("line", 42),
        ("role", "test"),
        ("rank", 0.9),
        ("cluster_id", 3),
        ("abstractness", 0.5),
        ("text_sparse", True),
        ("refined", True),
        ("annotation", "entry point"),
    ],
)
def test_neither_hash_moves_on_a_non_fact_field(field, value):
    """Line shifts, a role reclassification, every build-pass field and the overlay fields leave
    both hashes alone."""
    base, moved = _node(), _node(**{field: value})
    assert node_truth_sha(base) == node_truth_sha(moved)
    assert node_facts_sha(base) == node_facts_sha(moved)


@pytest.mark.parametrize(
    "edit, facts_moves",
    [
        (
            (
                "    ledger = Registry()",
                "    # keep the ledger local\n    ledger = Registry()",
            ),
            False,
        ),
        (("    ledger = Registry()", "\n    ledger = Registry()"), False),
        (
            (
                "    return json.dumps(job.start(retries))",
                "    return json.dumps(\n        job.start(retries),\n    )",
            ),
            False,
        ),
        (('"""Run one job and report."""', '"""Execute a single task."""'), True),
        (("ledger", "journal"), True),
        (("retries", "attempts"), True),
    ],
    ids=[
        "comment",
        "blank_line",
        "reformatted",
        "docstring",
        "renamed_local",
        "renamed_parameter",
    ],
)
def test_an_edit_with_no_structural_change_leaves_the_truth_hash_alone(
    edit, facts_moves
):
    """Spec 8.6 stage 1: a refinement survives a comment, a reformat and a rename in its own file.

    `facts_sha` still moves on a docstring edit and on a rename, because `doc_tokens` carry every
    identifier the body mentions and similarity edges read them.
    """
    source = BASE.replace(*edit)
    assert source != BASE  # the edit really landed in the source
    base, edited = _handle(BASE), _handle(source)
    assert node_truth_sha(base) == node_truth_sha(edited)
    assert (node_facts_sha(base) != node_facts_sha(edited)) is facts_moves


@pytest.mark.parametrize(
    "edit",
    [
        ("    ledger.record(job)", "    ledger.record(job)\n    json.loads('{}')"),
        ("retries: int", "retries: float"),
        ("def handle(", "@staticmethod\ndef handle("),
        (
            "    ledger = Registry()",
            "    def inner():\n        return 1\n\n    ledger = Registry()",
        ),
    ],
    ids=["new_call", "changed_annotation", "new_decorator", "new_nested_def"],
)
def test_a_structural_edit_moves_both_hashes(edit):
    base, edited = _handle(BASE), _handle(BASE.replace(*edit))
    assert node_truth_sha(base) != node_truth_sha(edited)
    assert node_facts_sha(base) != node_facts_sha(edited)


def _swap(value, names: frozenset[str]):
    """``value`` with every one of ``names`` replaced by a fresh identifier, at any depth."""
    if isinstance(value, str):
        return f"{value}_renamed" if value in names else value
    if isinstance(value, tuple):
        return tuple(_swap(item, names) for item in value)
    return value


def test_no_truth_field_carries_a_name_the_node_binds():
    """The property every rename case rests on, over a whole module's real facts: swapping a
    node's own names for fresh ones anywhere in its facts leaves the truth hash alone."""
    for node in extract_file_facts("m.py", BASE, "production").nodes:
        names = frozenset(node.local_names)
        if not names:
            continue
        renamed = node.model_copy(
            update={f: _swap(getattr(node, f), names) for f in UNION_FACT_FIELDS}
        )
        assert node_truth_sha(node) == node_truth_sha(renamed)


def test_a_docstring_edit_moves_only_the_facts_hash():
    base = _node(callees=("start",), doc_tokens=("run", "job"))
    edited = base.model_copy(update={"doc_tokens": ("execute", "task")})
    assert node_truth_sha(base) == node_truth_sha(edited)
    assert node_facts_sha(base) != node_facts_sha(edited)


@pytest.mark.parametrize(
    "field, value",
    [
        ("callees", ("start", "stop")),
        ("attr_callees", ((None, "start", True),)),
        ("bases", ("Base",)),
        ("is_hof", True),
        ("is_stub", True),
    ],
)
def test_a_structural_fact_moves_both_hashes(field, value):
    base = _node(callees=("start",))
    moved = _node(
        **{"callees": ("start",), field: value}
    )  # one dict, so `callees` can be the case
    assert node_truth_sha(base) != node_truth_sha(moved)
    assert node_facts_sha(base) != node_facts_sha(moved)


def test_the_kind_is_part_of_the_hash():
    fn = _node()
    cls = fn.model_copy(update={"kind": NodeKind.CLASS})
    assert node_truth_sha(fn) != node_truth_sha(cls)


def test_file_hashes_move_when_a_node_is_added_or_removed():
    """Rolled over the sorted (node_id, hash) set, so an added node moves the file hash even
    though every surviving node is untouched."""
    nodes = extract_file_facts("m.py", SRC, "production").nodes
    assert len(nodes) >= 2
    whole = file_hashes(nodes)
    assert file_hashes(nodes) == whole  # deterministic
    assert file_hashes(nodes[:-1]) != whole
    assert file_hashes(list(reversed(nodes))) == whole  # order independent


def test_file_hashes_are_empty_stable():
    a, b = file_hashes([]), file_hashes([])
    assert a == b
    assert a.truth and a.facts  # a hash of nothing is still a hash
