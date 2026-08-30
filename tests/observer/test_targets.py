"""Spec 8.3 item 2's target list: proximity, call form, rank, and a cap that counts nodes."""

import pytest

from auditor.graph.model import CallForm, UnresolvedReason
from auditor.graph.refine.models import NodePair, RefinementStatus
from auditor.observer.assess import (
    GraphSnapshot,
    QueuePair,
    RefinementState,
    choose_targets,
)


def _row(node_id: str, name: str = "widen", **over) -> QueuePair:
    return QueuePair(
        **{
            "node_id": node_id,
            "name": name,
            "reason": UnresolvedReason.UNIMPORTABLE_NAME,
            "call_form": CallForm.ATTR,
            "priority": 3,
            **over,
        }
    )


def _choose(rows, *, files=("pkg/edited.py",), max_nodes=12, stale=(), refinements=()):
    after = GraphSnapshot(pairs=tuple(rows), refinements=tuple(refinements))
    return choose_targets(
        pairs=tuple(r.pair for r in rows),
        after=after,
        stale_refinements=tuple(stale),
        files=tuple(files),
        max_nodes=max_nodes,
    )


def test_a_queue_pair_names_the_file_it_sits_in_without_storing_it():
    """Proximity needs the file; deriving it means it can never disagree with the node id."""
    assert _row("pkg/mod.py::Cls.method").file == "pkg/mod.py"
    assert _row("top.py").file == "top.py"


def test_the_edited_file_comes_first_then_its_directory_then_everywhere_else():
    rows = [_row("other/far.py::a"), _row("pkg/near.py::b"), _row("pkg/edited.py::c")]
    assert [p.node_id for p in _choose(rows).chosen] == [
        "pkg/edited.py::c",
        "pkg/near.py::b",
        "other/far.py::a",
    ]


@pytest.mark.parametrize("form", [CallForm.BARE, CallForm.SELF])
def test_a_bare_or_self_call_outranks_an_attribute_call_at_the_same_distance(form):
    """The two forms tier B can auto-activate, so they are the ones worth a run's turns."""
    rows = [_row("pkg/edited.py::attr"), _row("pkg/edited.py::bare", call_form=form)]
    assert _choose(rows).chosen[0].node_id == "pkg/edited.py::bare"


def test_the_newest_staled_refinement_ranks_ahead_of_an_older_one():
    """Spec 8.3's `created_at` desc: the correction the edit just broke is the cheapest to re-earn."""
    rows = [_row("pkg/a.py::old"), _row("pkg/a.py::new")]
    refinements = (
        RefinementState(
            refinement_id=1,
            status=RefinementStatus.STALE,
            anchor_nodes=("pkg/a.py::old",),
            created_at=10.0,
        ),
        RefinementState(
            refinement_id=2,
            status=RefinementStatus.STALE,
            anchor_nodes=("pkg/a.py::new",),
            created_at=99.0,
        ),
    )
    chosen = _choose(rows, files=(), stale=(1, 2), refinements=refinements).chosen
    assert [p.node_id for p in chosen] == ["pkg/a.py::new", "pkg/a.py::old"]


def test_a_stale_refinements_node_joins_the_targets_even_with_no_new_pair():
    """C17: targets are the new pairs *plus* the nodes of the stale refinements."""
    row = _row("pkg/a.py::anchored")
    after = GraphSnapshot(
        pairs=(row,),
        refinements=(
            RefinementState(
                refinement_id=1,
                status=RefinementStatus.STALE,
                anchor_nodes=("pkg/a.py::anchored",),
                created_at=5.0,
            ),
        ),
    )
    selection = choose_targets(
        pairs=(),
        after=after,
        stale_refinements=(1,),
        files=("pkg/a.py",),
        max_nodes=12,
    )
    assert selection.chosen == (NodePair(node_id="pkg/a.py::anchored", name="widen"),)


def test_the_cap_counts_distinct_nodes_and_a_second_question_rides_along_free():
    """Spec 8.3 caps nodes; `deferred_pairs` counted pairs until this slice (the C19 drift)."""
    rows = [
        _row("pkg/a.py::one", name="alpha"),
        _row("pkg/a.py::one", name="beta"),
        _row("pkg/a.py::two", name="gamma"),
    ]
    selection = _choose(rows, files=("pkg/a.py",), max_nodes=1)
    assert {p.name for p in selection.chosen} == {"alpha", "beta"}
    assert [p.name for p in selection.deferred] == ["gamma"]


def test_one_question_asked_for_two_reasons_is_ordered_once_by_its_best_row():
    """`graph_unresolved` keys on the reason too, and the drain order is the row's, not the pair's."""
    rows = [
        _row("pkg/a.py::n", reason=UnresolvedReason.AMBIGUOUS_NAME, priority=1),
        _row("pkg/a.py::n", reason=UnresolvedReason.UNIMPORTABLE_NAME, priority=3),
        _row("pkg/b.py::m", priority=2),
    ]
    selection = _choose(rows, files=())
    assert [p.node_id for p in selection.chosen] == ["pkg/a.py::n", "pkg/b.py::m"]


def test_the_order_is_total_so_two_identical_rows_never_swap():
    """Sorted on the node id then the name, so two rows alike in every other key still order.

    Given in the reverse of the answer, because the sort is stable and its input is the caller's
    order: a tie-break that stopped working would hand the input straight back.
    """
    rows = [_row("pkg/z.py::b", name="z"), _row("pkg/a.py::b", name="a")]
    assert [p.node_id for p in _choose(rows, files=()).chosen] == [
        "pkg/a.py::b",
        "pkg/z.py::b",
    ]
    same_node = [_row("pkg/a.py::b", name="z"), _row("pkg/a.py::b", name="a")]
    assert [p.name for p in _choose(same_node, files=()).chosen] == ["a", "z"]
