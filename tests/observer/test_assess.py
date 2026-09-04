"""Spec 8.6's assessment, case by case.

Every case here is pure: `extract_file_facts` takes a source string, so a file's before and after
facts are built inline with no tmp path and no store. The one case spec 15 lists that is not here,
"the skipped row's `trigger_detail.assessment`", is a store round trip and lives in
`tests/graph/test_refine_service.py` (P17).
"""

from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from auditor.discovery import FileDiscovery
from auditor.graph.extract import extract_file_facts
from auditor.graph.hashes import file_hashes
from auditor.graph.model import CallForm, FactKind, UnresolvedReason, UnresolvedRow
from auditor.graph.refine.models import (
    Assessment,
    AssessmentDecision,
    BatchKind,
    Decision,
    NodePair,
    Refinement,
    RefinementKind,
    RefinementPayload,
    RefinementStatus,
    RefinementTarget,
    Spend,
)
from auditor.observer.assess import (
    Bars,
    CachedFile,
    EditedFile,
    GraphSnapshot,
    NodeDigest,
    PathOutcome,
    QueuePair,
    RefinementState,
    Stage1,
    assess,
    assess_path,
    assess_unchanged,
    bars_for,
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
    after: str | None,
    *,
    before: str | None = _BEFORE,
    path: str = "m.py",
    role: str = "production",
) -> EditedFile:
    """One path as the loop hands it to Stage 1: the cache row it read, and the re-extraction."""
    cached = None
    if before is not None:
        facts = extract_file_facts(path, before, role)
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
        extracted=extract_file_facts(path, after, role),
    )


_COMMENT_ONLY = _BEFORE.replace("def load(name):", "# a note\ndef load(name):")
_BLANK_ONLY = _BEFORE.replace("    return name", "    return name\n")
_FORMATTED = _BEFORE.replace(
    "        return load(key)", "        return load(\n            key,\n        )"
)
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


#: the whole source of each case is a parameter, so the ids are given by hand: the default would
#: put a whole module body in every collected id and in every row of spec 15's map
_EDIT_CASES = {
    "comment only": (_COMMENT_ONLY, PathOutcome.UNCHANGED),
    "blank line only": (_BLANK_ONLY, PathOutcome.UNCHANGED),
    "formatter run": (_FORMATTED, PathOutcome.UNCHANGED),
    "docstring only": (_DOCSTRING, PathOutcome.FACTS_ONLY),
    "new bare callee": (_NEW_BARE_CALLEE, PathOutcome.TRUTH),
    "deleted function": (_DELETED_FN, PathOutcome.TRUTH),
    "renamed function": (_RENAMED_FN, PathOutcome.TRUTH),
}


@pytest.mark.parametrize(
    ("after", "outcome"), _EDIT_CASES.values(), ids=list(_EDIT_CASES)
)
def test_stage_one_classifies_one_edit(after, outcome):
    assert assess_path(_edited(after)).outcome is outcome


def test_identical_bytes_never_reach_the_extractor():
    """The content hash short circuit is what makes a Stop path set's repeats free (spec 8.2).

    The facts handed in are the ones a `TRUTH` verdict is made of, so only the hash arm can
    return `UNCHANGED` here: without it this input classifies as a structural change.
    """
    loud = extract_file_facts("m.py", _NEW_BARE_CALLEE, "production")
    verdict = assess_path(_edited(_BEFORE).model_copy(update={"extracted": loud}))
    assert verdict.outcome is PathOutcome.UNCHANGED
    assert verdict.persist is False


def test_bytes_that_moved_with_no_facts_to_compare_are_not_called_unchanged():
    """No extraction is `UNPARSED`, never a silent `UNCHANGED` that leaves the cache behind."""
    verdict = assess_path(
        _edited(_NEW_BARE_CALLEE).model_copy(update={"extracted": None})
    )
    assert verdict.outcome is PathOutcome.UNPARSED
    assert verdict.persist is False


def test_a_new_path_with_no_facts_to_compare_is_not_added_with_nothing_in_it():
    """One rule for both: `added` with empty facts would write the next edit into the mid-save
    hazard `UNPARSED` exists to close."""
    fresh = _edited(_BEFORE, before=None).model_copy(update={"extracted": None})
    verdict = assess_path(fresh)
    assert verdict.outcome is PathOutcome.UNPARSED
    assert (verdict.persist, verdict.added_nodes) == (False, ())


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


_MOVED_CASES = {
    "new bare callee": (_NEW_BARE_CALLEE, {"m.py::Store.get"}),
    "docstring only": (_DOCSTRING, {"m.py::load"}),
    "deleted function": (_DELETED_FN, set()),
}


@pytest.mark.parametrize(
    ("after", "moved"), _MOVED_CASES.values(), ids=list(_MOVED_CASES)
)
def test_facts_changed_nodes_names_only_the_nodes_whose_digest_moved(after, moved):
    """The shared ids whose hashes moved, not every id the file still has (P21)."""
    assert set(assess_path(_edited(after)).facts_changed_nodes) == moved


def test_a_save_the_extractor_cannot_parse_writes_nothing():
    """Spec 15 case 25: `extract` swallows a SyntaxError and returns no nodes (P20)."""
    verdict = assess_path(_edited("def load(name:\n", path="m.py"))
    assert verdict.outcome is PathOutcome.UNPARSED
    assert verdict.persist is False
    assert (verdict.added_nodes, verdict.removed_nodes) == ((), ())


def test_a_re_edit_of_a_dirty_file_is_classified_by_hashes_not_by_the_short_circuit():
    """The first appearance persisted nothing, so the cache the second reads still holds
    `_BEFORE` and the second edit is classified against it, not against the first."""
    first_edit = _edited(_COMMENT_ONLY)
    first = assess_path(first_edit)
    assert (first.outcome, first.persist) == (PathOutcome.UNCHANGED, False)
    second = assess_path(
        first_edit.model_copy(
            update={
                "content_hash": sha256(_NEW_BARE_CALLEE.encode()).hexdigest(),
                "extracted": extract_file_facts("m.py", _NEW_BARE_CALLEE, "production"),
            }
        )
    )
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
    """The caller-role gate lives in the queue writer, not here; stage 1 only reads hashes, and
    `role` is not one of them, so a test file moves the same hashes a production file does."""
    as_test = _edited(_NEW_BARE_CALLEE, path="tests/test_m.py", role="test")
    as_production = _edited(_NEW_BARE_CALLEE, path="tests/test_m.py", role="production")
    assert as_test.cached == as_production.cached
    verdict = assess_path(as_test)
    assert verdict.outcome is PathOutcome.TRUTH
    assert verdict.facts_changed_nodes == ("tests/test_m.py::Store.get",)


def test_a_cache_row_with_no_per_node_digests_still_refuses_a_mid_edit_save():
    """`facts_hash` and `hashes` are the two store reads a loop reaches for first, so
    `node_hashes` is empty on that path and `UNPARSED` cannot key off it."""
    full = _edited("def load(name:\n")
    assert full.cached is not None
    thin = full.cached.model_copy(update={"node_hashes": ()})
    stage1 = stage_one((full.model_copy(update={"cached": thin}),))
    verdict = stage1.verdicts[0]
    assert verdict.outcome is PathOutcome.UNPARSED
    assert (verdict.persist, verdict.removed_nodes) == (False, ())
    assert stage1.needs_rebuild is False
    assert assess_unchanged(stage1).verdict.decision is AssessmentDecision.SKIP


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


def test_a_path_listed_twice_is_classified_from_the_later_read():
    """A `PostToolUse` event and the Stop path set read the same file at different moments, and
    the later read is the one closer to the settled bytes (P23)."""
    stale, fresh = _edited(_BEFORE), _edited(_NEW_BARE_CALLEE)
    stage1 = stage_one((stale, fresh))
    assert stage1.files == ("m.py",)
    assert stage1.verdicts[0].outcome is PathOutcome.TRUTH
    assert stage1.persist_paths == ("m.py",)


@pytest.mark.parametrize(
    ("order", "outcome"),
    [
        (("gone", "live"), PathOutcome.TRUTH),
        (("live", "gone"), PathOutcome.REMOVED),
    ],
    ids=["deleted then recreated", "recreated then deleted"],
)
def test_a_path_deleted_and_recreated_in_one_batch_ends_where_the_batch_left_it(
    order, outcome
):
    """A stash restore or a delete-then-create save puts both reads in one batch, and forgetting
    a live file's facts would drop every node it has until the next full scan."""
    reads = {"gone": _edited(None), "live": _edited(_NEW_BARE_CALLEE)}
    stage1 = stage_one(tuple(reads[k] for k in order))
    assert stage1.verdicts[0].outcome is outcome


def _refused_by_stage_zero(root: Path, rel: str) -> None:
    """Stage 0 turns `rel` away and stage 1 is handed what it admitted, which is nothing."""
    finder = FileDiscovery(root)
    assert finder.auditable(rel, must_exist=False) is False
    admitted = [r for r in (rel,) if finder.auditable(r, must_exist=False)]
    assert stage_one(tuple(_edited(_NEW_BARE_CALLEE, path=r) for r in admitted)) == (
        Stage1()
    )


def test_a_path_outside_the_repo_never_reaches_stage_one(tmp_path: Path):
    """Spec 8.6 stage 0 is the hook's filter, and stage 1 classifies whatever it is handed, so a
    batch built from what stage 0 admitted is the empty batch."""
    _refused_by_stage_zero(tmp_path, "../outside.py")


def test_a_file_no_language_claims_never_reaches_stage_one(tmp_path: Path):
    """The suffix set comes from the registered languages, so a note is not an edit to assess."""
    _refused_by_stage_zero(tmp_path, "notes.md")


def test_stage_zero_keeps_a_deleted_path_so_stage_one_can_remove_its_nodes(
    tmp_path: Path,
):
    """The shape predicate is what makes the `REMOVED` branch reachable in production (P13)."""
    assert FileDiscovery(tmp_path).auditable("pkg/gone.py", must_exist=False) is True


def _pair(node_id: str, name: str, **over) -> QueuePair:
    """One queue row; `reason` is a key column, so a case that needs two rows names it."""
    return QueuePair(
        **{
            "node_id": node_id,
            "name": name,
            "reason": UnresolvedReason.UNIMPORTABLE_NAME,
            **over,
        }
    )


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
_MIN2 = SchedulingConfig(min_new_unresolved=2)
_MIN20 = SchedulingConfig(min_new_unresolved=20)
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


def test_a_copied_snapshot_answers_for_the_pairs_it_was_copied_with():
    """`model_copy(update=...)` re-runs nothing, so a view cached on first read would go on
    answering for the tuple the copy replaced and stage 2 would diff one queue against another."""
    before = _snapshot((_pair(_GET, "widen"),))
    assert [p.name for p in before.by_key.values()] == ["widen"]
    after = before.model_copy(update={"pairs": (_pair(_GET, "render"),)})
    assert [p.name for p in after.by_key.values()] == ["render"]


def test_a_pair_absent_before_is_new():
    """The batch is the new bare callee, and the queue row is on the node that edit moved, so the
    two halves of the case are the same node rather than two unrelated fixtures."""
    assert _STAGE1.facts_changed_nodes == (_GET,)
    result = _assess(_STAGE1, _snapshot(), _snapshot((_pair(_GET, "widen"),)))
    assert result.new_pairs == (NodePair(node_id=_GET, name="widen"),)
    assert (result.verdict.decision, result.verdict.reason) == (
        AssessmentDecision.RUN,
        "1 new question",
    )


def test_two_rows_for_one_question_are_diffed_apart_and_reported_once():
    """`graph_unresolved` keys on `reason` too, so a bare `render()` and an `obj.render()` on one
    node are two rows. Keyed on the pair alone the second row masks the first, whose offer moved
    to what the second already held, and a real new question is dropped."""
    ambiguous = _pair(
        _GET,
        "render",
        reason=UnresolvedReason.AMBIGUOUS_NAME,
        definers=("a.py::render",),
    )
    unimportable = _pair(
        _GET,
        "render",
        reason=UnresolvedReason.UNIMPORTABLE_NAME,
        definers=("b.py::render",),
    )
    moved = ambiguous.model_copy(update={"definers": ("b.py::render",)})
    result = _assess(
        _STAGE1, _snapshot((ambiguous, unimportable)), _snapshot((moved, unimportable))
    )
    assert result.new_pairs == (NodePair(node_id=_GET, name="render"),)
    assert (result.verdict.decision, result.verdict.reason) == (
        AssessmentDecision.RUN,
        "1 new question",
    )


def test_two_rows_for_one_question_never_cross_a_bar_meant_for_two():
    """`min_new_unresolved` counts questions: one name asked twice is still one question."""
    after = _snapshot(
        (
            _pair(_GET, "render", reason=UnresolvedReason.AMBIGUOUS_NAME),
            _pair(_GET, "render", reason=UnresolvedReason.UNIMPORTABLE_NAME),
        )
    )
    result = _assess(_STAGE1, _snapshot(), after, scheduling=_MIN2)
    assert result.new_pairs == (NodePair(node_id=_GET, name="render"),)
    assert (result.verdict.decision, result.verdict.reason) == (
        AssessmentDecision.SKIP,
        "1 new question, below the 2 the gate needs",
    )


def test_a_question_one_of_whose_rows_survived_is_not_resolved():
    """The resolver settled nothing while any row still asks the same name."""
    ambiguous = _pair(_GET, "render", reason=UnresolvedReason.AMBIGUOUS_NAME)
    unimportable = _pair(_GET, "render", reason=UnresolvedReason.UNIMPORTABLE_NAME)
    result = _assess(
        _STAGE1, _snapshot((ambiguous, unimportable)), _snapshot((ambiguous,))
    )
    assert result.resolved_pairs == ()


def test_an_externally_bound_pair_that_vanished_is_not_a_resolution():
    """The same predicate on both sides: a row that never counted as new cannot be settled."""
    before = _snapshot((_pair(_GET, "search", externally_bound=True),))
    assert _assess(_STAGE1, before, _snapshot()).resolved_pairs == ()


def test_a_stored_row_reaches_the_diff_with_every_column_it_is_keyed_by():
    """`QueuePair.of` is the only adapter between the store and stage 2, so a dropped column
    here is invisible to every case that builds its rows by hand."""
    row = UnresolvedRow(
        node_id=_GET,
        fact_kind=FactKind.CALLEE,
        name="render",
        reason=UnresolvedReason.AMBIGUOUS_NAME,
        call_form=CallForm.ATTR,
        candidates=("a.py::render",),
        definers=("b.py::render",),
        externally_bound=True,
    )
    narrowed = QueuePair.of(row)
    assert narrowed.key == (_GET, "render", UnresolvedReason.AMBIGUOUS_NAME)
    assert narrowed.offer == (("a.py::render",), ("b.py::render",))
    assert (narrowed.call_form, narrowed.externally_bound) == (CallForm.ATTR, True)


def test_a_stored_refinement_reaches_the_diff_with_its_status_and_anchors():
    """`RefinementState.of` is the other adapter, and `staled_refinements` reads both fields."""
    state = RefinementState.of(
        Refinement(
            refinement_id=7,
            run_id="run-1",
            repo_identity="id",
            kind=RefinementKind.ANNOTATE_NODE,
            status=RefinementStatus.STALE,
            target=RefinementTarget(node_id=_GET),
            payload=RefinementPayload(annotation="entry point"),
            reason="judged by hand",
        )
    )
    assert (state.refinement_id, state.status) == (7, RefinementStatus.STALE)
    assert state.anchor_nodes == (_GET,)


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
    """Spec 15's case is an attribute call on a third-party object: it is a question no refiner
    in this repo could answer, whatever the budget."""
    after = _snapshot(
        (
            _pair(
                _GET,
                "search",
                call_form=CallForm.ATTR,
                externally_bound=True,
                receiver_root="requests",
            ),
        )
    )
    result = _assess(_STAGE1, _snapshot(), after)
    assert result.new_pairs == ()
    assert (result.verdict.decision, result.verdict.reason) == (
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
    assert (result.verdict.decision, result.verdict.reason) == (
        AssessmentDecision.RUN,
        "1 stale refinement",
    )


def test_a_refinement_already_stale_before_the_rebuild_is_not_reported_again():
    """`staled_refinements` is the status delta. Reported on status alone, a refinement stale
    before and after is re-reported on every edit touching its anchor, and with `run_on_stale` on
    that is a paid run per edit, for ever."""
    stale = RefinementState(
        refinement_id=1, status=RefinementStatus.STALE, anchor_nodes=(_GET,)
    )
    result = _assess(
        _STAGE1, _snapshot(refinements=(stale,)), _snapshot(refinements=(stale,))
    )
    assert result.stale_refinements == ()
    assert result.verdict.decision is AssessmentDecision.SKIP


def test_a_refinement_staled_on_an_anchor_this_batch_never_touched_is_excluded():
    """`noop_builds` and Jaccard drift land the same status, and an edit cannot re-confirm them."""
    before, after = _staled(("z.py::other",))
    assert _assess(_STAGE1, before, after).stale_refinements == ()


def test_a_docstring_edit_rebuilds_and_finds_nothing():
    stage1 = stage_one((_edited(_DOCSTRING),))
    result = _assess(stage1, _snapshot(), _snapshot())
    assert stage1.needs_rebuild is True
    assert (result.verdict.decision, result.verdict.reason) == (
        AssessmentDecision.SKIP,
        "no new questions",
    )


def test_a_batch_that_moved_nothing_never_reaches_stage_two():
    result = assess_unchanged(stage_one((_edited(_COMMENT_ONLY),)))
    assert (result.verdict.decision, result.verdict.reason) == (
        AssessmentDecision.SKIP,
        "no structural change",
    )
    assert result.files == ("m.py",)


def test_a_changed_node_in_a_recent_flow_is_recorded_and_decides_nothing():
    """`_STAGE1` moves `_GET`, so the flow node is one this batch actually touched."""
    result = _assess(_STAGE1, _snapshot(), _snapshot(), flow_nodes=frozenset({_GET}))
    assert result.affected_flow == (_GET,)
    assert result.verdict.decision is AssessmentDecision.SKIP


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


def test_a_batch_that_earned_no_run_deferred_nothing():
    """No run opened, so every pair stayed in the queue: a count here reads as "the rest were
    taken", which is the opposite of what happened."""
    after = _snapshot(_pair(f"m.py::n{i}", "widen") for i in range(15))
    result = _assess(_STAGE1, _snapshot(), after, scheduling=_MIN20)
    assert result.verdict.decision is AssessmentDecision.SKIP
    assert result.deferred_pairs == 0


def test_under_the_bar_the_deferral_counts_what_the_run_could_actually_take():
    """The narrowing decides the run's target, so the deferral has to read the same set: 15 new
    questions of which 2 are bare leaves 13 behind, not 3."""
    after = _snapshot(
        _pair(
            f"m.py::n{i}",
            "widen",
            call_form=CallForm.BARE if i < 2 else CallForm.ATTR,
        )
        for i in range(15)
    )
    result = _assess(
        _STAGE1,
        _snapshot(),
        after,
        budget=_budget(fraction=0.1),
        scheduling=SchedulingConfig(min_new_unresolved=2),
        max_nodes_per_run=1,
    )
    assert result.verdict.decision is AssessmentDecision.RUN
    assert (len(result.new_pairs), result.deferred_pairs) == (15, 1)


_NO_STALE = SchedulingConfig(run_on_stale=False)
_SKIP, _RUN = AssessmentDecision.SKIP, AssessmentDecision.RUN


def _gate(
    *, scheduling=None, new=0, stale=0, bounded=None, **over
) -> tuple[Decision, tuple[NodePair, ...]]:
    """One `decide` call, so a new keyword lands here rather than in a dozen cases."""
    pairs = tuple(NodePair(node_id=f"m.py::n{i}", name="w") for i in range(new))
    return decide(
        new_pairs=pairs,
        bounded_pairs=pairs if bounded is None else pairs[:bounded],
        stale_refinements=tuple(range(1, stale + 1)),
        scheduling=scheduling or SchedulingConfig(),
        **{"budget": _budget(), **over},
    )


def _decide(**over) -> Decision:
    """The verdict alone, which is what all but one case asserts on."""
    return _gate(**over)[0]


def test_a_gate_that_fires_on_nothing_is_refused_by_the_config():
    """`min_new_unresolved = 0` opened a model-calling run on every batch that rebuilt."""
    with pytest.raises(ValidationError):
        SchedulingConfig(min_new_unresolved=0)


@pytest.mark.parametrize(
    ("scheduling", "new", "stale", "decision", "reason"),
    [
        (_MIN2, 1, 0, _SKIP, "1 new question, below the 2 the gate needs"),
        (_MIN2, 2, 0, _RUN, "2 new questions"),
        (_NO_STALE, 0, 1, _SKIP, "1 stale refinement, run_on_stale is off"),
        (SchedulingConfig(), 1, 1, _RUN, "1 new question and 1 stale refinement"),
        (SchedulingConfig(), 0, 0, _SKIP, "no new questions"),
        # the stale arm carried it alone, so the reason must not credit the clause that failed
        (_MIN2, 1, 1, _RUN, "1 stale refinement"),
    ],
)
def test_the_decision_rule(scheduling, new, stale, decision, reason):
    verdict = _decide(scheduling=scheduling, new=new, stale=stale)
    assert (verdict.decision, verdict.reason) == (decision, reason)


@pytest.mark.parametrize(
    ("kind", "decision"),
    [
        (BatchKind.EDIT, _SKIP),
        (BatchKind.SUSPECT, _RUN),
        (BatchKind.VERIFY, _RUN),
    ],
)
def test_the_no_eval_row_shutdown_stops_an_edit_batch_alone(kind, decision):
    """Spec 8.6 disables edit-triggered runs under a low budget with no eval row; a suspect or
    verify batch keeps draining the idle capacity that is already paid for."""
    verdict = _decide(new=1, budget=_budget(fraction=0.1, evaluated=False), kind=kind)
    assert verdict.decision is decision


@pytest.mark.parametrize(
    ("kind", "decision", "reason"),
    [
        (BatchKind.EDIT, _SKIP, "low budget: 0 of 3 new questions are bare or self"),
        (BatchKind.SUSPECT, _RUN, "3 new questions"),
        (BatchKind.VERIFY, _RUN, "3 new questions"),
    ],
)
def test_the_low_budget_narrowing_counts_an_edit_batch_alone(kind, decision, reason):
    """The eval row exists, so the shutdown is out of the way and the narrowing is the only rule
    left: three questions none of which is bare clear the bar for every kind but `edit`."""
    verdict = _decide(new=3, bounded=0, budget=_budget(fraction=0.1), kind=kind)
    assert (verdict.decision, verdict.reason) == (decision, reason)


@pytest.mark.parametrize("kind", list(BatchKind))
def test_a_spent_day_stops_every_batch(kind):
    """The ceiling is hard, and it is read before the narrowing that a suspect batch skips."""
    verdict = _decide(new=5, budget=_budget(fraction=0.0, evaluated=False), kind=kind)
    assert (verdict.decision, verdict.reason) == (_SKIP, "the day's budget is spent")


@pytest.mark.parametrize(
    ("over", "targets"),
    [
        ({"budget": _budget(fraction=0.0)}, 0),
        ({"budget": _budget(fraction=0.1, evaluated=False)}, 0),
        ({"scheduling": _MIN20}, 0),
        ({}, 3),
    ],
    ids=[
        "a spent day",
        "no eval row under the bar",
        "below the threshold",
        "a run",
    ],
)
def test_only_a_gate_that_said_yes_hands_back_pairs_to_run(over, targets):
    """`deferred_pairs` is measured off that half, so a skip that handed its pairs back anyway
    would report a batch nobody opened as one a run had partly taken."""
    assert len(_gate(new=3, **over)[1]) == targets


def test_a_low_budget_that_narrowed_nothing_does_not_take_the_blame():
    """With every new question already bare, the threshold refused the batch, and the reason has
    to name the lever the user can move."""
    verdict = _decide(
        scheduling=SchedulingConfig(min_new_unresolved=10),
        new=5,
        budget=_budget(fraction=0.1),
    )
    assert (verdict.decision, verdict.reason) == (
        _SKIP,
        "5 new questions, below the 10 the gate needs",
    )


def test_a_full_budget_with_no_eval_row_still_runs():
    """The arm is `low and not evaluated`: a fresh install at full budget refines normally."""
    verdict = _decide(new=1, budget=_budget(fraction=1.0, evaluated=False))
    assert (verdict.decision, verdict.reason) == (_RUN, "1 new question")


def test_a_batch_under_min_new_unresolved_does_not_earn_a_run():
    """Spec 15's `min_new_unresolved = 2` case: one question is not two, and the reason names the
    bar rather than the count."""
    assert _decide(scheduling=_MIN2, new=1).reason == (
        "1 new question, below the 2 the gate needs"
    )
    assert _decide(scheduling=_MIN2, new=1).decision is _SKIP
    assert _decide(scheduling=_MIN2, new=2).decision is _RUN


def test_run_on_stale_off_leaves_a_staled_refinement_alone():
    """Spec 15's `run_on_stale = false` case: the arm is off, so the only clause left is empty."""
    verdict = _decide(scheduling=_NO_STALE, new=0, stale=1)
    assert (verdict.decision, verdict.reason) == (
        _SKIP,
        "1 stale refinement, run_on_stale is off",
    )


def test_under_the_bar_with_an_eval_row_only_bare_and_self_pairs_count():
    after = _snapshot(
        (
            _pair(_GET, "widen", call_form=CallForm.ATTR),
            _pair(_GET, "narrow", call_form=CallForm.ATTR),
        )
    )
    result = _assess(_STAGE1, _snapshot(), after, budget=_budget(fraction=0.1))
    assert len(result.new_pairs) == 2
    assert (result.verdict.decision, result.verdict.reason) == (
        AssessmentDecision.SKIP,
        "low budget: 0 of 2 new questions are bare or self",
    )


def test_under_the_bar_with_an_eval_row_a_bare_pair_still_runs():
    after = _snapshot((_pair(_GET, "widen", call_form=CallForm.BARE),))
    result = _assess(_STAGE1, _snapshot(), after, budget=_budget(fraction=0.1))
    assert (result.verdict.decision, result.verdict.reason) == (
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
    assert (result.verdict.decision, result.verdict.reason) == (
        AssessmentDecision.RUN,
        "1 new question",
    )


def test_under_the_bar_with_no_eval_row_edit_runs_are_off():
    after = _snapshot((_pair(_GET, "widen", call_form=CallForm.BARE),))
    result = _assess(
        _STAGE1, _snapshot(), after, budget=_budget(fraction=0.1, evaluated=False)
    )
    assert (result.verdict.decision, result.verdict.reason) == (
        AssessmentDecision.SKIP,
        "low budget and no eval row for this runner",
    )


def test_under_the_bar_with_an_eval_row_the_stale_arm_still_runs():
    """Spec 8.6 narrows the new-pairs clause only; re-confirming is the cheapest run (P16)."""
    before, after = _staled((_GET,))
    result = _assess(_STAGE1, before, after, budget=_budget(fraction=0.1))
    assert result.verdict.decision is AssessmentDecision.RUN


@pytest.mark.parametrize(
    "kind", [BatchKind.SUSPECT, BatchKind.VERIFY], ids=["suspect", "verify"]
)
def test_the_edit_batch_knobs_govern_edit_batches_alone(kind):
    """M6: `min_new_unresolved` and `run_on_stale` are documented as an edit batch's own bars."""
    scheduling = SchedulingConfig(min_new_unresolved=5, run_on_stale=False)
    assert bars_for(kind, scheduling) == Bars()
    assert bars_for(BatchKind.EDIT, scheduling) == Bars(min_new=5, on_stale=False)


def test_a_drain_of_one_pair_still_runs_when_an_edit_batch_would_need_five():
    """The suspect drain's own brake is `cooldown_minutes`, and it never reads the edit bar."""
    verdict, pairs = decide(
        new_pairs=(NodePair(node_id="a.py::f", name="load"),),
        bounded_pairs=(NodePair(node_id="a.py::f", name="load"),),
        stale_refinements=(),
        scheduling=SchedulingConfig(min_new_unresolved=5),
        budget=BudgetState(max_cost_usd_per_day=2.0),
        kind=BatchKind.SUSPECT,
    )
    assert verdict.decision is AssessmentDecision.RUN
    assert len(pairs) == 1


def test_a_skipped_batch_chooses_no_targets_even_when_a_refinement_staled():
    """The selection is guarded on the verdict, and `choose_targets` unions in the staled anchors."""
    active = RefinementState(
        refinement_id=1, status=RefinementStatus.ACTIVE, anchor_nodes=(_GET,)
    )
    stale = active.model_copy(update={"status": RefinementStatus.STALE})
    anchored = _pair(_GET, "widen")
    result = _assess(
        _STAGE1,
        _snapshot((anchored,), (active,)),
        _snapshot((anchored,), (stale,)),
        scheduling=SchedulingConfig(run_on_stale=False),
    )
    assert result.stale_refinements == (1,)  # the anchor this batch touched did stale
    assert result.verdict.decision is AssessmentDecision.SKIP
    assert (result.targets, result.deferred) == ((), ())
