"""The refinement lifecycle (spec 9.1).

`propose` judges one proposal against the facts and stages it; `commit` takes this checkout's
rebuild lock once and does the conflict checks, the inserts and the rebuild inside it. Staged
proposals live in the process that staged them and never touch the database, so a run that dies
loses exactly the work that was never promised.

One run is one critical section: `StagedRun` owns the lock, and `commit` and `abort` close the run
before their first real await.
"""

import asyncio
import sqlite3
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from auditor.config import AuditorSettings
from auditor.database import IndexStore
from auditor.database.refinements import NoSuchRun
from auditor.discovery import git_output
from auditor.graph.build import GraphBuilder
from auditor.graph.model import EdgeKind, GraphEdge, UnresolvedRow
from auditor.graph.payloads import CommitResult, GraphBuildReport
from auditor.graph.refine.brief import Brief
from auditor.graph.refine.conflicts import ConflictRules
from auditor.graph.refine.facts import BriefBuilder, FactReader
from auditor.graph.refine.lock import RebuildLockTimeout, rebuild_lock
from auditor.graph.refine.models import (
    Anchor,
    Assessment,
    Checkout,
    ClientKind,
    ProducerKind,
    Proposal,
    ProposalOutcome,
    PruneOutcome,
    Refinement,
    RefinementKind,
    RefinementStatus,
    RefusalKind,
    Run,
    RunAttribution,
    RunnerKind,
    RunOutcome,
    RunReport,
    RunStatus,
    Stratum,
    Tier,
    TriggerDetail,
    TriggerKind,
    Verdict,
    VerifyStatus,
)
from auditor.graph.refine.namespace import (
    file_of,
    scope_path,
    to_partition,
    to_toplevel,
    under_scope,
)
from auditor.graph.refine.prompts import SYSTEM_PROMPT_SHA
from auditor.graph.refine.tiers import TierPolicy
from auditor.graph.refine.verify import FactVerifier, VerifyResult
from auditor.roles import RoleClassifier
from auditor.user_settings import LimitsConfig, UserSettings

#: statuses a hand transition may leave; everything else is terminal (spec 5.4, 5.7)
_ACCEPT_FROM = frozenset({RefinementStatus.PENDING})
_REVERT_FROM = frozenset(
    {RefinementStatus.PENDING, RefinementStatus.ACTIVE, RefinementStatus.PINNED}
)
_PIN_FROM = frozenset(
    {RefinementStatus.PENDING, RefinementStatus.ACTIVE, RefinementStatus.STALE}
)


class RefinementRefused(RuntimeError):
    """A caller asked for something the service will not do, with the reason in the message."""

    @classmethod
    def lock_held(cls, exc: RebuildLockTimeout, *, detail: str) -> "RefinementRefused":
        """Another build held this checkout's rebuild lock for the whole budget."""
        return cls(f"{detail}: {exc.advice}")

    @classmethod
    def no_such_run(cls, run_id: str) -> "RefinementRefused":
        """A run id no row on this checkout's identity answers to."""
        return cls(f"no run {run_id} on this checkout")

    @classmethod
    def not_a_proposal(cls, exc: ValidationError) -> "RefinementRefused":
        """A payload no lenient read can rescue, refused in the service's own error type.

        There is no proposal to attribute a stored rejection to, and a caller that has to tell a
        pydantic traceback from a verdict has two contracts instead of one.
        """
        return cls(f"this payload is not a proposal: {exc.errors()[0]['msg']}")

    @classmethod
    def commit_failed(cls, run_id: str, exc: BaseException) -> "RefinementRefused":
        """A commit died after its git guard, so the caller learns which run to look up."""
        return cls(
            f"run {run_id} failed to commit: {exc}. Nothing it inserted is live, so a retry "
            "cannot land the same change twice."
        )


class ProposalFacts(BaseModel):
    """Everything one proposal is judged against, read once: the queue row it answers, the
    role-filtered definitions of the name, and a verifier over the files it names.
    """

    model_config = ConfigDict(frozen=True)

    row: UnresolvedRow | None = None
    definers: tuple[str, ...] = ()
    verifier: FactVerifier = Field(default_factory=FactVerifier)

    @classmethod
    async def of(cls, reader: FactReader, proposal: Proposal) -> "ProposalFacts":
        """Read all three in the caller's own namespace, before any id has been rebased."""
        row = await reader.queue_row(proposal)
        return cls(
            row=row,
            definers=tuple(await reader.definers(proposal, row)),
            verifier=await reader.verifier(proposal, row),
        )

    def check(self, proposal: Proposal) -> VerifyResult:
        return self.verifier.check(proposal, row=self.row, definers=self.definers)

    def anchors(self, proposal: Proposal) -> tuple[Anchor, ...]:
        return self.verifier.anchors(proposal, row=self.row)

    def stratum(self, proposal: Proposal) -> Stratum | None:
        """The add suite's stratum for this proposal's own edge, which is what tier B's gate reads
        (spec 10.2); ``None`` for a kind that names no edge."""
        edge = proposal.edge()
        if edge is None:
            return None
        imports = self.verifier.bindings.imported_module_ids(file_of(edge.src))
        return Stratum.of(edge.src, edge.dst, imports=imports)


class StagedProposal(BaseModel):
    """One accepted proposal waiting for `commit`, with the judgement that accepted it."""

    model_config = ConfigDict(frozen=True)

    proposal: Proposal
    tier: Tier
    status: RefinementStatus
    verify: VerifyStatus
    anchors: tuple[Anchor, ...] = ()

    def verdict(self, *, refinement_id: int = 0) -> Verdict:
        return Verdict(
            outcome=ProposalOutcome.STAGED,
            kind=self.proposal.kind,
            tier=self.tier,
            status=self.status,
            verify=self.verify,
            refinement_id=refinement_id,
        )


class Landing(BaseModel):
    """One staged proposal's decided fate at commit: the row to insert and the verdict it earns.

    Deciding the whole batch before writing any of it is what lets the inserts be one transaction,
    so a commit that dies part way through leaves nothing behind (spec 6).
    """

    model_config = ConfigDict(frozen=True)

    refinement: Refinement
    anchors: tuple[Anchor, ...] = ()
    verdict: Verdict

    def landed(self, refinement_id: int) -> Verdict:
        """The verdict with the id the insert assigned its row."""
        return self.verdict.model_copy(update={"refinement_id": refinement_id})


class StagedRun(BaseModel):
    """One open run: its row, the partition it was opened against, the scope it may touch, and what
    it has staged.

    Deliberately mutable: it is filled in as the run proceeds and read when it commits. ``lock``
    makes one run one critical section, so two tool calls on the same run cannot interleave between
    a read and the write that depends on it. ``closed`` is set before a terminal method does any
    real work, so the second caller is refused rather than repeating it.
    """

    model_config = ConfigDict(frozen=False, arbitrary_types_allowed=True)

    run: Run
    partition: tuple[str, str]  # (identity, prefix) as `begin` resolved them
    scope: str = ""
    staged: list[StagedProposal] = Field(default_factory=list)
    closed: bool = False
    opened_at: float = Field(default_factory=time.time)
    lock: asyncio.Lock = Field(default_factory=asyncio.Lock)

    def covers(self, proposal: Proposal) -> bool:
        """Whether every node id the proposal names falls under this run's scope.

        `anchored_ids` is the enumeration the partition guard already uses, cluster members
        included: a `relabel_cluster` names none of the three ids a narrower reading looks at, so
        it would be in scope for every run at once.
        """
        return all(under_scope(i, self.scope) for i in proposal.anchored_ids())

    def require_open(self) -> None:
        """Refuse a run another caller in this process already finished, by what happened to it."""
        if self.closed:
            raise RefinementRefused(
                f"run {self.run.run_id} is already closed in this process: it was committed, "
                "aborted or evicted"
            )

    def partition_moved(self, current: tuple[str, str]) -> str | None:
        """Whether this call resolves the partition the run's ids were rebased with."""
        if current == self.partition:
            return None
        return (
            f"this run was opened against partition {self.partition} and this call resolved "
            f"{current}; commit from the same root you began on"
        )

    def holds(self, proposal: Proposal) -> bool:
        """Whether this run already staged exactly this proposal.

        The conflict rules only see other runs' committed work, so without this a run that proposed
        the same edge twice would insert it twice.
        """
        return any(item.proposal == proposal for item in self.staged)

    def collides(self, proposal: Proposal) -> str | None:
        """A staged proposal that already answers this (src, kind, short name) with another dst.

        `ConflictRules` reads `ACTIVE_STATUSES` rows, so it can see neither this run's staging nor
        its own just-inserted rows: two `add_edge`s for one queue name would both land `pending`
        and contradict each other the moment either is accepted.
        """
        edge = proposal.edge()
        if edge is None:
            return None
        for item in self.staged:
            other = item.proposal.edge()
            if other is None or other.src != edge.src or other.kind is not edge.kind:
                continue
            if other.name == edge.name and other.dst != edge.dst:
                return (
                    f"this run already points {edge.src} at {other.dst} for {edge.name}"
                )
        return None


class RunRegistry(BaseModel):
    """The runs one process has open on one repo identity. Process-local by design (spec 9.1's
    staging step).

    Bounded: an agent that opens a run and then stops is the normal end of a session, and a
    long-lived MCP server would otherwise hold every one of them, with their proposals and anchors,
    for the life of the process.
    """

    model_config = ConfigDict(frozen=False, arbitrary_types_allowed=True)

    #: evicted ids remembered past their run, so `require` can tell a caller what happened
    REMEMBERED: ClassVar[int] = 64
    #: one registry per repo identity (spec 5.2), keyed the way the identity tables are
    PROCESS: ClassVar[dict[str, "RunRegistry"]] = {}

    open_runs: dict[str, StagedRun] = Field(default_factory=dict)
    #: overwritten from `user.observer.limits.max_open_runs` by every service built on it
    max_open: int = int(LimitsConfig.model_fields["max_open_runs"].default)
    evicted: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def process(cls, identity: str) -> "RunRegistry":
        """The registry every service on ``identity`` stages into unless it is handed another.

        An MCP server builds a service per tool call and takes the repo path per call, so one
        process holds runs from several checkouts. Keyed by identity, an eviction can only drop a
        run whose rows this handle addresses, and one repo's cap cannot evict another's runs.
        """
        registry = cls.PROCESS.get(identity)
        if registry is None:
            registry = cls.PROCESS[identity] = cls()
        return registry

    def opened(
        self, run: Run, scope: str, partition: tuple[str, str]
    ) -> tuple[StagedRun, list[StagedRun]]:
        """Register a run, and hand back the runs evicted to make room for it.

        Eviction drops staging that was never promised; finishing the `graph_runs` row each evicted
        run owns needs a store, so it belongs to the caller (Invariant 2).
        """
        gone: list[StagedRun] = []
        while len(self.open_runs) >= self.max_open:
            oldest = min(self.open_runs.values(), key=lambda s: s.opened_at)
            oldest.closed = True
            gone.append(self.open_runs.pop(oldest.run.run_id))
            self._remember(oldest.run.run_id)
        staged = StagedRun(run=run, scope=scope, partition=partition)
        self.open_runs[run.run_id] = staged
        return staged, gone

    def reason(self) -> str:
        """Why a run was evicted, in the words the stored row and the refusal both use."""
        return f"evicted: registry full (max_open={self.max_open})"

    def require(self, run_id: str) -> StagedRun:
        """The open run, or a refusal that says which of the two things went wrong."""
        staged = self.open_runs.get(run_id)
        if staged is not None:
            return staged
        why = self.evicted.get(run_id)
        if why is not None:
            raise RefinementRefused(
                f"run {run_id} was {why}; its staging was stored as rejections, so start a "
                "new run"
            )
        raise RefinementRefused(f"run {run_id} is not open in this process")

    def close(self, run_id: str) -> None:
        self.open_runs.pop(run_id, None)

    def _remember(self, run_id: str) -> None:
        """Keep the newest evictions only: this exists to explain a refusal, not to be a log."""
        self.evicted[run_id] = self.reason()
        while len(self.evicted) > self.REMEMBERED:
            del self.evicted[next(iter(self.evicted))]


class RefinementLedger(BaseModel):
    """The by-hand half of the lifecycle: spec 5.4 and 5.7's status transitions and the retention
    sweep, over one index handle.

    It needs neither a checkout root nor a run registry nor a git guard, which is what a
    `graph refinements accept <id>` surface has to work without.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    index: IndexStore

    async def accept(self, refinement_id: int) -> Refinement:
        """Activate a pending refinement. The next build applies it; this takes no lock."""
        return await self._moved(refinement_id, RefinementStatus.ACTIVE, _ACCEPT_FROM)

    async def revert(self, refinement_id: int) -> Refinement:
        return await self._moved(refinement_id, RefinementStatus.REVERTED, _REVERT_FROM)

    async def pin(self, refinement_id: int) -> Refinement:
        return await self._moved(refinement_id, RefinementStatus.PINNED, _PIN_FROM)

    async def prune(
        self, retention_days: int, *, stranded_seconds: int
    ) -> PruneOutcome:
        """Finish the runs a dead process left open, then drop the assessment-only rows older than
        the retention window, with the rejections they own (spec 5.1, 5.7).

        Stranded first: a run left `queued` is not yet a row the retention sweep can see.
        """
        stranded = await self.index.runs.finish_stranded_runs(
            older_than=stranded_seconds
        )
        swept = await self.index.runs.prune_skipped_runs(retention_days)
        return swept.model_copy(update={"stranded_runs": stranded})

    async def refinement(self, refinement_id: int) -> Refinement:
        """One refinement by id, refused by name rather than answered with ``None``."""
        found = await self.index.refinements.refinement(refinement_id)
        if found is None:
            raise RefinementRefused(f"no refinement {refinement_id} on this checkout")
        return found

    async def _moved(
        self,
        refinement_id: int,
        status: RefinementStatus,
        allowed: frozenset[RefinementStatus],
    ) -> Refinement:
        """One hand transition, refusing by name rather than updating nothing."""
        current = await self.refinement(refinement_id)
        if current.status not in allowed:
            raise RefinementRefused(
                f"refinement {refinement_id} is {current.status.value}; "
                f"only {sorted(s.value for s in allowed)} can become {status.value}"
            )
        await self.index.refinements.set_status(refinement_id, status)
        return await self.refinement(refinement_id)


class RefinementService:
    """Spec 9.1's lifecycle over one index handle and one checkout."""

    def __init__(
        self,
        index: IndexStore,
        root: Path,
        settings: AuditorSettings,
        user: UserSettings,
        registry: RunRegistry | None = None,
        facts: FactReader | None = None,
    ) -> None:
        self.index = index
        self.root = root
        self.settings = settings
        self.user = user
        self.registry = (
            registry if registry is not None else RunRegistry.process(self.identity)
        )
        # the registry is per identity and so is the settings file this repo's overlay is read
        # from, so the cap this service was built with is the cap that identity runs under
        self.registry.max_open = user.observer.limits.max_open_runs
        self.ledger = RefinementLedger(index=index)
        # an injected reader is how an eval gives the brief and the verifier one masked queue
        self.facts = facts or FactReader(
            index=index, root=root, roles=RoleClassifier(settings.role_globs)
        )

    @property
    def identity(self) -> str:
        return self.index.partition.identity

    @property
    def prefix(self) -> str:
        return self.index.partition.prefix

    @property
    def partition(self) -> tuple[str, str]:
        """What every stored id in a run is relative to: a commit must resolve the same pair."""
        return (self.identity, self.prefix)

    async def _open(
        self,
        *,
        scope: str = "",
        producer: ProducerKind = ProducerKind.AGENT,
        client: ClientKind = ClientKind.CLI,
        trigger: TriggerKind = TriggerKind.MANUAL,
        runner: RunnerKind = RunnerKind.NONE,
        model: str | None = None,
        session_id: str | None = None,
        agent_name: str | None = None,
        detail: TriggerDetail | None = None,
        checkout: Checkout | None = None,
    ) -> Run:
        """Write one queued ``graph_runs`` row: the half of :meth:`begin` that stages nothing.

        ``scope`` arrives already through ``scope_path``; ``checkout`` is for a caller that has
        read HEAD itself, so a gate deciding in milliseconds does not spawn two `git rev-parse`
        processes to say no.
        """
        run = Run.begin(
            partition=self.partition,
            origin=self.index.repo,
            scope=scope,
            checkout=checkout or await self._head(),
            client=client,
            producer=producer,
            runner=runner,
            trigger=trigger,
            model=model,
            session_id=session_id,
            agent_name=agent_name,
            detail=detail,
        )
        await self.index.runs.add_run(run)
        return run

    async def begin(
        self,
        *,
        scope: str = "",
        producer: ProducerKind = ProducerKind.AGENT,
        client: ClientKind = ClientKind.CLI,
        trigger: TriggerKind = TriggerKind.MANUAL,
        runner: RunnerKind = RunnerKind.NONE,
        model: str | None = None,
        session_id: str | None = None,
        agent_name: str | None = None,
        detail: TriggerDetail | None = None,
        checkout: Checkout | None = None,
    ) -> Run:
        """Open a run and record who asked and against which checkout state (Invariant 2).

        ``scope`` is a repo-relative path prefix; one that could never name a node here is refused
        rather than silently refusing every proposal the run makes.
        """
        try:
            scope = scope_path(scope)
        except ValueError as exc:
            raise RefinementRefused(str(exc)) from exc
        run = await self._open(
            scope=scope,
            producer=producer,
            client=client,
            trigger=trigger,
            runner=runner,
            model=model,
            session_id=session_id,
            agent_name=agent_name,
            detail=detail,
            checkout=checkout,
        )
        _staged, evicted = self.registry.opened(run, scope, self.partition)
        for gone in evicted:
            await self._evict(gone)
        return run

    async def decline(
        self,
        assessment: Assessment,
        *,
        checkout: Checkout | None = None,
        client: ClientKind = ClientKind.CLI,
        trigger: TriggerKind = TriggerKind.EDIT,
        session_id: str | None = None,
    ) -> Run:
        """Record one batch the gate declined as its own run row, spending nothing (spec 8.6).

        Opened with ``runner=none`` and closed ``skipped`` before any runner could exist, with the
        reason on the assessment rather than in ``error``, which belongs to runs that broke. It
        stages nothing, so a skip can never evict a run that is holding proposals.

        Raises:
            RefinementRefused: the assessment decided to run, and a run row is not a skip row.
        """
        if assessment.decided_to_run:
            raise RefinementRefused(
                f"assessment decided to run: {assessment.verdict.reason}. "
                "Open the run through `begin`"
            )
        run = await self._open(
            producer=ProducerKind.OBSERVER,
            client=client,
            trigger=trigger,
            runner=RunnerKind.NONE,
            session_id=session_id,
            detail=TriggerDetail(files=assessment.files, assessment=assessment),
            checkout=checkout,
        )
        await self._finish(run.run_id, RunStatus.SKIPPED)
        return await self._stored(run.run_id)

    async def propose(
        self, run_id: str, proposal: Proposal | Mapping[str, Any]
    ) -> Verdict:
        """Validate, verify and tier one proposal, then stage it or store the rejection.

        ``proposal`` may be the payload a tool was called with: `Proposal` owns spec 9.2's shape
        and text rules, so an illegal one is refused here rather than re-checked. The caller names
        ids the way its own partition sees them; everything from staging on is toplevel-relative,
        which is the namespace the identity tables use (spec 5.2).
        """
        staged = self.registry.require(run_id)
        async with staged.lock:
            staged.require_open()
            return await self._judge(staged, proposal)

    async def _judge(
        self, staged: StagedRun, raw: Proposal | Mapping[str, Any]
    ) -> Verdict:
        """One proposal, under the run's lock: admissibility, facts, tier, staging."""
        run_id = staged.run.run_id
        try:
            proposal, complaint = Proposal.read(raw)
        except ValidationError as exc:
            raise RefinementRefused.not_a_proposal(exc) from exc
        stored = proposal.rebased(self.prefix)
        if complaint:
            return await self._reject(
                run_id,
                stored,
                VerifyStatus.UNVERIFIED,
                complaint,
                refusal=RefusalKind.INVALID,
            )
        refused = await self._refused(staged, proposal, stored)
        if refused is not None:
            return refused
        facts = await ProposalFacts.of(self.facts, proposal)
        result = facts.check(proposal)
        if not result.accepted:
            return await self._reject(run_id, stored, result.status, result.detail)
        policy = await self._policy(staged.run)
        tier = policy.tier(proposal, row=facts.row, verified=result.checked)
        item = StagedProposal(
            proposal=stored,
            tier=tier,
            status=policy.status(proposal.kind, tier, stratum=facts.stratum(proposal)),
            verify=result.status,
            anchors=tuple(a.rebased(self.prefix) for a in facts.anchors(proposal)),
        )
        staged.staged.append(item)
        return item.verdict()

    async def status(self, run_id: str) -> RunReport:
        """One run as a reader sees it: the stored row, plus this process's staging."""
        run = await self.index.runs.run(run_id)
        if run is None:
            raise RefinementRefused.no_such_run(run_id)
        open_run = self.registry.open_runs.get(run_id)
        rows = await self.index.refinements.of_run(run_id)
        return RunReport(
            run=run,
            staged=tuple(item.verdict() for item in open_run.staged)
            if open_run
            else (),
            staged_here=open_run is not None,
            committed=tuple(
                r.refinement_id
                for r in rows
                if r.status is not RefinementStatus.REJECTED
            ),
            rejected=tuple(
                r.refinement_id for r in rows if r.status is RefinementStatus.REJECTED
            ),
        )

    async def build_brief(self, scope: str, *, commit_sha: str | None = None) -> Brief:
        """The brief for one scope, off this checkout's queue under this user's limits.

        The one construction: `brief`, `preview` and the bound `brief` tool all come through here
        rather than each assembling a builder of their own.
        """
        return await BriefBuilder(
            facts=self.facts, limits=self.user.observer.limits
        ).build(scope, commit_sha=commit_sha)

    async def preview(self, scope: str) -> Brief:
        """The brief a run over ``scope`` would be given, opening no run and recording nothing.

        Raises:
            ValueError: the scope could never name a node in this checkout.
        """
        return await self.build_brief(scope, commit_sha=(await self._head()).commit_sha)

    async def brief(self, run_id: str) -> Brief:
        """The brief this run works from, recorded on its row the first time it is handed over.

        The prompt and the sha of the rules it was written under are stored here rather than at
        `begin`, which runs before there is a brief to store (Invariant 2). What is stored is what
        this call returns, verdicts included; a re-read gets the verdicts earned since and writes
        nothing, so the row keeps what the run was first asked rather than the last thing it read.
        """
        staged = self.registry.require(run_id)
        built = await self.build_brief(staged.scope, commit_sha=staged.run.commit_sha)
        brief = built.model_copy(
            update={"staged": tuple(item.verdict() for item in staged.staged)}
        )
        stored = await self.index.runs.run(run_id)
        if stored is None:
            raise RefinementRefused.no_such_run(run_id)
        if stored.prompt is None:
            try:
                await self.index.runs.record_prompt(
                    run_id,
                    prompt=brief.render(),
                    system_prompt_sha=SYSTEM_PROMPT_SHA,
                )
            except NoSuchRun as exc:
                raise RefinementRefused.no_such_run(run_id) from exc
        return brief

    async def commit(
        self, run_id: str, *, attribution: RunAttribution | None = None
    ) -> CommitResult:
        """Land one run under the rebuild lock (spec 6, spec 9.1).

        The partition is checked while the run is still open, because a caller that committed from
        the wrong root has to be able to retry from the right one. Everything after that closes the
        run first, so a second `commit` is refused by name rather than inserting the same rows again.
        """
        staged = self.registry.require(run_id)
        async with staged.lock:
            staged.require_open()
            moved = staged.partition_moved(self.partition)
            if moved is not None:
                raise RefinementRefused(moved)
            staged.closed = True
            self.registry.close(run_id)
            return await self._land_all(staged, attribution)

    async def _land_all(
        self, staged: StagedRun, attribution: RunAttribution | None = None
    ) -> CommitResult:
        """The body of one commit: the git guard, then the lock, the inserts and the rebuild.

        The whole batch is decided first and inserted as one transaction, and the rebuild follows
        inside the same lock, so a commit that dies leaves no live row a later `accept` could
        activate and no queue row retired by a build that never happened (spec 6).
        """
        run_id = staged.run.run_id
        refused = await self._checkout_moved(staged.run)
        if refused is not None:
            await self._finish(
                run_id, RunStatus.REJECTED, error=refused, attribution=attribution
            )
            raise RefinementRefused(refused)
        if not staged.staged:
            # spec 6 wants the queue rows retired in the same lock as the insert; with no insert
            # there is nothing to retire, so this takes no lock and runs no build
            await self._finish(
                run_id,
                RunStatus.SUCCEEDED,
                summary=await self._summary(run_id, attribution),
                attribution=attribution,
            )
            return CommitResult(run_id=run_id, rebuilt=False)
        landed: list[Verdict] = []
        try:
            async with rebuild_lock(
                self.identity,
                poll=self.settings.graph.rebuild_lock_poll_seconds,
                timeout=self.settings.graph.rebuild_lock_timeout_seconds,
            ):
                landed = await self._insert(await self._decided(staged))
                build = await GraphBuilder().rebuild(
                    self.index, self.settings, lock_held=True
                )
        except RebuildLockTimeout as exc:
            await self._retire(run_id, landed, str(exc), attribution=attribution)
            raise RefinementRefused.lock_held(
                exc, detail=f"run {run_id} committed nothing"
            ) from exc
        # `Exception`, not `BaseException`: a cancelled task cannot be trusted to await its own
        # bookkeeping, and catching `CancelledError` here would swallow the cancellation
        except Exception as exc:
            await self._retire(run_id, landed, str(exc), attribution=attribution)
            raise RefinementRefused.commit_failed(run_id, exc) from exc
        committed = tuple(v for v in landed if v.outcome is ProposalOutcome.STAGED)
        rejected = tuple(v for v in landed if v.outcome is ProposalOutcome.REJECTED)
        await self._finish(
            run_id,
            RunStatus.SUCCEEDED,
            summary=await self._summary(run_id, attribution),
            attribution=attribution,
        )
        return CommitResult(
            run_id=run_id, committed=committed, rejected=rejected, build=build
        )

    async def _summary(
        self, run_id: str, attribution: RunAttribution | None = None
    ) -> str:
        """What this run produced: the producer's own line when it gave one, else its rows counted.

        Counted rather than taken from the batch: a rejection stored at propose time is a row this
        run owns too, and a summary built from the landing alone reported "0 rejected" over four.
        """
        if attribution is not None and attribution.summary:
            return attribution.summary
        counts = (await self.index.refinements.counts_by_run([run_id])).get(run_id)
        return counts.summary if counts else "nothing staged"

    async def _decided(self, staged: StagedRun) -> list[Landing]:
        """What this commit will insert, worked out before anything is written."""
        rules = await self._conflict_rules(staged)
        policy = await self._policy(staged.run)
        return [
            self._decide(staged.run.run_id, item, rules, policy)
            for item in staged.staged
        ]

    async def _insert(self, landings: Sequence[Landing]) -> list[Verdict]:
        """Write one commit's whole batch as a single transaction: all of it, or none of it."""
        refinements = self.index.refinements

        def write(conn: sqlite3.Connection) -> list[int]:
            return [
                refinements.write_refinement(conn, item.refinement, item.anchors)
                for item in landings
            ]

        ids = await self.index.transaction(write)
        return [item.landed(rid) for item, rid in zip(landings, ids, strict=True)]

    async def _retire(
        self,
        run_id: str,
        landed: Sequence[Verdict],
        error: str,
        *,
        attribution: RunAttribution | None = None,
    ) -> None:
        """Fail the run and take back anything it inserted, so nothing it wrote stays live.

        The inserts are one transaction, so ``landed`` is empty unless the rebuild after them
        failed; those rows are real, and `accept` reads a refinement's own status, never its run's.
        """
        live = [
            v.refinement_id
            for v in landed
            if v.status in (RefinementStatus.PENDING, RefinementStatus.ACTIVE)
        ]
        await self.index.refinements.set_statuses(live, RefinementStatus.REJECTED)
        await self._finish(
            run_id, RunStatus.FAILED, error=error, attribution=attribution
        )

    async def abort(
        self, run_id: str, reason: str, *, attribution: RunAttribution | None = None
    ) -> Run:
        """Drop this run's staging and stamp it aborted (spec 9.1). Nothing was stored.

        The cost survives: a run that stopped at its turn or budget cap still spent what it spent,
        and only the proposals it never promised are lost.
        """
        return await self.terminate(
            run_id, RunStatus.ABORTED, reason, attribution=attribution
        )

    async def terminate(
        self,
        run_id: str,
        status: RunStatus,
        reason: str,
        *,
        attribution: RunAttribution | None = None,
    ) -> Run:
        """Close an open run under its own lock and stamp it, dropping whatever it staged.

        The status is the caller's: a producer that knows how its run ended must not have that
        mapped back out of a method name, and a future one is stamped as itself.
        """
        staged = self.registry.require(run_id)
        async with staged.lock:
            staged.require_open()
            staged.closed = True
            self.registry.close(run_id)
            await self._finish(run_id, status, error=reason, attribution=attribution)
        return await self._stored(run_id)

    async def _stored(self, run_id: str) -> Run:
        """Re-read a row this service just wrote, refusing if it somehow is not there."""
        run = await self.index.runs.run(run_id)
        if run is None:  # the row was written moments ago, so this cannot happen
            raise RefinementRefused.no_such_run(run_id)
        return run

    async def prune(self) -> PruneOutcome:
        """The ledger's retention sweep at this user's configured windows (spec 5.1, 5.7)."""
        return await self.ledger.prune(
            self.user.observer.skipped_retention_days,
            stranded_seconds=self.user.observer.limits.stranded_run_seconds,
        )

    async def rebuild(self) -> GraphBuildReport:
        """A build under the same lock a commit takes, and under the same timeout.

        A caller that changed a status by hand has to see the graph move; waiting on the lock for
        ever behind a wedged `auditr graph build` would hang whatever asked.
        """
        try:
            return await GraphBuilder().rebuild(
                self.index,
                self.settings,
                timeout=self.settings.graph.rebuild_lock_timeout_seconds,
            )
        except RebuildLockTimeout as exc:
            raise RefinementRefused.lock_held(
                exc, detail="the rebuild did not run"
            ) from exc

    async def _refused(
        self, staged: StagedRun, proposal: Proposal, stored: Proposal
    ) -> Verdict | None:
        """The stored rejection this proposal earns before any fact is read, or ``None``.

        Cheapest rule first: only the last one reads the database, so a refusal that needs no query
        costs none. Every one of them is stored, which is what spec 9.2 asks for.
        """
        run_id = staged.run.run_id
        cap = self.user.observer.limits.max_changes_per_run
        collision = staged.collides(stored)
        rules: tuple[tuple[bool, RefusalKind, str], ...] = (
            (
                len(staged.staged) >= cap,
                RefusalKind.OVER_CAP,
                f"this run is at max_changes_per_run ({cap})",
            ),
            (
                not staged.covers(proposal),
                RefusalKind.OUT_OF_SCOPE,
                f"the proposal names ids outside this run's scope {staged.scope!r}",
            ),
            (
                staged.holds(stored),
                RefusalKind.ALREADY_STAGED,
                "this run already staged an identical proposal",
            ),
            (collision is not None, RefusalKind.INTRA_BATCH, collision or ""),
        )
        for tripped, kind, detail in rules:
            if tripped:
                return await self._reject(
                    run_id, stored, VerifyStatus.UNVERIFIED, detail, refusal=kind
                )
        for node_id in proposal.anchored_ids():
            if await self.index.graph.node(node_id) is None:
                return await self._reject(
                    run_id,
                    stored,
                    VerifyStatus.UNVERIFIED,
                    f"{node_id} is not a node in this partition; name ids the way "
                    "graph_unresolved shows them",
                    refusal=RefusalKind.OUT_OF_PARTITION,
                )
        return None

    async def _policy(self, run: Run) -> TierPolicy:
        """This repo's activation policy for the run's runner and model (spec 10.3)."""
        return TierPolicy.of(
            await self.index.evals.latest(run.runner, run.model or ""),
            min_precision=self.user.observer.tuning.min_precision,
            runner=run.runner,
            model=run.model or "",
        )

    async def _conflict_rules(self, staged: StagedRun) -> ConflictRules:
        """The prior work this commit is checked against, in the toplevel namespace the staged
        proposals and the active refinements already use.

        `graph_edges` is a partition table, so its src has to go down into the partition for the
        query and the rows have to come back up as models. `edges_of` answers `src = ? OR dst = ?`
        and every rule keys on `src`, so the inbound half is inert rather than filtered out.
        """
        edges: dict[tuple[str, str, EdgeKind], GraphEdge] = {}
        for item in staged.staged:
            src, _dst = item.proposal.edge_pair()
            local = to_partition(src, self.prefix) if src is not None else None
            if local is None:
                continue
            for row in await self.index.graph.edges_of(local, None):
                edge = GraphEdge.model_validate(
                    {
                        **row,
                        "src": to_toplevel(str(row["src"]), self.prefix),
                        "dst": to_toplevel(str(row["dst"]), self.prefix),
                    }
                )
                edges[(edge.src, edge.dst, edge.kind)] = edge
        return ConflictRules.of(
            await self.index.refinements.active(), list(edges.values())
        )

    def _decide(
        self,
        run_id: str,
        item: StagedProposal,
        rules: ConflictRules,
        policy: TierPolicy,
    ) -> Landing:
        """The row one staged proposal earns, or the rejection a conflict earns it (spec 9.1).

        Pure: nothing here reads or writes the store, so a whole commit's decisions exist before
        its first insert does.
        """
        conflict = rules.check(item.proposal)
        if conflict is None:
            return Landing(
                refinement=self._row(item.proposal, run_id, item.tier, item.status),
                anchors=item.anchors,
                verdict=item.verdict(),
            )
        if (
            conflict.rewrite_as_confirm
            and item.proposal.kind is RefinementKind.ADD_EDGE
        ):
            confirmation = Proposal(
                kind=RefinementKind.CONFIRM_EDGE,
                target=item.proposal.target,
                payload=item.proposal.payload,
                reason=f"{item.proposal.reason} ({conflict.detail})",
                evidence=item.proposal.evidence,
                confidence=item.proposal.confidence,
            )
            # the tier and status of a confirmation are `tiers.py`'s answer, not a literal here
            tier = policy.tier(
                confirmation, row=None, verified=item.verify is VerifyStatus.OK
            )
            status = policy.status(confirmation.kind, tier)
            return Landing(
                refinement=self._row(confirmation, run_id, tier, status),
                anchors=item.anchors,
                verdict=Verdict(
                    outcome=ProposalOutcome.STAGED,
                    kind=RefinementKind.CONFIRM_EDGE,
                    tier=tier,
                    status=status,
                    verify=item.verify,
                    detail=conflict.detail,
                ),
            )
        return Landing(
            refinement=Refinement.rejected(
                item.proposal,
                run_id=run_id,
                repo_identity=self.identity,
                detail=conflict.detail,
                status=conflict.stored_status,
            ),
            verdict=Verdict(
                outcome=ProposalOutcome.REJECTED,
                kind=item.proposal.kind,
                status=conflict.stored_status,
                verify=item.verify,
                detail=conflict.detail,
            ),
        )

    def _row(
        self, proposal: Proposal, run_id: str, tier: Tier, status: RefinementStatus
    ) -> Refinement:
        """One accepted proposal as the row this checkout stores it as."""
        return Refinement.of(
            proposal,
            run_id=run_id,
            repo_identity=self.identity,
            tier=tier,
            status=status,
        )

    async def _reject(
        self,
        run_id: str,
        proposal: Proposal,
        verify: VerifyStatus,
        detail: str,
        *,
        status: RefinementStatus = RefinementStatus.REJECTED,
        refusal: RefusalKind | None = None,
    ) -> Verdict:
        """Store the rejection the moment it is made, so an aborted run still explains itself.

        ``proposal`` is already toplevel-relative: every caller rebases before it gets here.
        """
        rid = await self.index.refinements.add_refinement(
            Refinement.rejected(
                proposal,
                run_id=run_id,
                repo_identity=self.identity,
                detail=detail,
                status=status,
            )
        )
        return Verdict(
            outcome=ProposalOutcome.REJECTED,
            kind=proposal.kind,
            status=status,
            verify=verify,
            refusal=refusal,
            detail=detail,
            refinement_id=rid,
        )

    async def _evict(self, gone: StagedRun) -> None:
        """Finish a run the registry dropped, so no row is left `queued` (Invariant 2).

        Its staging was never promised, but it is stored as rejections rather than vanishing, and
        the row goes `skipped`, which is the one status `prune_skipped_runs` can ever reap. The
        registry is keyed by identity, so the evicted run's rows and this handle's are the same
        identity's: a rejection cannot land in another repo's table and `finish_run` cannot miss.
        """
        detail = self.registry.reason()
        async with (
            gone.lock
        ):  # a propose already in flight finishes before its run is retired
            for item in gone.staged:
                await self._reject(gone.run.run_id, item.proposal, item.verify, detail)
            await self._finish(gone.run.run_id, RunStatus.SKIPPED, error=detail)

    async def _head(self) -> Checkout:
        """This checkout's branch and HEAD, read off the event loop.

        `git_output` shells out with a 30 s timeout, and every other coroutine on the loop, which
        for an MCP server is every other tool call, would wait behind it.
        """
        branch, commit = await asyncio.gather(
            asyncio.to_thread(
                git_output, self.root, "rev-parse", "--abbrev-ref", "HEAD"
            ),
            asyncio.to_thread(git_output, self.root, "rev-parse", "HEAD"),
        )
        return Checkout(branch=branch, commit_sha=commit)

    async def _checkout_moved(self, run: Run) -> str | None:
        """Whether HEAD or the branch changed since `begin`, which invalidates every anchor.

        A run that did not start in a git checkout has nothing to compare and is never refused;
        an edit is not a move, and the verifier already caught that per file.
        """
        if run.commit_sha is None:
            return None
        now = await self._head()
        if now.branch == run.branch and now.commit_sha == run.commit_sha:
            return None
        return (
            f"the checkout moved during the run ({run.branch}@{run.commit_sha} to "
            f"{now.branch}@{now.commit_sha}); start a new run"
        )

    async def _finish(
        self,
        run_id: str,
        status: RunStatus,
        *,
        summary: str | None = None,
        error: str | None = None,
        attribution: RunAttribution | None = None,
    ) -> None:
        await self.index.runs.finish_run(
            run_id,
            RunOutcome.of(
                status, summary=summary, error=error, attribution=attribution
            ),
        )
