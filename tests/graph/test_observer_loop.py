"""Spec 8.3's work items, over a real store and a `FakeRunner`: $0 and no SDK."""

import asyncio
import json
import time
from collections.abc import Sequence
from typing import ClassVar

import pytest

from auditor.graph.model import FactKind, UnresolvedReason, UnresolvedRow
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
from auditor.observer.assess import EditedFile
from auditor.observer.budget import BudgetState
from auditor.observer.daemon import Daemon, IdleTimer
from auditor.observer.events import Event, EventQueue
from auditor.observer.loop import RepoLoop
from auditor.observer.scheduling import EventFeed, LoopState, pause_of
from auditor.observer.sessions import SessionBook
from auditor.paths import repo_dir_key
from auditor.status import status_path

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
        self.waits: list[float] = []

    async def take(self, timeout: float) -> tuple[Event, ...]:
        self.waits.append(timeout)
        return self.groups.pop(0) if self.groups else ()


class Priced(FakeRunner):
    """A fake that answers with the Claude runner's kind, so the paths gated on a real runner
    and on a priced day are both reachable at $0."""

    kind: ClassVar[RunnerKind] = RunnerKind.CLAUDE


def _limited(user, **knobs):
    """The same settings with different `observer.limits` knobs, so a cap is reachable here."""
    return user.model_copy(
        update={
            "observer": user.observer.model_copy(
                update={"limits": user.observer.limits.model_copy(update=knobs)}
            )
        }
    )


def _capped(user, nodes: int):
    """The same settings with a smaller `max_nodes_per_run`, which is the cap most cases want."""
    return _limited(user, max_nodes_per_run=nodes)


def _loop(
    service: RefinementService,
    *,
    feed: EventFeed | None = None,
    script: Sequence[dict] = (),
    now: float = 1_000.0,
    status=lambda _root: (),
    real: bool = False,
) -> RepoLoop:
    changes: list[int] = []
    runner = Priced if real else FakeRunner
    loop = RepoLoop(
        root=service.root,
        index=service.index,
        settings=service.settings,
        service=service,
        feed=feed or Scripted(),
        runner_for=lambda svc, proposer: runner(
            svc, proposer=proposer, pretend=FakeRun(script=tuple(script))
        ),
        now=lambda: now,
        on_change=lambda: changes.append(1),
        status=status,
    )
    loop.changes = changes
    return loop


def _scheduled(user, **knobs):
    """The same settings with different scheduling knobs, so a window is reachable in a test."""
    return user.model_copy(
        update={
            "observer": user.observer.model_copy(
                update={"scheduling": user.observer.scheduling.model_copy(update=knobs)}
            )
        }
    )


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

    loop.runner_for = lambda svc, proposer: FakeRunner(
        svc, proposer=proposer or watching, pretend=FakeRun(script=(_NOTE,))
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
    assert await loop.suspects() is True
    assert await loop.cooldown() != frozenset()  # the default knob is doing work
    loop.service.user = _scheduled(refine_service.user, cooldown_minutes=0)
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
    loop = _loop(refine_service, real=True)
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
        real=True,
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
        real=True,
    )
    assert await loop.verify() is True
    stored = await refine_service.index.refinements.refinement(rid)
    assert stored.status is RefinementStatus.REJECTED


async def test_a_verify_run_that_says_nothing_leaves_the_row_pending(
    refine_service: RefinementService,
):
    """Silence is not disagreement: a run that proposed nothing has judged nothing."""
    rid = await _pending(refine_service)
    assert await _loop(refine_service, real=True).verify() is True
    stored = await refine_service.index.refinements.refinement(rid)
    assert stored.status is RefinementStatus.PENDING


@pytest.mark.parametrize(
    "answer",
    [_PENDING_EDGE, {**_PENDING_EDGE, "dst": "base.py::Base.run"}],
    ids=["agrees", "disagrees"],
)
async def test_a_verify_run_leaves_a_row_a_human_moved_where_the_human_put_it(
    refine_service: RefinementService, answer: dict
):
    """Both doors out of `pending` are guarded, so a revert mid-run outranks either verdict."""
    rid = await _pending(refine_service)
    loop = _loop(
        refine_service,
        script=({"kind": "add_edge", "reason": "second opinion", "target": answer},),
        real=True,
    )
    inner = loop.runner_for

    def reverting(svc, proposer):
        """The human moves the row between the run's read of it and the judgement's write."""
        if proposer is None:
            return inner(svc, None)

        async def judged(run_id, proposal):
            await refine_service.ledger.revert(rid)
            return await proposer(run_id, proposal)

        return inner(svc, judged)

    loop.runner_for = reverting
    assert await loop.verify() is True
    stored = await refine_service.index.refinements.refinement(rid)
    assert stored.status is RefinementStatus.REVERTED


async def test_verify_finds_nothing_to_do_on_a_repo_with_no_pending_rows(
    refine_service: RefinementService,
):
    """Recon 4.4: a fresh home holds zero refinements, so item 4 has to fall through."""
    assert await _loop(refine_service, real=True).verify() is False


async def test_five_ticks_over_one_unsettled_row_open_at_most_one_verify_run(
    refine_service: RefinementService,
):
    """H1: silence leaves a row pending, so without a window every tick would re-ask about it."""
    await _pending(refine_service)
    loop = _loop(refine_service, real=True)
    for _ in range(5):
        await loop.verify()
    rows = await _rows(refine_service)
    assert [r.trigger_kind for r in rows].count(TriggerKind.VERIFY) == 1


async def test_the_verify_window_reopens_once_it_has_passed(
    refine_service: RefinementService,
):
    """The window is a cooldown, not a switch: an unsettled row is re-asked on the next one."""
    await _pending(refine_service)
    loop = _loop(refine_service, real=True)
    assert await loop.verify() is True
    loop.service.user = _scheduled(refine_service.user, verify_cooldown_minutes=0)
    assert await loop.verify() is True


async def test_a_fake_runner_opens_no_verify_run_at_all(
    refine_service: RefinementService,
):
    """A fake proposes nothing, so its second opinion could only ever leave the row pending."""
    await _pending(refine_service)
    loop = _loop(refine_service)
    assert await loop.verify() is False
    assert TriggerKind.VERIFY not in {
        r.trigger_kind for r in await _rows(refine_service)
    }


async def test_a_pending_row_that_names_no_pair_opens_no_run(
    refine_service: RefinementService,
):
    """A job with no targets is briefed on the whole scope, which is not a second opinion."""
    run_id = await refine_service.index.runs.add_run(
        Run(repo_identity=refine_service.identity, started_at=1.0)
    )
    await refine_service.index.refinements.add_refinement(
        Refinement(
            run_id=run_id,
            repo_identity=refine_service.identity,
            kind=RefinementKind.MOVE_NODE,
            reason="the members moved",
            target=RefinementTarget(node_id="impl.py::Impl", members=("run",)),
            status=RefinementStatus.PENDING,
        )
    )
    loop = _loop(refine_service, real=True)
    assert await loop.verify() is False
    assert TriggerKind.VERIFY not in {
        r.trigger_kind for r in await _rows(refine_service)
    }


async def test_the_drain_cap_lets_a_second_question_about_a_taken_node_ride_along(
    refine_service: RefinementService,
):
    """M3: the drain and the edit batch's chooser apply one cap, and it counts distinct nodes."""
    # the same node at two priorities, with another node's row between them (spec 8.3's order)
    await refine_service.index.graph.replace_unresolved(
        [
            UnresolvedRow(
                node_id=node_id,
                fact_kind=FactKind.CALLEE,
                name=name,
                reason=UnresolvedReason.UNIMPORTABLE_NAME,
                priority=priority,
            )
            for node_id, name, priority in (
                ("base.py::Base.run", "first", 1),
                ("impl.py::Impl.run", "elsewhere", 2),
                ("base.py::Base.run", "second", 3),
            )
        ]
    )
    loop = _loop(refine_service)
    loop.service.user = _capped(refine_service.user, 1)
    assert await loop.suspects() is True
    targets = (await _rows(refine_service))[0].trigger_detail.targets
    assert {pair.node_id for pair in targets} == {"base.py::Base.run"}
    assert {pair.name for pair in targets} == {"first", "second"}


async def test_the_poll_the_ladder_was_given_reaches_the_feed(
    refine_service: RefinementService,
):
    """L14: a regression in `tick`'s poll plumbing is invisible to a feed that drops it."""
    feed = Scripted()
    await _loop(refine_service, feed=feed).tick(poll=0.0)
    assert feed.waits == [0.0]


async def test_a_state_change_tells_the_daemon_so_the_status_etag_moves(
    refine_service: RefinementService,
):
    """The page's badge rides an ETag counter, so a transition nobody is told about is stale."""
    revision = 0

    def bump() -> None:
        nonlocal revision
        revision += 1

    loop = _loop(refine_service)
    loop.on_change = bump
    assert await loop.attach() is LoopState.OBSERVING
    assert revision == 2  # detached to building, then building to observing


def _daemon_for(loop: RepoLoop, tmp_path) -> Daemon:
    """A daemon over injected parts, holding this one loop: no port, no lock, no process."""
    daemon = Daemon(
        queue=EventQueue(lambda key: tmp_path / "repos" / key / "spool.jsonl"),
        sessions=SessionBook(expiry_minutes=45),
        idle=IdleTimer(minutes=30.0, now=0.0),
    )
    daemon.loops[repo_dir_key(loop.root)] = loop
    return daemon


async def _queue(service: RefinementService, *rows: tuple[str, int]) -> None:
    """Replace the queue with one row per (node, priority), so a drain order is arrangeable."""
    await service.index.graph.replace_unresolved(
        [
            UnresolvedRow(
                node_id=node_id,
                fact_kind=FactKind.CALLEE,
                name="load_user",
                reason=UnresolvedReason.UNIMPORTABLE_NAME,
                priority=priority,
            )
            for node_id, priority in rows
        ]
    )


async def test_attach_closes_the_run_a_dead_daemon_left_open(
    refine_service: RefinementService,
):
    """M12: nothing else in the daemon sweeps, so a killed run stayed `running` until a human did."""
    run_id = await refine_service.index.runs.add_run(
        Run(
            repo_identity=refine_service.identity,
            started_at=1.0,
            status=RunStatus.RUNNING,
        )
    )
    await _loop(refine_service).attach()
    stored = await refine_service.index.runs.run(run_id)
    assert stored.status is RunStatus.SKIPPED


async def test_the_drain_takes_item_two_s_deferred_pairs_before_the_queue_s_own_order(
    refine_service: RefinementService,
):
    """C29: the deferred set is intent, so the pass that drains has to honour it first."""
    await _queue(refine_service, ("a.py::first", 1), ("z.py::last", 9))
    loop = _loop(refine_service)
    loop.service.user = _capped(refine_service.user, 1)
    loop.deferred = (NodePair(node_id="z.py::last", name="load_user"),)
    assert await loop.suspects() is True
    targets = (await _rows(refine_service))[0].trigger_detail.targets
    assert [pair.node_id for pair in targets] == ["z.py::last"]


async def test_the_suspect_drain_takes_the_queue_in_the_store_s_own_priority_order(
    refine_service: RefinementService,
):
    """Spec 8.3 item 3: the queue's `ORDER BY priority` is the drain order, not an accident."""
    await _queue(refine_service, ("z.py::last", 1), ("a.py::first", 9))
    loop = _loop(refine_service)
    loop.service.user = _capped(refine_service.user, 1)
    assert await loop.suspects() is True
    targets = (await _rows(refine_service))[0].trigger_detail.targets
    assert [pair.node_id for pair in targets] == ["z.py::last"]


async def test_a_repo_s_own_observer_settings_are_what_the_loop_reads(
    refine_service: RefinementService,
):
    """M1: the daemon serves many repos, and its home layer is nobody's per-repo answer."""
    loop = _loop(refine_service)
    loop.service.user = _capped(refine_service.user, 3)
    assert loop.user.observer.limits.max_nodes_per_run == 3


async def test_a_pass_that_raises_pauses_the_repo_and_the_next_one_recovers(
    refine_service: RefinementService, tmp_path
):
    """H2: `_drive` caught only cancellation, so one bad pass stopped a repo for the daemon's life."""
    refine_service.user = _scheduled(refine_service.user, error_backoff_seconds=0.01)
    loop = _loop(refine_service)
    daemon = _daemon_for(loop, tmp_path)
    seen: list[LoopState] = []
    settled = loop.attach

    async def flaky() -> LoopState:
        seen.append(loop.state)
        if len(seen) == 1:
            raise RuntimeError("the session-start build blew up")
        return await settled()

    async def once(*, poll: float) -> LoopState:
        daemon.stopping = True
        return loop.state

    loop.attach, loop.tick = flaky, once
    await daemon._drive(loop)
    assert seen == [LoopState.DETACHED, LoopState.PAUSED_ERROR]
    assert (
        loop.pauses.errors.failures == 0
    )  # the pass that finished cleared the backoff
    daemon.reconcile()
    assert daemon.loops == {}  # and no key is left claiming a loop that stopped


async def test_a_tick_that_raises_is_retried_without_a_second_session_start_build(
    refine_service: RefinementService, tmp_path
):
    """H2: a bad tick is not a repo to rebuild, so the recovery must not re-run the attach."""
    refine_service.user = _scheduled(refine_service.user, error_backoff_seconds=0.01)
    loop = _loop(refine_service)
    daemon = _daemon_for(loop, tmp_path)
    ticks: list[LoopState] = []

    async def flaky(*, poll: float) -> LoopState:
        ticks.append(loop.state)
        if len(ticks) == 1:
            raise RuntimeError("the queue read blew up")
        daemon.stopping = True
        return loop.state

    loop.tick = flaky
    await daemon._drive(loop)
    assert ticks == [LoopState.OBSERVING, LoopState.PAUSED_ERROR]
    rows = await _rows(refine_service)
    assert [r.trigger_kind for r in rows] == [TriggerKind.SESSION_START]


async def test_a_driver_whose_key_was_retired_lets_go_of_the_loop(
    refine_service: RefinementService, tmp_path
):
    """M5: `reconcile` unclaims the key, and the driver is what has to notice and detach."""
    loop = _loop(refine_service)
    daemon = _daemon_for(loop, tmp_path)
    daemon.retire(repo_dir_key(loop.root))
    await daemon._drive(loop)
    assert loop.state is LoopState.DETACHED


async def test_the_meter_a_loop_publishes_is_its_own_and_names_the_auth_deadline(
    refine_service: RefinementService, tmp_path
):
    """H-9 and M8: the ceilings are per repo, and an auth hold has a deadline of its own."""
    loop = _loop(refine_service)
    daemon = _daemon_for(loop, tmp_path)
    loop.pauses.apply(pause_of("paused:auth", now=1_000.0))
    loop._moved(LoopState.PAUSED_AUTH)
    await daemon._publish(loop)
    drawn = daemon.repo_meters(repo_dir_key(loop.root))
    assert drawn.budget.max_cost_usd_per_day == 2.0
    assert (drawn.limits.paused, drawn.limits.resumes_at) == (
        True,
        loop.pauses.auth_until,
    )
    assert daemon.repo_meters("some-other-repo").budget is None


async def test_the_budget_the_meter_draws_is_the_one_the_tick_acted_on(
    refine_service: RefinementService, tmp_path
):
    """M8: two reads a tick is two answers, and the page drew the one the loop never used."""
    loop = _loop(refine_service)
    daemon = _daemon_for(loop, tmp_path)
    await loop.tick(poll=0.0)
    loop.budget = _raises
    await daemon._publish(loop)
    assert daemon.repo_meters(repo_dir_key(loop.root)).budget is not None


async def _raises() -> BudgetState:
    raise AssertionError("the tick already read the budget")


async def test_the_tuning_slot_is_reached_and_does_nothing(
    refine_service: RefinementService,
):
    """C39: spec 8.3 item 5 is S11's; the ladder knows the slot exists and finds no proposal."""
    assert await _loop(refine_service).tuning() == 0


async def test_a_driver_that_ended_hands_its_key_back_instead_of_deleting_it(
    refine_service: RefinementService, tmp_path
):
    """L4: `reconcile` iterates `loops` on the daemon's thread, and this runs on the host's.

    A `del` from here lands inside that comprehension, which is a `RuntimeError` on the thread
    the daemon's whole tick runs on, so the driver hands the key over and `reconcile` frees it.
    """
    loop = _loop(refine_service)
    daemon = _daemon_for(loop, tmp_path)
    key = repo_dir_key(loop.root)

    async def once(*, poll: float) -> LoopState:
        daemon.stopping = True
        return loop.state

    loop.tick = once
    await daemon._drive(loop)
    assert daemon.loops[key] is loop  # the driver wrote nothing
    assert [k for k, _held in daemon.ended] == [key]
    daemon.reconcile()
    assert daemon.loops == {} and list(daemon.ended) == []


@pytest.mark.parametrize(
    "fanout", [1, 2, 8], ids=["one-at-a-time", "in-pairs", "all-four-at-once"]
)
async def test_the_edit_batch_reads_its_files_in_chunks_of_this_repo_s_fanout(
    refine_service: RefinementService, fanout
):
    """L11: the fan-out was a bare constant, and a large batch is the thing it exists to bound.

    `_read` is replaced rather than driven through a repo, because what is being pinned is how
    many of them are in flight and nothing public reports that.
    """
    loop = _loop(refine_service)
    loop.service.user = _limited(refine_service.user, read_fanout=fanout)
    live, peak = 0, 0

    async def counted(path: str) -> EditedFile:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0)
        live -= 1
        return EditedFile(path=path)

    loop._read = counted
    read = await loop._read_all(tuple(f"f{n}.py" for n in range(4)))
    assert [f.path for f in read] == ["f0.py", "f1.py", "f2.py", "f3.py"]
    assert peak == min(fanout, 4)


async def _marker(service: RefinementService, node_id: str, name: str) -> int:
    """One in-force `unresolvable`, which is what the suspect pass reads to build its skip set."""
    run_id = await service.index.runs.add_run(
        Run(repo_identity=service.identity, started_at=1.0)
    )
    return await service.index.refinements.add_refinement(
        Refinement(
            run_id=run_id,
            repo_identity=service.identity,
            kind=RefinementKind.UNRESOLVABLE,
            reason="dispatched by a registry with no literal call site",
            target=RefinementTarget(node_id=node_id, name=name, reason_code="dynamic"),
            status=RefinementStatus.ACTIVE,
        )
    )


@pytest.mark.parametrize(
    ("cap", "found"), [(1, 1), (2, 2), (500, 3)], ids=["one", "two", "the-default"]
)
async def test_the_suppressed_read_takes_no_more_rows_than_this_repo_s_cap(
    refine_service: RefinementService, cap, found
):
    """Review M6: a suspect pass runs once a tick, so an unbounded read decodes the ledger that
    often. A marker past the cap stops suppressing, which is the cost the cap buys."""
    for n in range(3):
        await _marker(refine_service, f"a.py::f{n}", f"name{n}")
    loop = _loop(refine_service)
    loop.service.user = _limited(refine_service.user, max_suppressed_rows=cap)
    assert len(await loop.suppressed()) == found


@pytest.mark.parametrize(
    ("factor", "status"),
    [(1.0, RunStatus.SKIPPED), (2.0, RunStatus.RUNNING)],
    ids=["one-window", "two-windows"],
)
async def test_the_attach_sweep_gives_a_running_row_this_repo_s_own_longer_window(
    refine_service: RefinementService, factor, status
):
    """L11's sixth constant: the multiplier is a setting, and the attach is one of its readers."""
    refine_service.user = _limited(
        refine_service.user, stranded_run_seconds=2, stranded_running_factor=factor
    )
    run_id = await refine_service.index.runs.add_run(
        Run(
            repo_identity=refine_service.identity,
            started_at=time.time() - 3.0,
            status=RunStatus.RUNNING,
        )
    )
    await _loop(refine_service).attach()
    assert (await refine_service.index.runs.run(run_id)).status is status


async def test_the_daemon_writes_the_graph_block_the_status_line_reads(
    refine_service: RefinementService, tmp_path
):
    """C1: `graph` is the observer's block of `status.json`, written through `merge_status`."""
    loop = _loop(refine_service)
    daemon = _daemon_for(loop, tmp_path)
    before = int(time.time())
    await loop.attach()
    await daemon._publish(loop)
    block = json.loads(status_path(loop.root).read_text())["graph"]
    assert block["state"] == LoopState.OBSERVING.value
    # `len(await nodes())` and not `count_nodes()`: the reader under test on both sides of an
    # assertion is satisfied by a reader that returns a constant
    assert block["nodes"] == len(await refine_service.index.graph.nodes())
    assert block["refined"] == 0
    assert block["expiry_seconds"] == (
        refine_service.user.observer.scheduling.session_expiry_minutes * 60
    )
    assert block["written_at"] >= before


async def test_the_block_counts_the_refinements_the_build_applies_and_no_others(
    refine_service: RefinementService, tmp_path
):
    """`refined` is half of what the segment renders, and it was only ever asserted at zero.

    The filter matters as much as the number: a rejected refinement is not applied to the graph,
    so counting it would tell the user the build is carrying work it threw away.
    """
    loop = _loop(refine_service)
    daemon = _daemon_for(loop, tmp_path)
    await loop.attach()
    run_id = await refine_service.index.runs.add_run(
        Run(repo_identity=refine_service.identity, started_at=1.0)
    )
    for status in (RefinementStatus.ACTIVE, RefinementStatus.PINNED):
        await refine_service.index.refinements.add_refinement(
            Refinement(
                run_id=run_id,
                repo_identity=refine_service.identity,
                kind=RefinementKind.UNRESOLVABLE,
                reason="applied by the build",
                target=RefinementTarget(
                    node_id=f"n-{status.value}", name="x", reason_code="dynamic"
                ),
                status=status,
            )
        )
    await refine_service.index.refinements.add_refinement(
        Refinement(
            run_id=run_id,
            repo_identity=refine_service.identity,
            kind=RefinementKind.UNRESOLVABLE,
            reason="thrown away, so the build does not carry it",
            target=RefinementTarget(node_id="n-gone", name="x", reason_code="dynamic"),
            status=RefinementStatus.REJECTED,
        )
    )
    await daemon._publish(loop)
    assert json.loads(status_path(loop.root).read_text())["graph"]["refined"] == 2


async def test_an_unchanged_tick_takes_no_status_lock(
    refine_service: RefinementService, tmp_path
):
    """Two COUNTs per tick is cheap; a lock file per tick against a running scan is not."""
    loop = _loop(refine_service)
    daemon = _daemon_for(loop, tmp_path)
    await loop.attach()
    await daemon._publish(loop)
    written = status_path(loop.root)
    # a second writer clears the block; an unchanged publish must not put it back
    written.write_text(json.dumps({"scan": {"severity": {}}}))
    await daemon._publish(loop)
    assert "graph" not in json.loads(written.read_text())


async def test_a_quiet_repo_still_refreshes_the_block_before_it_reads_stale(
    refine_service: RefinementService, tmp_path
):
    """The status line reads the block as off once `written_at` is older than `expiry_seconds`.

    Nothing about an ordinary editing session moves the node count, the active refinement count
    or the loop state: editing an existing function adds no node, and no refinement runs without
    the `observer-claude` extra. So the content guard alone froze `written_at` at the last
    content change and a live, attached, observing repo rendered `graph off` after 45 minutes.
    """
    loop = _loop(refine_service)
    daemon = _daemon_for(loop, tmp_path)
    clock = {"now": 1_000.0}
    daemon.now = lambda: clock["now"]
    await loop.attach()
    await daemon._publish(loop)
    written = status_path(loop.root)
    written.write_text(json.dumps({"scan": {"severity": {}}}))
    expiry = refine_service.user.observer.scheduling.session_expiry_minutes * 60
    clock["now"] += expiry / 2 + 1
    await daemon._publish(loop)
    assert (
        json.loads(written.read_text())["graph"]["state"] == LoopState.OBSERVING.value
    )


async def test_a_block_that_could_not_be_written_is_not_recorded_as_written(
    refine_service: RefinementService, tmp_path, monkeypatch
):
    """The cache is the reason a tick takes no lock, so a cache that claims a write nothing did
    skips the block until the tuple changes again - and the failure it swallowed would be logged
    against the repo loop rather than against the status file."""
    loop = _loop(refine_service)
    daemon = _daemon_for(loop, tmp_path)
    await loop.attach()

    def broken(*args: object, **kw: object) -> None:
        raise RuntimeError("the status file is not writable")

    monkeypatch.setattr("auditor.observer.daemon.write_graph_status", broken)
    await daemon._publish(loop)
    assert daemon.blocks == {}
    monkeypatch.undo()
    await daemon._publish(loop)
    assert "graph" in json.loads(status_path(loop.root).read_text())


async def test_a_state_change_rewrites_the_block(
    refine_service: RefinementService, tmp_path
):
    loop = _loop(refine_service)
    daemon = _daemon_for(loop, tmp_path)
    await loop.attach()
    await daemon._publish(loop)
    loop.state = LoopState.RUNNING
    await daemon._publish(loop)
    assert json.loads(status_path(loop.root).read_text())["graph"]["state"] == "running"


async def test_the_node_count_reader_is_the_number_the_graph_holds(
    refine_service: RefinementService,
):
    """A COUNT rather than `len(await nodes())`, which decodes the whole graph for one number."""
    graph = refine_service.index.graph
    assert await graph.count_nodes() == len(await graph.nodes())
