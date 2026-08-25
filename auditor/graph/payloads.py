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

from pydantic import ConfigDict, Field, computed_field

from auditor.graph.model import (
    LOG_ROW_LIMIT,
    QUEUE_ID_CAP,
    EdgeKind,
    GraphCluster,
    UnresolvedRow,
    enum_value,
    enum_values,
    row_limit,
)
from auditor.graph.refine.models import (
    Anchor,
    ClientKind,
    ProducerKind,
    Refinement,
    RefinementCounts,
    RefinementKind,
    RefinementPayload,
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
    """One refinement as every surface shows it: the target flattened, the proposal's own values
    beside it, the anchors by node id.

    ``payload`` is carried whole rather than flattened further: a ``pending`` row is one a human
    has to judge, and the label, annotation, candidate or reason code is what there is to judge.
    """

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
    from_dst: str | None = None
    members: tuple[str, ...] = ()
    payload: RefinementPayload = RefinementPayload()
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
        """What the refinement points at, in one cell, for each shape spec 5.4 allows: an edge as
        ``src -> dst``, a retarget as ``src: from -> to``, a moved node, a cluster's members."""
        if self.src and self.dst:
            moved = f"{self.from_dst} -> " if self.from_dst else ""
            return (
                f"{self.src}: {moved}{self.dst}"
                if moved
                else f"{self.src} -> {self.dst}"
            )
        members = ", ".join(self.members)
        if self.node_id:
            return f"{self.node_id} -> {members}" if members else self.node_id
        return members or ", ".join(self.anchors) or self.name or ""

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
            from_dst=target.from_dst,
            members=target.members,
            payload=refinement.payload,
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
    """``auditr graph refinements list`` and ``graph_refinements``: one page, newest first.

    ``filtered`` is why an empty list is empty, and ``refinement_count`` is how many rows the same
    filters match, so a page at the cap cannot be read as the whole list.
    """

    rows: tuple[RefinementRowPayload, ...] = ()
    filtered: bool = False
    refinement_count: int = 0
    truncated: bool = False

    @classmethod
    def of(
        cls,
        rows: Sequence[RefinementRowPayload],
        *,
        filtered: bool,
        total: int,
    ) -> "RefinementsReport":
        """One page and the total beside it, with ``truncated`` derived here rather than at each
        call site, so the two numbers cannot disagree."""
        return cls(
            rows=tuple(rows),
            filtered=filtered,
            refinement_count=total,
            truncated=total > len(rows),
        )


class RunRowPayload(WirePayload):
    """One run as the log shows it: who made it, against which checkout, and what it cost.

    ``cost_estimated`` travels with ``cost_usd`` because a runner that reports no price has one
    modelled for it, and a log that showed only the number would present it as measured.
    """

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
    cost_estimated: bool = False
    num_turns: int = 0
    refinements: RefinementCounts = RefinementCounts()
    started_at: float = 0.0
    finished_at: float | None = None

    @classmethod
    def of(
        cls, run: Run, *, refinements: RefinementCounts | None = None
    ) -> "RunRowPayload":
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
            cost_estimated=run.usage.cost_estimated,
            num_turns=run.usage.num_turns,
            refinements=refinements or RefinementCounts(),
            started_at=run.started_at,
            finished_at=run.finished_at,
        )


class LogView(StrEnum):
    """Which half of the provenance the log is showing."""

    RUNS = "runs"
    REFINEMENTS = "refinements"


class LogNarrowing(StrEnum):
    """A filter the caller set, named so an empty page can say which one emptied it.

    Only the caller's own filters are here. The default runs view hides the assessment-only rows
    on its own, and that hiding is reported by ``hidden_statuses`` instead.
    """

    STATUS = "status"
    SINCE = "since"


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
        view: LogView | str,
        status: Sequence[str] | None,
        since: str | None,
        skipped: bool,
        limit: int,
    ) -> "LogFilter":
        """Validate every value against the enum the chosen view owns.

        A caller holding the view already passes the enum; only an untrusted string is re-parsed.
        ``since`` is validated whenever it is given at all, because an empty string is a caller
        that thinks it set a window rather than one that set none.
        """
        chosen = (
            view
            if isinstance(view, LogView)
            else LogView(enum_value(view, LogView, "view"))
        )
        if skipped and chosen is not LogView.RUNS:
            raise ValueError(
                "skipped applies to the runs view only. Valid in the refinements view: "
                "status, since, limit"
            )
        enum = RunStatus if chosen is LogView.RUNS else RefinementStatus
        return cls(
            view=chosen,
            statuses=tuple(enum_values(status, enum, "status") or ()),
            since=parse_since(since) if since is not None else None,
            skipped=skipped,
            limit=row_limit(limit),
        )

    @property
    def run_statuses(self) -> list[RunStatus] | None:
        """The run statuses the caller asked for, or ``None`` for every one."""
        if self.view is not LogView.RUNS or not self.statuses:
            return None
        return [RunStatus(s) for s in self.statuses]

    @property
    def excluded_run_statuses(self) -> tuple[RunStatus, ...]:
        """Skipped runs are out of the default view (spec 12.2): the assessment's own decisions,
        plus any run the registry evicted or the retention sweep found stranded. Expressed as an
        exclusion rather than as an every-other-status list, so a new `RunStatus` needs no edit
        here. This is the view's own hiding, never something the caller asked for."""
        if self.view is not LogView.RUNS or self.skipped or self.statuses:
            return ()
        return (RunStatus.SKIPPED,)

    @property
    def refinement_statuses(self) -> list[RefinementStatus] | None:
        if self.view is not LogView.REFINEMENTS or not self.statuses:
            return None
        return [RefinementStatus(s) for s in self.statuses]

    @property
    def narrowed_by(self) -> tuple[LogNarrowing, ...]:
        """The filters the caller set, in the order the log documents them.

        The default runs view's own hiding is deliberately not here: a reader who narrowed
        nothing must not be told a narrowing emptied the page.
        """
        return tuple(
            name
            for name, set_by_caller in (
                (LogNarrowing.STATUS, bool(self.statuses)),
                (LogNarrowing.SINCE, self.since is not None),
            )
            if set_by_caller
        )


class LogReport(WirePayload):
    """One page of the provenance log: one view, what produced it, and what it did not show.

    An empty page has three causes and the fields separate them: ``narrowed_by`` is what the
    caller filtered, ``hidden_statuses`` and ``hidden_count`` are what the default runs view hid
    on its own, and neither set means nothing is recorded.
    """

    view: LogView = LogView.RUNS
    runs: tuple[RunRowPayload, ...] = ()
    refinements: tuple[RefinementRowPayload, ...] = ()
    narrowed_by: tuple[LogNarrowing, ...] = ()
    hidden_statuses: tuple[RunStatus, ...] = ()
    hidden_count: int = 0
    run_count: int = 0
    refinement_count: int = 0
    truncated: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def filtered(self) -> bool:
        """Whether the caller narrowed this page themselves, which the default view never does."""
        return bool(self.narrowed_by)

    @property
    def rows(self) -> tuple[RunRowPayload, ...] | tuple[RefinementRowPayload, ...]:
        """The rows of whichever view this page carries, so a reader needs no view branch."""
        return self.runs if self.view is LogView.RUNS else self.refinements

    @property
    def total(self) -> int:
        """How many rows the same filters match in the view this page carries."""
        return self.run_count if self.view is LogView.RUNS else self.refinement_count

    @classmethod
    def of(
        cls,
        spec: LogFilter,
        *,
        runs: Sequence[RunRowPayload] = (),
        refinements: Sequence[RefinementRowPayload] = (),
        total: int = 0,
        hidden: int = 0,
    ) -> "LogReport":
        """One page of the provenance log, from rows a store already fetched.

        Pure, like every other ``of()`` in this module: `LogQuery` does the reading, because this
        module is imported by `cli/render.py` on every command and must stay free of the database.
        ``total`` counts the chosen view under the same filters and ``hidden`` the rows the view
        excluded on its own, both before the limit.
        """
        chosen = tuple(runs) if spec.view is LogView.RUNS else tuple(refinements)
        return cls(
            view=spec.view,
            runs=tuple(runs),
            refinements=tuple(refinements),
            narrowed_by=spec.narrowed_by,
            hidden_statuses=spec.excluded_run_statuses,
            hidden_count=hidden,
            run_count=total if spec.view is LogView.RUNS else 0,
            refinement_count=0 if spec.view is LogView.RUNS else total,
            truncated=total > len(chosen),
        )
