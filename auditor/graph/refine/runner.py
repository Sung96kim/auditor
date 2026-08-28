"""What drives a refinement run (spec 9.5).

The abstract runner opens a run, hands it its brief, and closes it; a subclass decides what happens
in between. `FakeRunner` replays a script, so every surface below the SDK is testable without one.
Deliberately free of any registry and any selection logic: `sdk_runner.py` imports this module, so
nothing here may import it back.
"""

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from auditor.graph.payloads import CommitResult
from auditor.graph.refine.brief import Brief
from auditor.graph.refine.client import ClientFactory
from auditor.graph.refine.models import (
    ClientKind,
    ProducerKind,
    Proposer,
    Run,
    RunAttribution,
    RunnerKind,
    RunOutcome,
    RunStatus,
    RunUsage,
    ToolCall,
    TriggerKind,
)
from auditor.graph.refine.prompts import GRAPH_SERVER, RunAnswer
from auditor.graph.refine.service import RefinementRefused, RefinementService
from auditor.user_settings import ClaudeModel, Runner

logger = logging.getLogger(__name__)

#: the in-process tool a proposal arrives through, as the trace names it
PROPOSE_TOOL = f"mcp__{GRAPH_SERVER}__propose"


class RunnerUnavailable(Exception):
    """The runner a caller asked for cannot run here, with the reason in the message."""


class RefinementJob(BaseModel):
    """One request to refine: what to work on, who asked, and what to drive it with.

    The runner and the model are typed rather than passed through: this is the one boundary both
    surfaces cross, so a value neither `Runner` nor `ClaudeModel` admits is refused here, before
    a run row exists to orphan.
    """

    model_config = ConfigDict(frozen=True)

    scope: str = ""
    trigger: TriggerKind = TriggerKind.MANUAL
    producer: ProducerKind = ProducerKind.CLI
    client: ClientKind = ClientKind.CLI
    session_id: str | None = None
    agent_name: str | None = None
    #: ``None`` means the configured model
    model: ClaudeModel | None = None
    #: ``None`` means the configured runner
    runner: Runner | None = None


class RunProduct(BaseModel):
    """Everything one run produced: the row it opened, the brief it worked from, and what its
    commit landed.

    One frozen answer rather than state left on the runner, because both surfaces need the run row
    and the brief, and a runner that remembered its last call could only ever drive one. How the
    run ended is not here: the stored row is the state of record, and a second copy of it on the
    product is a second copy to disagree.
    """

    model_config = ConfigDict(frozen=True)

    run: Run
    brief: Brief
    #: ``None`` unless the run committed: an aborted or failed run lands nothing
    landed: CommitResult | None = None


class RefinementRunner(ABC):
    """One producer of refinements (spec 9.5): open, work, close.

    The client is injected rather than built, so the SDK-shaped half of a real runner is a test
    double in CI and the whole lifecycle below is exercised without one.
    """

    kind: ClassVar[RunnerKind]

    def __init__(
        self,
        service: RefinementService,
        client_factory: ClientFactory | None,
        *,
        proposer: Proposer | None = None,
    ) -> None:
        self.service = service
        self.client_factory = client_factory
        # an eval judges proposals instead of storing them, so nothing it proposes reaches a row
        self.proposer = proposer or service.propose

    @abstractmethod
    async def run(self, job: RefinementJob) -> RunProduct:
        """Drive one run from `begin` to its terminal state, and report what it produced."""

    async def _open(self, job: RefinementJob) -> tuple[Run, Brief]:
        """Open the run and record the brief on its row before any work happens."""
        run = await self.service.begin(
            scope=job.scope,
            producer=job.producer,
            client=job.client,
            trigger=job.trigger,
            runner=self.kind,
            model=job.model or self.service.user.observer.runner.model,
            session_id=job.session_id,
            agent_name=job.agent_name,
        )
        return run, await self.service.brief(run.run_id)

    async def _close(self, run: Run, brief: Brief, outcome: RunOutcome) -> RunProduct:
        """Land or abandon the run the way ``outcome`` says, and answer with what it produced.

        One guard over both terminal writes: a run the registry evicted refuses either of them,
        and a `commit` that refuses has already stamped the row through `_retire` and closed the
        run, so a second write would raise "not open in this process" out of this handler. Either
        way the row already says what happened, which is why nothing is re-stamped here.
        """
        try:
            if outcome.status is RunStatus.SUCCEEDED:
                landed = await self.service.commit(run.run_id, attribution=outcome)
                return RunProduct(run=run, brief=brief, landed=landed)
            await self.service.terminate(
                run.run_id, outcome.status, outcome.error or "", attribution=outcome
            )
        except RefinementRefused as exc:
            logger.warning("run %s closed itself: %s", run.run_id, exc)
        return RunProduct(run=run, brief=brief)

    async def _propose_one(self, run_id: str, proposal: Mapping[str, Any]) -> ToolCall:
        """Stage one proposal and answer with the trace entry it earned, refusal included.

        A payload no `Proposal` can be read out of is refused outright rather than stored, and a
        producer that let that escape would orphan its own open run.
        """
        try:
            detail = (await self.proposer(run_id, proposal)).outcome.value
        except RefinementRefused as exc:
            detail = str(exc)
        return ToolCall(tool=PROPOSE_TOOL, ts=time.time(), detail=detail)


class FakeRun(BaseModel):
    """What one `FakeRunner` pretends its run did, so the double takes one shape not five knobs.

    ``stop`` drives the non-succeeded half: the status is the caller's, so `failed` is reachable
    here rather than only through a real client.
    """

    model_config = ConfigDict(frozen=True)

    script: tuple[Mapping[str, Any], ...] = ()
    #: a producer's own closing line; without one the row counts the rows it landed
    answer: RunAnswer | None = None
    stop: str | None = None
    stop_status: RunStatus = RunStatus.ABORTED
    #: what this run reports having spent; without one it counts its own turns and nothing else
    usage: RunUsage | None = None


class FakeRunner(RefinementRunner):
    """A runner that replays a scripted set of proposals, so the whole path runs with no SDK."""

    kind: ClassVar[RunnerKind] = RunnerKind.FAKE

    def __init__(
        self,
        service: RefinementService,
        client_factory: ClientFactory | None = None,
        *,
        proposer: Proposer | None = None,
        pretend: FakeRun | None = None,
    ) -> None:
        super().__init__(service, client_factory, proposer=proposer)
        self.pretend = pretend or FakeRun()

    async def run(self, job: RefinementJob) -> RunProduct:
        run, brief = await self._open(job)
        pretend = self.pretend
        trace = [await self._propose_one(run.run_id, p) for p in pretend.script]
        attribution = RunAttribution(
            usage=pretend.usage or RunUsage(num_turns=len(pretend.script) + 1),
            tool_trace=tuple(trace),
            summary=pretend.answer.summary if pretend.answer is not None else None,
        )
        outcome = (
            RunOutcome.of(
                pretend.stop_status, error=pretend.stop, attribution=attribution
            )
            if pretend.stop is not None
            else RunOutcome.of(RunStatus.SUCCEEDED, attribution=attribution)
        )
        return await self._close(run, brief, outcome)
