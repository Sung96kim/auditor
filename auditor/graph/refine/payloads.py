"""The wire models for a `RefinementService` result (spec 9.1, spec 12.3).

Separate from `auditor/graph/payloads.py`: this module reaches the service, and therefore the graph
build and numpy, which no fast CLI command may load. `Verdict` is emitted as itself.
"""

from auditor.graph.payloads import GraphBuildReport, RunRowPayload
from auditor.graph.refine.models import RefinementCounts
from auditor.graph.refine.service import CommitResult, RunReport, Verdict
from auditor.payload import WirePayload


class CommitPayload(WirePayload):
    """One `graph_refine_commit` answer: what landed, what did not, and the build that followed.

    ``build`` is null and ``rebuilt`` false for a run that staged nothing: there was no insert, so
    there was no queue row to retire and no reason to hold the rebuild lock.
    """

    run_id: str
    committed: tuple[Verdict, ...] = ()
    rejected: tuple[Verdict, ...] = ()
    landed: int = 0
    rebuilt: bool = True
    build: GraphBuildReport | None = None

    @classmethod
    def of(cls, result: CommitResult) -> "CommitPayload":
        return cls(
            run_id=result.run_id,
            committed=result.committed,
            rejected=result.rejected,
            landed=result.landed,
            rebuilt=result.rebuilt,
            build=result.build,
        )


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
