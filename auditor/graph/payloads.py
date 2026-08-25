"""The wire models the graph surfaces emit: what a build landed, and what ``GraphQuery`` returns.

They live beside the graph rather than under ``auditor/cli`` so the CLI renderers and the MCP
tools read the same shape and neither imports the other.
"""

import re
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import ConfigDict, Field

from auditor.graph.model import (
    LOG_ROW_LIMIT,
    QUEUE_ID_CAP,
    EdgeKind,
    GraphCluster,
    UnresolvedRow,
    enum_values,
)
from auditor.graph.refine.models import (
    Anchor,
    ClientKind,
    ProducerKind,
    Refinement,
    RefinementKind,
    RefinementStatus,
    Run,
    RunnerKind,
    RunStatus,
    Tier,
    TriggerKind,
)
from auditor.payload import WirePayload, WireRows


class RelatedRow(WirePayload):
    """One semantic neighbour of a symbol, with the edge weight that found it."""

    id: str
    kind: str
    weight: float
    rank: float


class RelatedReport(WireRows[RelatedRow]):
    """``graph related``."""


class NeighborRow(WirePayload):
    """One structural neighbour, with the relation and the hop count that reached it."""

    id: str
    kind: str
    edge: str
    direction: Literal["in", "out"]
    hops: int


class NeighborsReport(WireRows[NeighborRow]):
    """``graph neighbors``."""


class SearchRow(WirePayload):
    """One symbol whose id contains the search term."""

    id: str
    kind: str
    rank: float


class SearchReport(WireRows[SearchRow]):
    """``graph search``."""


class ClustersReport(WireRows[GraphCluster]):
    """``graph clusters``, over the cluster record the build already writes."""


class ClusterMember(WirePayload):
    """One node inside a concept cluster."""

    id: str
    name: str
    module: str
    rank: float
    refined: int = 0
    annotation: str | None = None


class CappedConcept(WirePayload):
    """A concept with its member list truncated and the true total alongside."""

    cluster_id: int
    label: str
    member_count: int
    members: tuple[ClusterMember, ...] = ()
    shown: int = 0


class ConceptPayload(WirePayload):
    """``graph concept``: the cluster a term resolved to, and every member it holds."""

    cluster_id: int
    label: str
    members: tuple[ClusterMember, ...] = ()

    def capped(self, limit: int) -> CappedConcept:
        """The first ``limit`` members with the true total alongside, for a bounded response.

        A negative limit is floored at zero: slicing from the end would answer a nonsense
        request with a plausible-looking page of every member but the last.
        """
        members = self.members[: max(0, limit)]
        return CappedConcept(
            cluster_id=self.cluster_id,
            label=self.label,
            member_count=len(self.members),
            members=members,
            shown=len(members),
        )


class UsageGroup(WirePayload):
    """One edge kind's usage count and a rank-ordered sample of the symbols on the other end."""

    count: int
    sample: tuple[str, ...] = ()


class UsagesPayload(WirePayload):
    """``graph usages``: structural edges grouped by kind, split by direction."""

    symbol: str
    resolved: str
    kind: str | None = None
    ambiguous: tuple[str, ...] = ()
    used_by: dict[str, UsageGroup] = Field(default_factory=dict)
    depends_on: dict[str, UsageGroup] = Field(default_factory=dict)
    total_in: int = 0
    total_out: int = 0


class QueueRowPayload(UnresolvedRow):
    """One queue row on the wire: the two id lists capped, their true totals alongside.

    ``extra="forbid"``: the queue is read with ``SELECT *``, so a column the table gains has to be
    declared here or fail loudly, never be dropped on the way to the CLI and the MCP tool.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    definers_count: int = 0
    candidates_count: int = 0

    @property
    def display_name(self) -> str:
        """The called name as written: ``job.handle`` for an attribute call, so two rows on the
        same method under different receivers are told apart."""
        return f"{self.receiver_root}.{self.name}" if self.receiver_root else self.name

    @classmethod
    def of(cls, row: Mapping[str, Any]) -> "QueueRowPayload":
        """Cap a stored queue row's two id lists the way ``graph_overview`` caps its hub lists:
        a node can have dozens of definers."""
        return cls.model_validate(
            {
                **row,
                "definers": tuple(row["definers"])[:QUEUE_ID_CAP],
                "candidates": tuple(row["candidates"])[:QUEUE_ID_CAP],
                "definers_count": len(row["definers"]),
                "candidates_count": len(row["candidates"]),
            }
        )


class QueueReport(WireRows[QueueRowPayload]):
    """``graph unresolved``."""


class GraphBuildReport(WirePayload):
    """What one build landed, as ``graph build`` and the MCP tool report it."""

    nodes: int
    edges: int
    clusters: int
    unresolved: int
    findings: int
    refined: int
    expired: int


class RefinementRowPayload(WirePayload):
    """One refinement as every surface shows it: the target flattened, the anchors by node id."""

    refinement_id: int
    run_id: str
    kind: RefinementKind
    tier: Tier
    status: RefinementStatus
    src: str | None = None
    dst: str | None = None
    edge_kind: EdgeKind | None = None
    name: str | None = None
    node_id: str | None = None
    reason: str = ""
    confidence: float = 0.0
    drifted: bool = False
    noop_builds: int = 0
    supersedes: int | None = None
    anchors: tuple[str, ...] = ()
    created_at: float = 0.0
    status_at: float = 0.0

    @property
    def summary(self) -> str:
        """What the refinement points at, in one cell: an edge as ``src -> dst``, a node as its id."""
        if self.src and self.dst:
            return f"{self.src} -> {self.dst}"
        return self.node_id or ", ".join(self.anchors) or self.name or ""

    @classmethod
    def of(
        cls, refinement: Refinement, anchors: Sequence[Anchor] = ()
    ) -> "RefinementRowPayload":
        target = refinement.target
        src, dst = refinement.edge_pair()
        return cls(
            refinement_id=refinement.refinement_id,
            run_id=refinement.run_id,
            kind=refinement.kind,
            tier=refinement.tier,
            status=refinement.status,
            src=src,
            dst=dst,
            edge_kind=target.edge_kind,
            name=target.name,
            node_id=target.node_id,
            reason=refinement.reason,
            confidence=refinement.confidence,
            drifted=refinement.drifted,
            noop_builds=refinement.noop_builds,
            supersedes=refinement.supersedes,
            anchors=tuple(a.node_id for a in anchors),
            created_at=refinement.created_at,
            status_at=refinement.status_at,
        )


class RefinementsReport(WirePayload):
    """``auditr graph refinements list`` and ``graph_refinements``. ``filtered`` is why an empty
    list is empty."""

    rows: tuple[RefinementRowPayload, ...] = ()
    filtered: bool = False


class RunRowPayload(WirePayload):
    """One run as the log shows it: who made it, against which checkout, and what it cost."""

    run_id: str
    status: RunStatus
    producer: ProducerKind
    client: ClientKind
    runner: RunnerKind
    trigger_kind: TriggerKind
    model: str | None = None
    summary: str | None = None
    error: str | None = None
    session_id: str | None = None
    agent_name: str | None = None
    branch: str | None = None
    commit_sha: str | None = None
    cost_usd: float = 0.0
    num_turns: int = 0
    refinements: int = 0
    started_at: float = 0.0
    finished_at: float | None = None

    @classmethod
    def of(cls, run: Run, *, refinements: int = 0) -> "RunRowPayload":
        return cls(
            run_id=run.run_id,
            status=run.status,
            producer=run.producer,
            client=run.client,
            runner=run.runner,
            trigger_kind=run.trigger_kind,
            model=run.model,
            summary=run.summary,
            error=run.error,
            session_id=run.session_id,
            agent_name=run.agent_name,
            branch=run.branch,
            commit_sha=run.commit_sha,
            cost_usd=run.usage.cost_usd,
            num_turns=run.usage.num_turns,
            refinements=refinements,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )


class LogView(StrEnum):
    """Which half of the provenance the log is showing."""

    RUNS = "runs"
    REFINEMENTS = "refinements"


#: `90s`, `45m`, `2h`, `7d` — one number and one unit, which is every shape a log window needs
_DURATION = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_since(raw: str, *, now: float | None = None) -> float:
    """A time-window value as an epoch cutoff: a duration back from now, or an ISO instant.

    A git ref would be the wrong answer here: `scan --since` scopes files, and a log is scoped by
    time. The message names no flag, because the MCP tool's parameter is `since` and the CLI's is
    `--since`.
    """
    match = _DURATION.match(raw.strip())
    if match:
        amount, unit = match.groups()
        return (time.time() if now is None else now) - int(amount) * _UNIT_SECONDS[unit]
    try:
        return datetime.fromisoformat(raw.strip()).timestamp()
    except ValueError as exc:
        raise ValueError(
            f"since {raw!r} is neither a duration (90s, 45m, 2h, 7d) nor an ISO date "
            "(2026-08-20, 2026-08-20T14:00:00)"
        ) from exc


class LogFilter(WirePayload):
    """The log's validated filter set, so a filter cannot mean two things on two surfaces."""

    view: LogView = LogView.RUNS
    statuses: tuple[str, ...] = ()
    since: float | None = None
    skipped: bool = False
    limit: int = LOG_ROW_LIMIT

    @classmethod
    def of(
        cls,
        *,
        view: str,
        status: Sequence[str] | None,
        since: str | None,
        skipped: bool,
        limit: int,
    ) -> "LogFilter":
        """Validate every value against the enum the chosen view owns."""
        if view not in {v.value for v in LogView}:
            raise ValueError(
                f"unknown view: {view}. Valid: {', '.join(v.value for v in LogView)}"
            )
        chosen = LogView(view)
        enum = RunStatus if chosen is LogView.RUNS else RefinementStatus
        return cls(
            view=chosen,
            statuses=tuple(enum_values(status, enum, "status") or ()),
            since=parse_since(since) if since else None,
            skipped=skipped,
            limit=max(1, limit),
        )

    @property
    def run_statuses(self) -> list[RunStatus] | None:
        """The run statuses the caller asked for, or ``None`` for every one."""
        if self.view is not LogView.RUNS or not self.statuses:
            return None
        return [RunStatus(s) for s in self.statuses]

    @property
    def excluded_run_statuses(self) -> tuple[RunStatus, ...]:
        """Skipped runs are assessment-only and out of the default view (spec 12.2). Expressed as
        an exclusion rather than as an every-other-status list, so a new `RunStatus` needs no edit
        here."""
        if self.view is not LogView.RUNS or self.skipped or self.statuses:
            return ()
        return (RunStatus.SKIPPED,)

    @property
    def refinement_statuses(self) -> list[RefinementStatus] | None:
        if self.view is not LogView.REFINEMENTS or not self.statuses:
            return None
        return [RefinementStatus(s) for s in self.statuses]

    @property
    def filtered(self) -> bool:
        """Whether the caller narrowed the view, which is why an empty page is empty."""
        return bool(self.statuses) or self.since is not None


class LogReport(WirePayload):
    """One page of the provenance log: one view, and what produced it."""

    view: LogView = LogView.RUNS
    runs: tuple[RunRowPayload, ...] = ()
    refinements: tuple[RefinementRowPayload, ...] = ()
    filtered: bool = False

    @classmethod
    def of(
        cls,
        spec: LogFilter,
        *,
        runs: Sequence[RunRowPayload] = (),
        refinements: Sequence[RefinementRowPayload] = (),
    ) -> "LogReport":
        """One page of the provenance log, from rows a store already fetched.

        Pure, like every other ``of()`` in this module: `LogQuery` does the reading, because this
        module is imported by `cli/render.py` on every command and must stay free of the database.
        """
        return cls(
            view=spec.view,
            runs=tuple(runs),
            refinements=tuple(refinements),
            filtered=spec.filtered,
        )


class PruneReport(WirePayload):
    """``auditr graph refinements prune``: how many assessment-only runs were dropped."""

    removed: int
