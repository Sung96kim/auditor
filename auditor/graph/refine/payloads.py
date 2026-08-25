"""The wire model for a `RefinementService` report (spec 9.1, spec 12.3).

Separate from `auditor/graph/payloads.py`: this module reaches the service, and therefore the graph
build and numpy, which no fast CLI command may load. `Verdict` and `CommitResult` are emitted as
themselves; only a `RunReport`, which carries a whole 30-field `Run`, needs narrowing.
"""

from auditor.graph.payloads import RunRowPayload
from auditor.graph.refine.models import RefinementCounts
from auditor.graph.refine.service import RunReport, Verdict
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
