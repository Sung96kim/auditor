"""The wire model for a `RefinementService` report (spec 9.1, spec 12.3).

Separate from `auditor/graph/payloads.py`: this module reaches the refinement models and the brief,
which no fast CLI command loads. `Verdict` and `CommitResult` are emitted as themselves; only a
`RunReport`, which carries a whole 30-field `Run`, needs narrowing.
"""

from auditor.graph.payloads import CommitResult, GraphBuildReport, RunRowPayload
from auditor.graph.refine.brief import Brief, BriefLimits, BriefTarget, StaleNote
from auditor.graph.refine.models import (
    RefinementCounts,
    RunnerChoiceCode,
    RunReport,
    Verdict,
)
from auditor.graph.refine.prompts import SYSTEM_PROMPT_SHA
from auditor.payload import WirePayload


class RunReportPayload(WirePayload):
    """One `graph_refine_status` answer: the run, what this process has staged, and the rows it
    already owns, split by fate."""

    run: RunRowPayload
    staged: tuple[Verdict, ...] = ()
    staged_here: bool = True
    committed: tuple[int, ...] = ()
    rejected: tuple[int, ...] = ()

    @classmethod
    def of(cls, report: RunReport) -> "RunReportPayload":
        return cls(
            run=RunRowPayload.of(
                report.run,
                refinements=RefinementCounts(
                    committed=len(report.committed), rejected=len(report.rejected)
                ),
            ),
            staged=report.staged,
            staged_here=report.staged_here,
            committed=report.committed,
            rejected=report.rejected,
        )


class BriefPayload(WirePayload):
    """One brief on the wire: the structured rows, and the rendered text a runner would send.

    ``run_id`` is ``None`` for the preview `auditr graph refine --brief` renders, which opens no
    run and therefore records no prompt.
    """

    run_id: str | None = None
    scope: str = ""
    commit_sha: str | None = None
    targets: tuple[BriefTarget, ...] = ()
    queue_total: int = 0
    stale: tuple[StaleNote, ...] = ()
    limits: BriefLimits
    staged: tuple[Verdict, ...] = ()
    prompt: str
    system_prompt_sha: str

    @classmethod
    def of(cls, brief: Brief, *, run_id: str | None = None) -> "BriefPayload":
        return cls(
            run_id=run_id,
            scope=brief.scope,
            commit_sha=brief.commit_sha,
            targets=brief.targets,
            queue_total=brief.queue_total,
            stale=brief.stale,
            limits=brief.limits,
            staged=brief.staged,
            prompt=brief.render(),
            system_prompt_sha=SYSTEM_PROMPT_SHA,
        )


class RefinePayload(WirePayload):
    """One `auditr graph refine` answer: the run row, what it was briefed on, and what it landed.

    ``choice`` is the machine code the runner was selected under, which the run row cannot carry:
    the row says which runner drove it, this says why that one. A refusal never reaches here,
    because `drive.refine` raises rather than returning a payload with no run behind it.
    """

    run: RunRowPayload
    choice: RunnerChoiceCode
    scope: str = ""
    targets: int = 0
    queue_total: int = 0
    committed: tuple[Verdict, ...] = ()
    rejected: tuple[Verdict, ...] = ()
    build: GraphBuildReport | None = None

    @classmethod
    def of(
        cls,
        report: RunReport,
        brief: Brief,
        landed: CommitResult | None,
        choice: RunnerChoiceCode,
    ) -> "RefinePayload":
        return cls(
            run=RunRowPayload.of(
                report.run,
                refinements=RefinementCounts(
                    committed=len(report.committed), rejected=len(report.rejected)
                ),
            ),
            choice=choice,
            scope=brief.scope,
            targets=len(brief.targets),
            queue_total=brief.queue_total,
            committed=landed.committed if landed else (),
            rejected=landed.rejected if landed else (),
            build=landed.build if landed else None,
        )
