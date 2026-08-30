"""Spec 8.3's work items, over a real store and a `FakeRunner`: $0 and no SDK."""

from collections.abc import Sequence

from auditor.graph.refine.models import (
    NodePair,
    ProducerKind,
    Refinement,
    RefinementKind,
    RefinementStatus,
    RefinementTarget,
    Run,
    RunnerKind,
    RunStatus,
    RunUsage,
    TriggerKind,
)
from auditor.graph.refine.runner import FakeRun, FakeRunner
from auditor.graph.refine.service import RefinementService
from auditor.observer.events import Event
from auditor.observer.loop import RepoLoop
from auditor.observer.scheduling import EventFeed, LoopState, pause_of

_IMPL_WITH_A_NEW_CALL = (
    "from base import Base\nclass Impl(Base):\n    def run(self):\n"
    "        return load_user() or _local()\n\n"
    "    def again(self):\n        return load_user()\n\ndef _local():\n    return 1\n"
)
_IMPL_COMMENT_ONLY = (
    "from base import Base\nclass Impl(Base):\n    def run(self):\n"
    "        # a comment the graph cannot see\n"
    "        return load_user() or _local()\n\ndef _local():\n    return 1\n"
)
_NOTE = {
    "kind": "annotate_node",
    "reason": "a note",
    "target": {"node_id": "impl.py::Impl.run"},
    "payload": {"annotation": "seen"},
}


class Scripted(EventFeed):
    """One batch, then quiet: the loop's own debounce is what turns this into a window."""

    def __init__(self, *paths: str) -> None:
        self.groups = (
            [tuple(Event(repo="/r", paths=(p,)) for p in paths)] if paths else []
        )

    async def take(self, timeout: float) -> tuple[Event, ...]:
        return self.groups.pop(0) if self.groups else ()


def _capped(user, nodes: int):
    """The same settings with a smaller `max_nodes_per_run`, so a cap is reachable in a test."""
    return user.model_copy(
        update={
            "observer": user.observer.model_copy(
                update={
                    "limits": user.observer.limits.model_copy(
                        update={"max_nodes_per_run": nodes}
                    )
                }
            )
        }
    )


def _loop(
    service: RefinementService,
    *,
    feed: EventFeed | None = None,
    script: Sequence[dict] = (),
    now: float = 1_000.0,
    status=lambda _root: (),
) -> RepoLoop:
    changes: list[int] = []
    loop = RepoLoop(
        root=service.root,
        index=service.index,
        settings=service.settings,
        user=service.user,
        feed=feed or Scripted(),
        service=service,
        runner_for=lambda svc: FakeRunner(svc, pretend=FakeRun(script=tuple(script))),
        now=lambda: now,
        on_change=lambda: changes.append(1),
        status=status,
    )
    loop.changes = changes
    return loop


async def _rows(service: RefinementService):
    return await service.index.runs.runs(limit=50)


async def test_attach_scans_and_rebuilds_and_ends_observing(
    refine_service: RefinementService,
):
    """Spec 8.3 item 1: the attach rebuilds, records its row and ends in the observing state."""
    loop = _loop(refine_service)
    assert await loop.attach() is LoopState.OBSERVING
    assert loop.changes


async def test_attach_records_the_one_row_that_says_the_observer_arrived(
    refine_service: RefinementService,
):
    """C9: `TriggerKind.SESSION_START` is declared in spec 5.3 and written by nothing."""
    loop = _loop(refine_service)
    await loop.attach()
    rows = await _rows(refine_service)
    assert [r.trigger_kind for r in rows] == [TriggerKind.SESSION_START]
    assert rows[0].status is RunStatus.SKIPPED
    assert rows[0].runner is RunnerKind.NONE
    assert rows[0].usage.cost_usd == 0.0
    assert rows[0].trigger_detail.assessment.verdict.reason == "session-start build"


async def test_an_edit_with_no_structural_change_is_a_skipped_row_and_no_rebuild(
    refine_service: RefinementService,
):
    """Spec 8.6's first table row, and invariant 2: the batch is still a `graph_runs` row."""
    (refine_service.root / "impl.py").write_text(_IMPL_COMMENT_ONLY)
    loop = _loop(refine_service, feed=Scripted("impl.py"))
    await loop.tick(poll=0.0)
    rows = await _rows(refine_service)
    assert [r.status for r in rows] == [RunStatus.SKIPPED]
    assert rows[0].trigger_detail.assessment.verdict.reason == "no structural change"
    assert rows[0].producer is ProducerKind.OBSERVER


async def test_a_batch_of_paths_stage_zero_drops_writes_no_row_at_all(
    refine_service: RefinementService,
):
    """P19 of S8a: stage 0 rejects before the batch exists, so there is nothing to record."""
    loop = _loop(refine_service, feed=Scripted("notes.md", "node_modules/x.py"))
    await loop.tick(poll=0.0)
    assert await _rows(refine_service) == []


async def test_a_new_question_opens_a_run_carrying_exactly_its_targets(
    refine_service: RefinementService,
):
    """Spec 8.3 item 2 end to end: persist, rebuild, gate, then a target-driven run."""
    (refine_service.root / "impl.py").write_text(_IMPL_WITH_A_NEW_CALL)
    loop = _loop(refine_service, feed=Scripted("impl.py"))
    await loop.tick(poll=0.0)
    rows = await _rows(refine_service)
    assert len(rows) == 1
    row = rows[0]
    assert row.trigger_kind is TriggerKind.EDIT
    assert row.producer is ProducerKind.OBSERVER
    assert (
        NodePair(node_id="impl.py::Impl.again", name="load_user")
        in row.trigger_detail.targets
    )
    assert row.trigger_detail.assessment.decided_to_run is True
    assert loop.state is LoopState.OBSERVING


async def test_a_run_row_records_whether_the_tree_was_dirty_when_it_opened(
    refine_service: RefinementService,
):
    """Spec 8.5's third pre-run read reaches the row, or the git status is paid for and thrown."""
    (refine_service.root / "impl.py").write_text(_IMPL_WITH_A_NEW_CALL)
    loop = _loop(
        refine_service, feed=Scripted("impl.py"), status=lambda _root: ("impl.py",)
    )
    await loop.tick(poll=0.0)
    assert [r.dirty for r in await _rows(refine_service)] == [True]
    clean = _loop(refine_service)
    await clean.attach()
    assert (await _rows(refine_service))[0].dirty is False


async def test_the_loop_reports_running_while_a_run_is_open(
    refine_service: RefinementService,
):
    """The state badge S8b's `/api/status` shows, and the counter its ETag reads."""
    seen: list[LoopState] = []
    (refine_service.root / "impl.py").write_text(_IMPL_WITH_A_NEW_CALL)
    loop = _loop(refine_service, feed=Scripted("impl.py"))

    async def watching(run_id: str, proposal):
        seen.append(loop.state)
        return await refine_service.propose(run_id, proposal)

    loop.runner_for = lambda svc: FakeRunner(
        svc, proposer=watching, pretend=FakeRun(script=(_NOTE,))
    )
    await loop.tick(poll=0.0)
    assert seen == [LoopState.RUNNING]
    assert loop.state is LoopState.OBSERVING
    assert loop.changes


async def test_the_suspect_drain_opens_a_suspect_run_over_the_queue(
    refine_service: RefinementService,
):
    """Spec 8.3 item 3, in the store's own priority order (`GraphDB.unresolved` already sorts)."""
    loop = _loop(refine_service)
    assert await loop.suspects() is True
    rows = await _rows(refine_service)
    assert [r.trigger_kind for r in rows] == [TriggerKind.SUSPECT]
    assert rows[0].trigger_detail.targets != ()


async def test_a_pair_a_recent_run_already_looked_at_is_on_cooldown(
    refine_service: RefinementService,
):
    """Q8: derived from `graph_runs`, because `graph_unresolved` is replaced by every build."""
    loop = _loop(refine_service)
    assert await loop.suspects() is True
    first = (await _rows(refine_service))[0].trigger_detail.targets
    cooled = await loop.cooldown()
    assert set(first) <= cooled
    assert await loop.suspects() is False


async def test_cooldown_is_off_when_the_knob_is_zero(
    refine_service: RefinementService,
):
    """A repo whose whole queue fits in one run wants every pass to see all of it."""
    loop = _loop(refine_service)
    loop.service.user = refine_service.user.model_copy(
        update={
            "observer": refine_service.user.observer.model_copy(
                update={
                    "scheduling": refine_service.user.observer.scheduling.model_copy(
                        update={"cooldown_minutes": 0}
                    )
                }
            )
        }
    )
    assert await loop.cooldown() == frozenset()


async def test_a_paused_loop_opens_nothing_and_reports_why(
    refine_service: RefinementService,
):
    """Spec 8.4: `paused:auth` is a state, not a refusal the gate has to re-derive every tick."""
    (refine_service.root / "impl.py").write_text(_IMPL_WITH_A_NEW_CALL)
    loop = _loop(refine_service, feed=Scripted("impl.py"))
    loop.pauses.apply(pause_of("paused:auth", now=1_000.0))
    assert await loop.tick(poll=0.0) is LoopState.PAUSED_AUTH
    assert await _rows(refine_service) == []


async def test_a_batch_drained_while_paused_is_assessed_when_the_pause_lifts(
    refine_service: RefinementService,
):
    """Emptying the spool must not mean forgetting the edits it held (spec 8.3)."""
    (refine_service.root / "impl.py").write_text(_IMPL_WITH_A_NEW_CALL)
    loop = _loop(refine_service, feed=Scripted("impl.py"))
    loop.pauses.apply(pause_of("paused:auth", now=1_000.0))
    assert await loop.tick(poll=0.0) is LoopState.PAUSED_AUTH
    assert await _rows(refine_service) == []
    loop.pauses.cleared()
    await loop.tick(poll=0.0)
    rows = await _rows(refine_service)
    assert [r.trigger_kind for r in rows] == [TriggerKind.EDIT]


async def test_a_spent_day_pauses_the_loop_on_budget(
    refine_service: RefinementService,
):
    """C43: `decide` returning a skip is not the same thing as the loop stopping."""
    await refine_service.index.runs.add_run(
        Run(
            repo_identity=refine_service.identity,
            started_at=999.0,
            runner=RunnerKind.CLAUDE,
            producer=ProducerKind.OBSERVER,
            usage=RunUsage(cost_usd=99.0),
        )
    )
    loop = _loop(refine_service)
    assert (await loop.budget()).exhausted is True
    assert await loop.tick(poll=0.0) is LoopState.PAUSED_BUDGET


async def test_item_two_s_deferred_pairs_are_what_item_three_drains_first(
    refine_service: RefinementService,
):
    """C29: they are a set the loop holds, because the count on the row cannot be drained."""
    (refine_service.root / "impl.py").write_text(_IMPL_WITH_A_NEW_CALL)
    loop = _loop(refine_service, feed=Scripted("impl.py"))
    loop.service.user = _capped(refine_service.user, 1)
    await loop.tick(poll=0.0)
    assert loop.deferred != ()
    row = (await _rows(refine_service))[0]
    assert row.trigger_detail.assessment.deferred_pairs == len(loop.deferred)


async def test_the_drain_skips_a_pair_a_refinement_already_answers(
    refined_facts_store,
    refine_service: RefinementService,
):
    """C27 and C28: `unresolvable` is a refinement kind and `redundant` a status, not queue rows."""
    loop = _loop(refine_service)
    assert await loop.suppressed() == frozenset()
    await refine_service.index.refinements.add_refinement(
        Refinement(
            run_id=(
                await refine_service.index.runs.add_run(
                    Run(repo_identity=refine_service.identity, started_at=1.0)
                )
            ),
            repo_identity=refine_service.identity,
            kind=RefinementKind.UNRESOLVABLE,
            reason="dispatched by a registry with no literal call site",
            target=RefinementTarget(
                node_id="impl.py::Impl.run", name="load_user", reason_code="dynamic"
            ),
            status=RefinementStatus.ACTIVE,
        )
    )
    assert NodePair(node_id="impl.py::Impl.run", name="load_user") in (
        await loop.suppressed()
    )


_PENDING_EDGE = {
    "src": "impl.py::Impl.run",
    "dst": "svc.py::load_user",
    "edge_kind": "calls",
    "name": "load_user",
}


async def _pending(service: RefinementService, dst: str = "svc.py::load_user") -> int:
    """One `pending` tier C add_edge, which is what a verify run exists to settle (spec 10.3)."""
    run_id = await service.index.runs.add_run(
        Run(repo_identity=service.identity, started_at=1.0)
    )
    return await service.index.refinements.add_refinement(
        Refinement(
            run_id=run_id,
            repo_identity=service.identity,
            kind=RefinementKind.ADD_EDGE,
            reason="the call resolves there",
            target=RefinementTarget(**{**_PENDING_EDGE, "dst": dst}),
            status=RefinementStatus.PENDING,
        )
    )


async def test_a_verify_run_that_agrees_promotes_the_pending_refinement(
    refine_service: RefinementService,
):
    """Spec 10.3: agreement activates, and the second opinion stores nothing of its own."""
    rid = await _pending(refine_service)
    loop = _loop(
        refine_service,
        script=({"kind": "add_edge", "reason": "same call", "target": _PENDING_EDGE},),
    )
    assert await loop.verify() is True
    stored = await refine_service.index.refinements.refinement(rid)
    assert stored.status is RefinementStatus.ACTIVE
    assert await refine_service.index.refinements.count() == 1
    assert TriggerKind.VERIFY in {r.trigger_kind for r in await _rows(refine_service)}


async def test_a_verify_run_that_names_another_destination_rejects_it(
    refine_service: RefinementService,
):
    """ "Disagreement stamps `rejected`" (spec 10.3), which is a status move, not a new row."""
    rid = await _pending(refine_service)
    loop = _loop(
        refine_service,
        script=(
            {
                "kind": "add_edge",
                "reason": "somewhere else",
                "target": {**_PENDING_EDGE, "dst": "base.py::Base.run"},
            },
        ),
    )
    assert await loop.verify() is True
    stored = await refine_service.index.refinements.refinement(rid)
    assert stored.status is RefinementStatus.REJECTED


async def test_a_verify_run_that_says_nothing_leaves_the_row_pending(
    refine_service: RefinementService,
):
    """Silence is not disagreement: a run that proposed nothing has judged nothing."""
    rid = await _pending(refine_service)
    assert await _loop(refine_service).verify() is True
    stored = await refine_service.index.refinements.refinement(rid)
    assert stored.status is RefinementStatus.PENDING


async def test_a_verify_run_leaves_a_row_a_human_moved_where_the_human_put_it(
    refine_service: RefinementService,
):
    """The one guarded door out of `pending`, so agreement cannot re-activate a reverted row."""
    rid = await _pending(refine_service)
    await refine_service.ledger.revert(rid)
    loop = _loop(
        refine_service,
        script=({"kind": "add_edge", "reason": "same call", "target": _PENDING_EDGE},),
    )
    assert await loop.verify() is False
    await loop._judge_pending(
        [await refine_service.index.refinements.refinement(rid)],
        [("impl.py::Impl.run", "load_user", "svc.py::load_user")],
    )
    stored = await refine_service.index.refinements.refinement(rid)
    assert stored.status is RefinementStatus.REVERTED


async def test_verify_finds_nothing_to_do_on_a_repo_with_no_pending_rows(
    refine_service: RefinementService,
):
    """Recon 4.4: a fresh home holds zero refinements, so item 4 has to fall through."""
    assert await _loop(refine_service).verify() is False


async def test_the_tuning_slot_is_reached_and_does_nothing(
    refine_service: RefinementService,
):
    """C39: spec 8.3 item 5 is S11's; the ladder knows the slot exists and finds no proposal."""
    assert await _loop(refine_service).tuning() == 0
