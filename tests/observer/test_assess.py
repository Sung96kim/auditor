"""Spec 8.6's assessment, case by case.

Every case here is pure: `extract_file_facts` takes a source string, so a file's before and after
facts are built inline with no tmp path and no store. The one case spec 15 lists that is not here,
"the skipped row's `trigger_detail.assessment`", is a store round trip and lives in
`tests/graph/test_refine_service.py` (P17).
"""

from hashlib import sha256
from pathlib import Path

import pytest

from auditor.discovery import FileDiscovery
from auditor.graph.extract import extract_file_facts
from auditor.graph.hashes import file_hashes
from auditor.observer.assess import (
    CachedFile,
    EditedFile,
    NodeDigest,
    PathOutcome,
    assess_path,
    stage_one,
)

_BEFORE = """
def load(name):
    return name


class Store:
    def get(self, key):
        return load(key)
"""


def _edited(
    after: str | None, *, before: str | None = _BEFORE, path: str = "m.py"
) -> EditedFile:
    """One path as the loop hands it to Stage 1: the cache row it read, and the re-extraction."""
    cached = None
    if before is not None:
        facts = extract_file_facts(path, before, "production")
        cached = CachedFile(
            content_hash=sha256(before.encode()).hexdigest(),
            hashes=file_hashes(facts.nodes),
            node_hashes=tuple(NodeDigest.of(n) for n in facts.nodes),
        )
    if after is None:
        return EditedFile(path=path, cached=cached, content_hash=None, extracted=None)
    return EditedFile(
        path=path,
        cached=cached,
        content_hash=sha256(after.encode()).hexdigest(),
        extracted=extract_file_facts(path, after, "production"),
    )


_COMMENT_ONLY = _BEFORE.replace("def load(name):", "# a note\ndef load(name):")
_BLANK_ONLY = _BEFORE.replace("    return name", "    return name\n")
_FORMATTED = _BEFORE.replace("def get(self, key):", "def get(self, key):  ")
_DOCSTRING = _BEFORE.replace(
    "    return name", '    """Look it up."""\n    return name'
)
_NEW_BARE_CALLEE = _BEFORE.replace(
    "        return load(key)", "        return widen(load(key))"
)
_DELETED_FN = _BEFORE.replace("def load(name):\n    return name\n\n\n", "")
_RENAMED_FN = _BEFORE.replace("def load(", "def fetch(").replace(
    "load(key)", "fetch(key)"
)


@pytest.mark.parametrize(
    ("case", "after", "outcome"),
    [
        ("comment only", _COMMENT_ONLY, PathOutcome.UNCHANGED),
        ("blank line only", _BLANK_ONLY, PathOutcome.UNCHANGED),
        ("formatter run", _FORMATTED, PathOutcome.UNCHANGED),
        ("docstring only", _DOCSTRING, PathOutcome.FACTS_ONLY),
        ("new bare callee", _NEW_BARE_CALLEE, PathOutcome.TRUTH),
        ("deleted function", _DELETED_FN, PathOutcome.TRUTH),
        ("renamed function", _RENAMED_FN, PathOutcome.TRUTH),
    ],
)
def test_stage_one_classifies_one_edit(case, after, outcome):
    assert assess_path(_edited(after)).outcome is outcome, case


def test_identical_bytes_never_reach_the_extractor():
    """The content hash short circuit is what makes a Stop path set's repeats free (spec 8.2)."""
    edited = _edited(_BEFORE)
    verdict = assess_path(edited.model_copy(update={"extracted": None}))
    assert verdict.outcome is PathOutcome.UNCHANGED
    assert verdict.persist is False


#: `FileExtractor.extract` always emits a module node, so every expectation here carries `m.py`
_ALL_NODES = {"m.py", "m.py::load", "m.py::Store", "m.py::Store.get"}


def test_a_deleted_path_removes_every_node_it_had():
    verdict = assess_path(_edited(None))
    assert verdict.outcome is PathOutcome.REMOVED
    assert set(verdict.removed_nodes) == _ALL_NODES
    assert verdict.added_nodes == ()


def test_a_path_with_no_cached_row_adds_every_node_it_has():
    verdict = assess_path(_edited(_BEFORE, before=None))
    assert verdict.outcome is PathOutcome.ADDED
    assert set(verdict.added_nodes) == _ALL_NODES
    assert verdict.removed_nodes == ()


@pytest.mark.parametrize(
    ("case", "after", "moved"),
    [
        ("new bare callee", _NEW_BARE_CALLEE, {"m.py::Store.get"}),
        ("docstring only", _DOCSTRING, {"m.py::load"}),
        ("deleted function", _DELETED_FN, set()),
    ],
)
def test_facts_changed_nodes_names_only_the_nodes_whose_digest_moved(
    case, after, moved
):
    """The shared ids whose hashes moved, not every id the file still has (P21)."""
    assert set(assess_path(_edited(after)).facts_changed_nodes) == moved, case


def test_a_save_the_extractor_cannot_parse_writes_nothing():
    """Spec 15 case 25: `extract` swallows a SyntaxError and returns no nodes (P20)."""
    verdict = assess_path(_edited("def load(name:\n", path="m.py"))
    assert verdict.outcome is PathOutcome.UNPARSED
    assert verdict.persist is False
    assert (verdict.added_nodes, verdict.removed_nodes) == ((), ())


def test_a_re_edit_of_a_dirty_file_is_classified_by_hashes_not_by_the_short_circuit():
    """The first appearance skipped and persisted nothing, so the cache still holds `_BEFORE`."""
    first = assess_path(_edited(_COMMENT_ONLY))
    second = assess_path(_edited(_NEW_BARE_CALLEE))
    assert (first.outcome, first.persist) == (PathOutcome.UNCHANGED, False)
    assert second.outcome is PathOutcome.TRUTH
    assert "m.py::Store.get" in second.facts_changed_nodes


def test_a_rename_is_a_removal_and_an_addition_not_a_move():
    verdict = assess_path(_edited(_RENAMED_FN))
    assert "m.py::load" in verdict.removed_nodes
    assert "m.py::fetch" in verdict.added_nodes


def test_an_init_import_edit_moves_the_truth_hash():
    """`import_bindings` is a hashed fact, so an `__init__` re-export chain is structural."""
    verdict = assess_path(
        _edited("from .b import thing\n", before="", path="pkg/__init__.py")
    )
    assert verdict.outcome is PathOutcome.TRUTH


def test_a_test_file_edit_is_classified_like_any_other():
    """The caller-role gate lives in the queue writer, not here; stage 1 only reads hashes."""
    assert assess_path(_edited(_NEW_BARE_CALLEE, path="tests/test_m.py")).outcome is (
        PathOutcome.TRUTH
    )


def test_a_half_written_cache_pair_is_treated_as_a_truth_change():
    """`GraphDB.hashes` degrades to a miss; the conservative direction here is to rebuild."""
    edited = _edited(_DOCSTRING)
    assert edited.cached is not None
    blind = edited.cached.model_copy(update={"hashes": None})
    assert (
        assess_path(edited.model_copy(update={"cached": blind})).outcome
        is PathOutcome.TRUTH
    )


def test_a_batch_unions_what_its_paths_moved_and_names_what_to_persist():
    stage1 = stage_one(
        (
            _edited(_COMMENT_ONLY, path="a.py"),
            _edited(_DOCSTRING, path="b.py"),
            _edited(_NEW_BARE_CALLEE, path="c.py"),
        )
    )
    assert stage1.files == ("a.py", "b.py", "c.py")
    assert stage1.persist_paths == ("b.py", "c.py")
    assert stage1.needs_rebuild is True
    assert "c.py::Store.get" in stage1.facts_changed_nodes


def test_a_batch_where_nothing_moved_asks_for_no_persist_and_no_rebuild():
    stage1 = stage_one((_edited(_COMMENT_ONLY), _edited(_BLANK_ONLY, path="b.py")))
    assert stage1.persist_paths == ()
    assert stage1.needs_rebuild is False


def test_a_path_listed_twice_in_one_batch_is_classified_once():
    """A `PostToolUse` event and the Stop path set name the same file every time (P23)."""
    stage1 = stage_one((_edited(_NEW_BARE_CALLEE), _edited(_NEW_BARE_CALLEE)))
    assert stage1.files == ("m.py",)
    assert stage1.persist_paths == ("m.py",)


@pytest.mark.parametrize("rel", ["notes.md", "../outside.py"])
def test_stage_zero_drops_a_path_before_stage_one_ever_sees_it(
    tmp_path: Path, rel: str
):
    """Spec 8.6 stage 0 is the hook's filter; the loop and `/events` share this predicate."""
    assert FileDiscovery(tmp_path).auditable_rel(rel) is False


def test_stage_zero_keeps_a_deleted_path_so_stage_one_can_remove_its_nodes(
    tmp_path: Path,
):
    """The shape predicate is what makes the `REMOVED` branch reachable in production (P13)."""
    assert FileDiscovery(tmp_path).auditable_rel("pkg/gone.py") is True
