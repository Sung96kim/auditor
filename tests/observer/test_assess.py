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
from auditor.graph.model import CallForm
from auditor.graph.refine.models import (
    Assessment,
    AssessmentDecision,
    NodePair,
    RefinementStatus,
    Spend,
)
from auditor.observer.assess import (
    CachedFile,
    EditedFile,
    GraphSnapshot,
    NodeDigest,
    PathOutcome,
    QueuePair,
    RefinementState,
    assess,
    assess_path,
    assess_unchanged,
    decide,
    stage_one,
)
from auditor.observer.budget import BudgetState, budget_state
from auditor.user_settings import BudgetConfig, SchedulingConfig

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


def _pair(node_id: str, name: str, **over) -> QueuePair:
    return QueuePair(node_id=node_id, name=name, **over)


def _snapshot(pairs=(), refinements=()) -> GraphSnapshot:
    return GraphSnapshot(pairs=tuple(pairs), refinements=tuple(refinements))


def _budget(*, fraction: float = 1.0, evaluated: bool = True) -> BudgetState:
    return budget_state(
        Spend(cost_usd=2.0 * (1.0 - fraction)),
        config=BudgetConfig(),
        evaluated=evaluated,
    )


def _assess(stage1, before, after, **over) -> Assessment:
    """Every case's call, so a new keyword is added once rather than in twenty places."""
    return assess(
        stage1,
        before=before,
        after=after,
        **{
            "scheduling": SchedulingConfig(),
            "budget": _budget(),
            "max_nodes_per_run": 12,
            **over,
        },
    )


_GET = "m.py::Store.get"
_STAGE1 = stage_one((_edited(_NEW_BARE_CALLEE),))


def _staled(anchors: tuple[str, ...]) -> tuple[GraphSnapshot, GraphSnapshot]:
    """One refinement, active before and stale after, anchored where the caller says."""
    return (
        _snapshot(
            refinements=(
                RefinementState(
                    refinement_id=1,
                    status=RefinementStatus.ACTIVE,
                    anchor_nodes=anchors,
                ),
            )
        ),
        _snapshot(
            refinements=(
                RefinementState(
                    refinement_id=1, status=RefinementStatus.STALE, anchor_nodes=anchors
                ),
            )
        ),
    )


def test_a_pair_absent_before_is_new():
    result = _assess(_STAGE1, _snapshot(), _snapshot((_pair(_GET, "widen"),)))
    assert result.new_pairs == (NodePair(node_id=_GET, name="widen"),)
    assert (result.decision, result.reason) == (
        AssessmentDecision.RUN,
        "1 new question",
    )


def test_a_pair_whose_offer_moved_is_new():
    """Spec 8.6: absent before, or `candidates_json`/`definers_json` changed."""
    before = _snapshot((_pair(_GET, "widen", candidates=("a.py::widen",)),))
    after = _snapshot(
        (_pair(_GET, "widen", candidates=("a.py::widen", "b.py::widen")),)
    )
    assert _assess(_STAGE1, before, after).new_pairs != ()


def test_an_unchanged_pair_is_not_new():
    same = (
        _pair(_GET, "widen", candidates=("a.py::widen",), definers=("a.py::widen",)),
    )
    assert _assess(_STAGE1, _snapshot(same), _snapshot(same)).new_pairs == ()


def test_an_externally_bound_pair_never_counts_as_new():
    after = _snapshot((_pair(_GET, "search", externally_bound=True),))
    result = _assess(_STAGE1, _snapshot(), after)
    assert result.new_pairs == ()
    assert (result.decision, result.reason) == (
        AssessmentDecision.SKIP,
        "no new questions",
    )


def test_a_pair_that_disappeared_is_resolved_unless_its_node_went_with_it():
    stage1 = stage_one((_edited(_DELETED_FN),))
    before = _snapshot((_pair(_GET, "widen"), _pair("m.py::load", "helper")))
    result = _assess(stage1, before, _snapshot())
    assert NodePair(node_id=_GET, name="widen") in result.resolved_pairs
    assert all(p.node_id != "m.py::load" for p in result.resolved_pairs)


def test_a_refinement_the_rebuild_staled_on_a_touched_anchor_counts():
    before, after = _staled((_GET,))
    result = _assess(_STAGE1, before, after)
    assert result.stale_refinements == (1,)
    assert (result.decision, result.reason) == (
        AssessmentDecision.RUN,
        "1 stale refinement",
    )


def test_a_refinement_staled_on_an_anchor_this_batch_never_touched_is_excluded():
    """`noop_builds` and Jaccard drift land the same status, and an edit cannot re-confirm them."""
    before, after = _staled(("z.py::other",))
    assert _assess(_STAGE1, before, after).stale_refinements == ()


def test_a_docstring_edit_rebuilds_and_finds_nothing():
    stage1 = stage_one((_edited(_DOCSTRING),))
    result = _assess(stage1, _snapshot(), _snapshot())
    assert stage1.needs_rebuild is True
    assert (result.decision, result.reason) == (
        AssessmentDecision.SKIP,
        "no new questions",
    )


def test_a_batch_that_moved_nothing_never_reaches_stage_two():
    result = assess_unchanged(stage_one((_edited(_COMMENT_ONLY),)))
    assert (result.decision, result.reason) == (
        AssessmentDecision.SKIP,
        "no structural change",
    )
    assert result.files == ("m.py",)


def test_a_changed_node_in_a_recent_flow_is_recorded_and_decides_nothing():
    """`_STAGE1` moves `_GET`, so the flow node is one this batch actually touched."""
    result = _assess(_STAGE1, _snapshot(), _snapshot(), flow_nodes=frozenset({_GET}))
    assert result.affected_flow == (_GET,)
    assert result.decision is AssessmentDecision.SKIP


def test_a_flow_node_this_batch_never_touched_is_not_recorded():
    """`affected_flow` is the intersection, not an echo of what the flow cache asked about."""
    result = _assess(
        _STAGE1, _snapshot(), _snapshot(), flow_nodes=frozenset({"z.py::other"})
    )
    assert result.affected_flow == ()


def test_pairs_beyond_the_cap_are_counted_not_dropped():
    after = _snapshot(_pair(f"m.py::n{i}", "widen") for i in range(15))
    result = _assess(_STAGE1, _snapshot(), after)
    assert len(result.new_pairs) == 15
    assert result.deferred_pairs == 3


_MIN2 = SchedulingConfig(min_new_unresolved=2)
_NO_STALE = SchedulingConfig(run_on_stale=False)
_MIN0 = SchedulingConfig(min_new_unresolved=0)
_SKIP, _RUN = AssessmentDecision.SKIP, AssessmentDecision.RUN


@pytest.mark.parametrize(
    ("scheduling", "new", "stale", "decision", "reason"),
    [
        (_MIN2, 1, 0, _SKIP, "1 new question, below the 2 the gate needs"),
        (_MIN2, 2, 0, _RUN, "2 new questions"),
        (_NO_STALE, 0, 1, _SKIP, "1 stale refinement, run_on_stale is off"),
        (SchedulingConfig(), 1, 1, _RUN, "1 new question and 1 stale refinement"),
        (_MIN0, 0, 0, _RUN, "0 new questions"),
        # the stale arm carried it alone, so the reason must not credit the clause that failed
        (_MIN2, 1, 1, _RUN, "1 stale refinement"),
    ],
)
def test_the_decision_rule(scheduling, new, stale, decision, reason):
    pairs = tuple(NodePair(node_id=f"m.py::n{i}", name="w") for i in range(new))
    result = decide(
        new_pairs=pairs,
        bounded_pairs=pairs,
        stale_refinements=tuple(range(1, stale + 1)),
        scheduling=scheduling,
        budget=_budget(),
    )
    assert (result.decision, result.reason) == (decision, reason)


def test_under_the_bar_with_an_eval_row_only_bare_and_self_pairs_count():
    after = _snapshot(
        (
            _pair(_GET, "widen", call_form=CallForm.ATTR),
            _pair(_GET, "narrow", call_form=CallForm.ATTR),
        )
    )
    result = _assess(_STAGE1, _snapshot(), after, budget=_budget(fraction=0.1))
    assert len(result.new_pairs) == 2
    assert (result.decision, result.reason) == (
        AssessmentDecision.SKIP,
        "low budget: 0 of 2 new questions are bare or self",
    )


def test_under_the_bar_with_an_eval_row_a_bare_pair_still_runs():
    after = _snapshot((_pair(_GET, "widen", call_form=CallForm.BARE),))
    result = _assess(_STAGE1, _snapshot(), after, budget=_budget(fraction=0.1))
    assert (result.decision, result.reason) == (
        AssessmentDecision.RUN,
        "1 new question",
    )


def test_under_the_bar_the_reason_counts_only_the_pairs_that_still_qualify():
    """`k`, not `n`: the narrowing exists so a clause that did not fire is not credited."""
    after = _snapshot(
        (
            _pair(_GET, "widen", call_form=CallForm.BARE),
            _pair(_GET, "narrow", call_form=CallForm.ATTR),
        )
    )
    result = _assess(_STAGE1, _snapshot(), after, budget=_budget(fraction=0.1))
    assert len(result.new_pairs) == 2
    assert (result.decision, result.reason) == (
        AssessmentDecision.RUN,
        "1 new question",
    )


def test_under_the_bar_with_no_eval_row_edit_runs_are_off():
    after = _snapshot((_pair(_GET, "widen", call_form=CallForm.BARE),))
    result = _assess(
        _STAGE1, _snapshot(), after, budget=_budget(fraction=0.1, evaluated=False)
    )
    assert (result.decision, result.reason) == (
        AssessmentDecision.SKIP,
        "low budget and no eval row for this runner",
    )


def test_under_the_bar_with_an_eval_row_the_stale_arm_still_runs():
    """Spec 8.6 narrows the new-pairs clause only; re-confirming is the cheapest run (P16)."""
    before, after = _staled((_GET,))
    assert _assess(_STAGE1, before, after, budget=_budget(fraction=0.1)).decision is (
        AssessmentDecision.RUN
    )
