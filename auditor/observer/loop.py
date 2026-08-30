"""Spec 8.3's RepoLoop: what the observer does with one repo, in priority order.

The loop owns every side effect the assessment refuses to have (spec 8.6): it reads the cached
facts, extracts, persists, rebuilds with the two snapshots, gates, then opens or declines a run.
Its events arrive through an injected feed, so a spool drain and a test harness are one seam.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from auditor.config import AuditorSettings
from auditor.database import IndexStore
from auditor.discovery import FileDiscovery, git_status_paths
from auditor.fingerprints import content_hash
from auditor.graph.build import GraphBuilder
from auditor.graph.extract import extract_file_facts
from auditor.graph.hashes import file_hashes
from auditor.graph.model import FileGraphFacts, UnresolvedRow
from auditor.graph.refine.lock import RebuildLockTimeout
from auditor.graph.refine.models import (
    ACTIVE_STATUSES,
    Assessment,
    AssessmentDecision,
    BatchKind,
    ClientKind,
    Decision,
    NodePair,
    ProducerKind,
    Proposal,
    ProposalOutcome,
    Proposer,
    Refinement,
    RefinementKind,
    RefinementStatus,
    RunnerKind,
    RunStatus,
    SnapshotPhase,
    TriggerDetail,
    TriggerKind,
    Verdict,
)
from auditor.graph.refine.runner import RefinementJob, RefinementRunner
from auditor.graph.refine.service import RefinementRefused, RefinementService
from auditor.graph.refine.tiers import TierPolicy
from auditor.graph.scan import autoscan
from auditor.observer.assess import (
    CachedFile,
    EditedFile,
    GraphSnapshot,
    NodeDigest,
    PathOutcome,
    QueuePair,
    RefinementState,
    Stage1,
    assess,
    assess_unchanged,
    cap_by_node,
    decide,
    stage_one,
)
from auditor.observer.budget import (
    BudgetState,
    budget_state,
    priced_runner,
    window_start,
)
from auditor.observer.events import Event
from auditor.observer.scheduling import (
    MINUTE,
    Backoff,
    EventFeed,
    LoopState,
    Pauses,
    Retries,
    RunGuard,
    RunSlots,
    debounced,
    pause_of,
    read_guard,
)
from auditor.roles import RoleClassifier
from auditor.user_settings import UserSettings

logger = logging.getLogger(__name__)


class RepoLoop:
    """One attached repo's work, in spec 8.3's priority order.

    Every collaborator is injected: the feed so the daemon's drain and a test harness are one
    seam, the runner factory so `FakeRunner` drives the whole path at $0, and the clock so the
    day window and the rate-limit deadline are decided without sleeping.
    """

    def __init__(
        self,
        *,
        root: Path,
        index: IndexStore,
        settings: AuditorSettings,
        service: RefinementService,
        feed: EventFeed,
        runner_for: Callable[[RefinementService, Proposer | None], RefinementRunner],
        slots: RunSlots | None = None,
        now: Callable[[], float] = time.time,
        on_change: Callable[[], object] = lambda: None,
        status: Callable[[Path], tuple[str, ...] | None] = git_status_paths,
    ) -> None:
        self.root = root
        self.index = index
        self.settings = settings
        self.feed = feed
        self.service = service
        self.runner_for = runner_for
        self.slots = slots or RunSlots()
        self.now = now
        self.on_change = on_change
        self.status = status
        scheduling = self.user.observer.scheduling
        self.pauses = Pauses(
            errors=Backoff(
                first=scheduling.error_backoff_seconds,
                ceiling=scheduling.max_error_backoff_seconds,
            )
        )
        self.retries = Retries()
        self.deferred: tuple[NodePair, ...] = ()
        #: the budget this tick already read, so `/api/status` draws what the tick acted on (M8)
        self.last_budget: BudgetState | None = None
        self.state = LoopState.DETACHED
        self._held: tuple[Event, ...] = ()
        self._kind: RunnerKind | None = None
        self._roles = RoleClassifier(settings.role_globs)

    @property
    def user(self) -> UserSettings:
        """The one settings object: the service holds it, so the two can never disagree (M-6)."""
        return self.service.user

    # --- state ---------------------------------------------------------------

    def _moved(self, state: LoopState) -> LoopState:
        """Record a state change and tell the daemon, whose `/api/status` ETag is that counter."""
        if state is not self.state:
            self.state = state
            self.on_change()
        return state

    def failed(self, error: BaseException) -> float:
        """Name the exception for the page, take the hold, and answer the driver's wait (L2).

        The hold itself is `Pauses.failed`'s; what this adds is the state move, which is what
        puts `paused:error` on the badge.
        """
        wait = self.pauses.failed(str(error) or type(error).__name__, now=self.now())
        self._moved(LoopState.PAUSED_ERROR)
        return wait

    @property
    def runner_kind(self) -> RunnerKind:
        """Which runner this loop opens runs with, resolved once: a factory re-reads credentials."""
        if self._kind is None:
            self._kind = self.runner_for(self.service, None).kind
        return self._kind

    async def budget(self) -> BudgetState:
        """This repo's day, and whether this runner and model have ever been measured here."""
        spend = await self.index.runs.spend_since(window_start(self.now()))
        runner = self.user.observer.runner
        policy = TierPolicy.of(
            await self.index.evals.latest(self.runner_kind, runner.model),
            min_precision=self.user.observer.tuning.min_precision,
            runner=self.runner_kind,
            model=runner.model,
        )
        return budget_state(
            spend,
            config=self.user.observer.budget,
            priced=priced_runner(self.runner_kind, runner),
            evaluated=bool(policy.measured),
        )

    async def _guard(self) -> RunGuard:
        """Spec 8.5's pre-run read, taken as late as possible so HEAD is what the run will use."""
        return await read_guard(self.root, status=self.status)

    # --- work item 1: the session-start build --------------------------------

    async def attach(self) -> LoopState:
        """Spec 8.3 item 1: an incremental scan with extraction forced on, then a rebuild.

        The scan is forced independently of ``graph.enabled`` (D2), and the rebuild is the one
        that marks refinements stale and redundant; the loop only has to let it run. The stranded
        sweep runs first, because a daemon killed mid-run is what this attach is recovering from.
        """
        self._moved(LoopState.BUILDING)
        self.pauses.cleared()
        limits = self.user.observer.limits
        await self.index.runs.finish_stranded_runs(
            older_than=limits.stranded_run_seconds,
            running_factor=limits.stranded_running_factor,
        )
        await autoscan(self.root)
        reason = "session-start build"
        try:
            await GraphBuilder().rebuild(
                self.index,
                self.settings,
                timeout=self.settings.graph.rebuild_lock_timeout_seconds,
            )
        except RebuildLockTimeout as timeout:
            logger.warning("session-start build skipped: %s", timeout.advice)
            reason = "session-start build skipped: the rebuild lock was held"
        guard = await self._guard()
        # spec 5.3's `session_start` trigger: the one row that says the observer attached here
        await self.service.decline(
            Assessment(verdict=_reason(reason)),
            checkout=guard.checkout,
            dirty=guard.dirty,
            client=ClientKind.CLAUDE_CODE,
            trigger=TriggerKind.SESSION_START,
        )
        return self._moved(LoopState.OBSERVING)

    def detach(self) -> LoopState:
        return self._moved(LoopState.DETACHED)

    # --- the ladder ----------------------------------------------------------

    async def tick(self, *, poll: float = 1.0) -> LoopState:
        """One pass of spec 8.3's ladder: the highest item with work to do wins.

        The feed is drained before the pause is read, so a repo that cannot spend still empties
        its spool; a batch drained while paused is held and assessed when the pause lifts.
        """
        scheduling = self.user.observer.scheduling
        batch = (
            *self._held,
            *await debounced(
                self.feed,
                seconds=float(scheduling.debounce_seconds),
                timeout=poll,
                restarts=scheduling.debounce_restart_cap,
            ),
        )
        budget = await self.budget()
        self.last_budget = budget
        held = batch[-self.user.observer.limits.max_held_events :]
        pause = self.pauses.state(budget=budget, now=self.now())
        if pause is not None:
            self._held = held
            return self._moved(pause)
        self._moved(LoopState.OBSERVING)
        if batch:
            # kept until the batch is assessed, so a raise in `edit_batch` cannot lose the edits
            self._held = held
            await self.edit_batch(batch, budget=budget)
            self._held = ()
            return self.state
        self._held = ()
        if self.user.observer.suspects and await self.suspects(budget=budget):
            return self.state
        if await self.verify(budget=budget):
            return self.state
        await self.tuning()
        return self.state

    # --- work item 2: edit batches -------------------------------------------

    def _paths(self, events: Sequence[Event]) -> tuple[str, ...]:
        """The batch's auditable paths, first appearance first (stage 0 already ran on them).

        Capped: a resumed loop can hold `max_held_events` events of `MAX_EVENT_PATHS` paths each,
        and extracting all of them in one coroutine would hold the ladder for minutes.
        """
        finder = FileDiscovery(self.root)
        seen: dict[str, None] = {}
        for event in events:
            for path in event.paths:
                if finder.auditable_shape(path):
                    seen.setdefault(path, None)
        cap = self.user.observer.limits.max_paths_per_batch
        if len(seen) > cap:
            logger.warning("edit batch of %d paths truncated to %d", len(seen), cap)
        return tuple(seen)[:cap]

    async def _read(self, path: str) -> EditedFile:
        """One edited path as stage 1 needs it: the cache read *before* anything is written."""
        graph = self.index.graph
        blob, cached_hash, hashes = await asyncio.gather(
            graph.facts(path), graph.facts_hash(path), graph.hashes(path)
        )
        cached = None
        if blob is not None:
            nodes = FileGraphFacts.model_validate_json(blob).nodes
            cached = CachedFile(
                content_hash=cached_hash,
                hashes=hashes,
                node_hashes=tuple(NodeDigest.of(node) for node in nodes),
            )
        source = await asyncio.to_thread(_read_text, self.root / path)
        if source is None:
            return EditedFile(path=path, cached=cached)
        extracted = await asyncio.to_thread(
            extract_file_facts, path, source, self._roles.classify(path, source).value
        )
        return EditedFile(
            path=path,
            cached=cached,
            content_hash=content_hash(source),
            extracted=extracted,
        )

    async def _read_all(self, paths: Sequence[str]) -> list[EditedFile]:
        """Every edited path, a bounded chunk at a time so one large batch still yields."""
        out: list[EditedFile] = []
        fanout = self.user.observer.limits.read_fanout
        for start in range(0, len(paths), fanout):
            chunk = paths[start : start + fanout]
            out.extend(await asyncio.gather(*(self._read(path) for path in chunk)))
        return out

    async def _persist(self, stage1: Stage1, edited: dict[str, EditedFile]) -> None:
        """Write what stage 1 chose to keep, as one commit, so the rebuild reads this edit whole."""
        gone = [
            v.path
            for v in stage1.verdicts
            if v.persist and v.outcome is PathOutcome.REMOVED
        ]
        written = [
            (
                verdict.path,
                edited[verdict.path].extracted.model_dump_json(),
                edited[verdict.path].content_hash or "",
                file_hashes(edited[verdict.path].extracted.nodes),
            )
            for verdict in stage1.verdicts
            if verdict.persist
            and verdict.outcome is not PathOutcome.REMOVED
            and edited[verdict.path].extracted is not None
        ]
        await self.index.graph.replace_facts(removed=gone, written=written)

    async def snapshot(self) -> GraphSnapshot:
        """The queue and the refinement statuses at one side of the rebuild's persist (spec 6)."""
        # `external=True` is the store's default and the assessment's own filter drops the rest
        rows, refinements = await asyncio.gather(
            self.index.graph.unresolved(),
            self.index.refinements.refinements(),
        )
        return GraphSnapshot(
            pairs=tuple(QueuePair.of(UnresolvedRow.model_validate(r)) for r in rows),
            refinements=tuple(RefinementState.of(r) for r in refinements),
        )

    async def edit_batch(
        self, events: Sequence[Event], *, budget: BudgetState | None = None
    ) -> Assessment | None:
        """Spec 8.6 end to end: stage 1, the persist, the rebuild, stage 2, then run or decline."""
        paths = self._paths(events)
        if not paths:
            return None
        edited = dict(zip(paths, await self._read_all(paths), strict=True))
        stage1 = stage_one(tuple(edited.values()))
        if not stage1.needs_rebuild:
            return await self._declined(assess_unchanged(stage1))
        await self._persist(stage1, edited)
        sides: dict[SnapshotPhase, GraphSnapshot] = {}

        async def capture(phase: SnapshotPhase) -> None:
            sides[phase] = await self.snapshot()

        try:
            await GraphBuilder().rebuild(
                self.index,
                self.settings,
                snapshot=capture,
                timeout=self.settings.graph.rebuild_lock_timeout_seconds,
            )
        except RebuildLockTimeout:
            return await self._declined(_no_rebuild(stage1))
        if not {SnapshotPhase.BEFORE, SnapshotPhase.AFTER} <= set(sides):
            return await self._declined(_no_rebuild(stage1))
        spend = budget if budget is not None else await self.budget()
        assessment = assess(
            stage1,
            before=sides[SnapshotPhase.BEFORE],
            after=sides[SnapshotPhase.AFTER],
            scheduling=self.user.observer.scheduling,
            budget=spend,
            max_nodes_per_run=self.user.observer.limits.max_nodes_per_run,
        )
        self._defer(assessment.deferred)
        if not assessment.decided_to_run:
            return await self._declined(assessment)
        targets = self.retries.keep(assessment.targets)
        if not targets:
            return await self._declined(
                assessment.model_copy(
                    update={
                        "verdict": _reason("every target had spent its one retry"),
                        "targets": (),
                    }
                )
            )
        await self._run(
            targets=targets,
            trigger=TriggerKind.EDIT,
            files=stage1.files,
            assessment=assessment,
        )
        return assessment

    def _defer(self, pairs: Sequence[NodePair]) -> None:
        """Item 2's leftovers, newest last and capped: intent, not a fact about the repo (P18)."""
        held = dict.fromkeys((*self.deferred, *pairs))
        self.deferred = tuple(held)[-self.user.observer.limits.max_deferred_pairs :]

    async def _declined(self, assessment: Assessment) -> Assessment:
        """Invariant 2: a batch that reached stage 1 is a run row, whatever the gate said."""
        guard = await self._guard()
        await self.service.decline(
            assessment,
            checkout=guard.checkout,
            dirty=guard.dirty,
            client=ClientKind.CLAUDE_CODE,
        )
        return assessment

    # --- work item 3: suspect runs -------------------------------------------

    async def cooldown(self) -> frozenset[NodePair]:
        """The pairs a recent run already looked at (spec 8.3 item 3; recon Q8)."""
        minutes = self.user.observer.scheduling.cooldown_minutes
        if minutes <= 0:
            return frozenset()
        return await self.index.runs.targeted_since(self.now() - minutes * MINUTE)

    async def suppressed(self) -> frozenset[NodePair]:
        """Pairs an in-force `unresolvable` or a `redundant` refinement answers (spec 8.3, 5.7).

        Two filtered reads rather than the whole ledger, and each is bounded by
        `max_suppressed_rows`: a reverted `unresolvable` is not a marker in force, and a suspect
        pass runs once a tick, so an unbounded read decodes the ledger that often (review M6).
        """
        ledger = self.index.refinements
        cap = self.user.observer.limits.max_suppressed_rows
        settled, redundant = await asyncio.gather(
            ledger.refinements(
                kinds=[RefinementKind.UNRESOLVABLE],
                statuses=sorted(ACTIVE_STATUSES),
                newest_first=True,
                limit=cap,
            ),
            ledger.refinements(
                statuses=[RefinementStatus.REDUNDANT], newest_first=True, limit=cap
            ),
        )
        out: set[NodePair] = set()
        for row in (*settled, *redundant):
            node_id = row.target.node_id or row.target.src
            if node_id and row.target.name:
                out.add(NodePair(node_id=node_id, name=row.target.name))
        return frozenset(out)

    async def suspects(self, *, budget: BudgetState | None = None) -> bool:
        """Spec 8.3 item 3: drain `graph_unresolved` in the store's own priority order.

        Item 2's deferred pairs are drained here, ahead of the queue's own order, because the
        assessment that deferred them already judged them worth a run.
        """
        queue = await self._queue_pairs()
        live = frozenset(queue)
        # a rebuild replaces the queue wholesale (spec 5.6), so a deferred pair can stop existing
        self.deferred = tuple(p for p in self.deferred if p in live)
        skip = (await self.cooldown()) | (await self.suppressed())
        wanted = (
            pair
            for pair in dict.fromkeys((*self.deferred, *queue))
            if pair not in skip and self.retries.allowed(pair)
        )
        chosen = cap_by_node(
            wanted, max_nodes=self.user.observer.limits.max_nodes_per_run
        ).chosen
        if not chosen:
            return False
        verdict, _pairs = decide(
            new_pairs=chosen,
            bounded_pairs=chosen,
            stale_refinements=(),
            scheduling=self.user.observer.scheduling,
            budget=budget if budget is not None else await self.budget(),
            kind=BatchKind.SUSPECT,
        )
        if verdict.decision is not AssessmentDecision.RUN:
            return False
        opened = await self._run(targets=chosen, trigger=TriggerKind.SUSPECT, files=())
        if opened:
            self.deferred = tuple(p for p in self.deferred if p not in chosen)
        return opened

    async def _queue_pairs(self) -> tuple[NodePair, ...]:
        """The queue in its stored drain order, which already encodes spec 8.3's priorities.

        Bounded: an observing loop reads this once a tick and the cap only ever takes the first
        `max_nodes_per_run` distinct nodes off the front of it.
        """
        # `external=False` hides the rows a brief hides, so the drain cannot pick one it cannot ask
        rows = await self.index.graph.unresolved(
            external=False, limit=self.user.observer.limits.max_queue_rows_per_pass
        )
        return tuple(NodePair(node_id=row["node_id"], name=row["name"]) for row in rows)

    # --- work item 4: verify runs --------------------------------------------

    async def verify_cooled(self) -> bool:
        """Whether item 4 already had its turn inside `verify_cooldown_minutes` (H1).

        Silence leaves a refinement `pending`, which is the default outcome, so without a window
        one unsettled row would open a fresh run on every tick for as long as it sits there.
        """
        minutes = self.user.observer.scheduling.verify_cooldown_minutes
        if minutes <= 0:
            return False
        opened = await self.index.runs.opened_since(
            TriggerKind.VERIFY, self.now() - minutes * MINUTE
        )
        return opened > 0

    async def verify(self, *, budget: BudgetState | None = None) -> bool:
        """Spec 10.3's second opinion for `pending` refinements, shown no first pick.

        The verify run's proposals are judged and never stored: the injected proposer is the seam
        an eval already uses, so a second opinion cannot insert a second copy of the correction.
        """
        # a fake run proposes nothing, so it can only ever leave the rows it was opened for pending
        if self.runner_kind is RunnerKind.FAKE or await self.verify_cooled():
            return False
        pending = await self.index.refinements.refinements(
            statuses=[RefinementStatus.PENDING],
            limit=self.user.observer.limits.max_nodes_per_run,
        )
        if not pending:
            return False
        verdict, _pairs = decide(
            new_pairs=(),
            bounded_pairs=(),
            stale_refinements=tuple(r.refinement_id for r in pending),
            scheduling=self.user.observer.scheduling,
            budget=budget if budget is not None else await self.budget(),
            kind=BatchKind.VERIFY,
        )
        if verdict.decision is not AssessmentDecision.RUN:
            return False
        seen: list[tuple[str, str, str]] = []

        async def judging(run_id: str, proposal: Mapping[str, Any]) -> Verdict:
            read = Proposal.model_validate(proposal)
            edge = read.edge()
            if edge is not None:
                seen.append((edge.src, read.target.name or "", edge.dst))
            return Verdict(outcome=ProposalOutcome.STAGED, kind=read.kind)

        targets = tuple(
            NodePair(
                node_id=row.target.node_id or row.target.src or "",
                name=row.target.name or "",
            )
            for row in pending
            if (row.target.node_id or row.target.src) and row.target.name
        )
        if not targets:
            # a job with no targets is briefed on the whole scope, which is not a second opinion
            logger.warning("verify: %d pending rows name no target pair", len(pending))
            return False
        opened = await self._run(
            targets=targets,
            trigger=TriggerKind.VERIFY,
            files=(),
            proposer=judging,
        )
        if opened:
            await self._judge_pending(pending, seen)
        return opened

    async def _judge_pending(
        self, pending: Sequence[Refinement], seen: Sequence[tuple[str, str, str]]
    ) -> None:
        """Agreement promotes to `active`, a contradiction rejects, silence leaves it pending."""
        agreed: list[int] = []
        rejected: list[int] = []
        for row in pending:
            edge = row.target
            src, name = edge.src or edge.node_id or "", edge.name or ""
            answers = [dst for s, n, dst in seen if s == src and n == name]
            if not answers:
                continue
            if edge.dst in answers:
                agreed.append(row.refinement_id)
            else:
                rejected.append(row.refinement_id)
        # both through the guarded transition, so a row a human moved keeps its status (M-2)
        await self.service.ledger.accept_all(agreed)
        await self.service.ledger.reject_all(rejected)

    # --- work item 5: tuning trials ------------------------------------------

    async def tuning(self) -> int:
        """Spec 8.3 item 5's slot, which S11 fills: the ladder reaches it and finds no proposal.

        Nothing tuning-shaped is written anywhere yet, so a trial harness here would be S11's
        whole slice arriving early (recon Q6).
        """
        return 0

    # --- opening a run -------------------------------------------------------

    async def _run(
        self,
        *,
        targets: Sequence[NodePair],
        trigger: TriggerKind,
        files: Sequence[str],
        assessment: Assessment | None = None,
        proposer: Proposer | None = None,
    ) -> bool:
        """Open one target-driven run under the concurrency gate, and say whether it landed.

        The gate waits rather than refusing: a repo already running, or two runs already in flight
        anywhere, holds this coroutine until a slot frees (spec 8.4), so a run always opens. What
        it answers is whether the run reached a terminal state that answered its targets.
        """
        async with self.slots.slot(self.service.identity):
            self._moved(LoopState.RUNNING)
            guard = await self._guard()
            runner = self.runner_for(self.service, proposer)
            job = RefinementJob(
                trigger=trigger,
                producer=ProducerKind.OBSERVER,
                client=ClientKind.CLAUDE_CODE,
                detail=TriggerDetail(
                    files=tuple(files), targets=tuple(targets), assessment=assessment
                ),
                checkout=guard.checkout,
                dirty=guard.dirty,
            )
            try:
                product = await runner.run(job)
            except RefinementRefused as refused:
                logger.warning("observer run refused: %s", refused)
                self.retries.aborted(targets)
                self._moved(LoopState.OBSERVING)
                return False
            stored = await self.index.runs.run(product.run.run_id)
            landed = stored is None or stored.status not in _UNLANDED
            if not landed:
                self.retries.aborted(targets)
            scheduling = self.user.observer.scheduling
            pause = pause_of(
                stored.error if stored else None,
                now=self.now(),
                minutes=scheduling.ratelimit_pause_minutes,
                auth_minutes=scheduling.auth_pause_minutes,
            )
            if pause is None or pause.state is not LoopState.PAUSED_AUTH:
                # the run reached the model, so the credentials work: drop any auth hold (H-3)
                self.pauses.authenticated()
            self.pauses.apply(pause)
            self._moved(LoopState.OBSERVING)
            return landed


#: the terminal statuses that mean this run's targets were not answered (spec 8.5)
_UNLANDED = frozenset(
    {RunStatus.ABORTED, RunStatus.FAILED, RunStatus.REJECTED, RunStatus.SKIPPED}
)


def _reason(text: str) -> Decision:
    """One skip verdict, so the three places that write one share its shape."""
    return Decision(decision=AssessmentDecision.SKIP, reason=text)


def _no_rebuild(stage1: Stage1) -> Assessment:
    """The assessment for a batch whose rebuild did not run, so no snapshot pair exists."""
    return assess_unchanged(stage1).model_copy(
        update={"verdict": _reason("the rebuild did not run")}
    )


def _read_text(path: Path) -> str | None:
    """One edited file's bytes, or ``None`` for a path the edit deleted (spec 8.6 stage 1)."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
