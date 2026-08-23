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


def _node(**kw) -> GraphNode:
    return GraphNode(
        id="m.py::handle",
        kind=NodeKind.FUNCTION,
        name="handle",
        module="m.py",
        qualname="handle",
        **kw,
    )


def test_the_two_field_lists_nest_inside_the_union_fields():
    """`facts_sha` is the truth inputs plus `doc_tokens`; `local_names` is in neither."""
    assert set(UNION_FACT_FIELDS) - set(FACTS_FACT_FIELDS) == {"local_names"}
    assert set(FACTS_FACT_FIELDS) - set(TRUTH_FACT_FIELDS) == {"doc_tokens"}
    assert set(TRUTH_FACT_FIELDS) < set(FACTS_FACT_FIELDS) < set(UNION_FACT_FIELDS)


@pytest.mark.parametrize(
    "field, value",
    [
        ("line", 42),
        ("role", "test"),
        ("rank", 0.9),
        ("cluster_id", 3),
        ("abstractness", 0.5),
        ("text_sparse", True),
    ],
)
def test_neither_hash_moves_on_a_non_fact_field(field, value):
    """Line shifts, a role reclassification and every build-pass field leave both hashes alone."""
    base, moved = _node(), _node(**{field: value})
    assert node_truth_sha(base) == node_truth_sha(moved)
    assert node_facts_sha(base) == node_facts_sha(moved)


def test_neither_hash_covers_a_renamed_local():
    """Spec 8.6 stage 1 skips a rename with no persist, no rebuild and no run, which only holds
    while `local_names` is outside both hashes. It gates queue rows, and every build rebuilds
    the whole queue anyway."""
    base = _node(callees=("start",), local_names=("job",))
    renamed = base.model_copy(update={"local_names": ("task",)})
    assert node_truth_sha(base) == node_truth_sha(renamed)
    assert node_facts_sha(base) == node_facts_sha(renamed)


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
