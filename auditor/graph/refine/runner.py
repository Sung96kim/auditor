"""What drives a refinement run (spec 9.5).

The abstract runner opens a run, hands it its brief, and closes it; a subclass decides what happens
in between. `FakeRunner` replays a script, so every surface below the SDK is testable without one.
Deliberately free of any registry and any selection logic: `sdk_runner.py` imports this module, so
nothing here may import it back.
"""

import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from auditor.graph.refine.brief import Brief
from auditor.graph.refine.models import (
    ClientKind,
    ProducerKind,
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
from auditor.graph.refine.service import (
    CommitResult,
    RefinementRefused,
    RefinementService,
)

#: the in-process tool a proposal arrives through, as the trace names it
PROPOSE_TOOL = f"mcp__{GRAPH_SERVER}__propose"


class RunnerUnavailable(Exception):
    """The runner a caller asked for cannot run here, with the reason in the message."""


class RefinementJob(BaseModel):
    """One request to refine: what to work on, and who asked."""

    model_config = ConfigDict(frozen=True)

    scope: str = ""
    trigger: TriggerKind = TriggerKind.MANUAL
    producer: ProducerKind = ProducerKind.CLI
    client: ClientKind = ClientKind.CLI
    session_id: str | None = None
    agent_name: str | None = None
    #: ``None`` means the configured model
    model: str | None = None


class RunProduct(BaseModel):
    """Everything one run produced: the row it opened, the brief it worked from, how it ended, and
    what its commit landed.

    One frozen answer rather than state left on the runner, because both surfaces need the run row
    and the brief as well as the outcome, and a runner that remembered its last call could only
    ever drive one.
    """

    model_config = ConfigDict(frozen=True)

    run: Run
    brief: Brief
    outcome: RunOutcome
    #: ``None`` unless the run committed: an aborted or failed run lands nothing
    landed: CommitResult | None = None


class RefinementRunner(ABC):
    """One producer of refinements (spec 9.5): open, work, close.

    The client is injected rather than built, so the SDK-shaped half of a real runner is a test
    double in CI and the whole lifecycle below is exercised without one.
    """

    kind: ClassVar[RunnerKind]

    def __init__(
        self, service: RefinementService, client_factory: Callable[..., Any] | None
    ) -> None:
        self.service = service
        self.client_factory = client_factory

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

    async def _close(
        self,
        run: Run,
        brief: Brief,
        *,
        status: RunStatus,
        reason: str,
        attribution: RunAttribution,
    ) -> RunProduct:
        """Land or abandon the run, and answer with what the producer made of it.

        ``reason`` is the run's own summary when it succeeded and why it stopped otherwise. The
        stored row keeps the service's counted summary, which is the one every other surface
        reads; this outcome carries the producer's.

        A `commit` that refuses has already stamped the row through `_retire` and closed the run,
        so it is never followed by an `abort`: that would raise "not open in this process" out of
        this handler.
        """
        if status is not RunStatus.SUCCEEDED:
            terminate = (
                self.service.fail if status is RunStatus.FAILED else self.service.abort
            )
            await terminate(run.run_id, reason, attribution=attribution)
            return RunProduct(
                run=run,
                brief=brief,
                outcome=RunOutcome.of(status, error=reason, attribution=attribution),
            )
        try:
            landed = await self.service.commit(run.run_id, attribution=attribution)
        except RefinementRefused as exc:
            return RunProduct(
                run=run,
                brief=brief,
                outcome=RunOutcome.of(
                    RunStatus.FAILED, error=str(exc), attribution=attribution
                ),
            )
        return RunProduct(
            run=run,
            brief=brief,
            outcome=RunOutcome.of(
                RunStatus.SUCCEEDED, summary=reason, attribution=attribution
            ),
            landed=landed,
        )


class FakeRunner(RefinementRunner):
    """A runner that replays a scripted set of proposals, so the whole path runs with no SDK."""

    kind: ClassVar[RunnerKind] = RunnerKind.FAKE

    def __init__(
        self,
        service: RefinementService,
        client_factory: Callable[..., Any] | None = None,
        *,
        script: Sequence[Mapping[str, Any]] = (),
        answer: RunAnswer | None = None,
        fail_with: str | None = None,
    ) -> None:
        super().__init__(service, client_factory)
        self.script = script
        self.answer = answer
        self.fail_with = fail_with

    async def run(self, job: RefinementJob) -> RunProduct:
        run, brief = await self._open(job)
        trace: list[ToolCall] = []
        for proposal in self.script:
            verdict = await self.service.propose(run.run_id, proposal)
            trace.append(
                ToolCall(
                    tool=PROPOSE_TOOL, ts=time.time(), detail=verdict.outcome.value
                )
            )
        attribution = RunAttribution(
            usage=RunUsage(num_turns=len(self.script) + 1), tool_trace=tuple(trace)
        )
        summary = (
            self.answer.summary
            if self.answer is not None
            else f"{len(self.script)} proposed"
        )
        return await self._close(
            run,
            brief,
            status=RunStatus.SUCCEEDED if self.fail_with is None else RunStatus.ABORTED,
            reason=self.fail_with or summary,
            attribution=attribution,
        )
