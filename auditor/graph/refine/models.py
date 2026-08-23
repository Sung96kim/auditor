"""Frozen records for the refinement tables (spec 5.3, 5.4, 5.5). These cross the store boundary
in both directions, so every JSON column has a model rather than a raw dict."""

import time
import uuid
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from auditor.graph.model import CallForm, EdgeKind


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABORTED = "aborted"
    REJECTED = "rejected"
    SKIPPED = "skipped"  # the gate declined; no runner ever existed


class ClientKind(StrEnum):
    CLAUDE_CODE = "claude-code"
    CODEX = "codex"
    CLI = "cli"


class ProducerKind(StrEnum):
    OBSERVER = "observer"
    AGENT = "agent"
    CLI = "cli"


class RunnerKind(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"
    FAKE = "fake"
    NONE = "none"  # assessment-only and synthetic rows


class TriggerKind(StrEnum):
    SESSION_START = "session_start"
    EDIT = "edit"
    SUSPECT = "suspect"
    MANUAL = "manual"
    TUNE = "tune"
    VERIFY = "verify"
    EVAL = "eval"
    RETIRE = "retire"


class RefinementKind(StrEnum):
    ADD_EDGE = "add_edge"
    RETARGET_EDGE = "retarget_edge"
    CONFIRM_EDGE = "confirm_edge"
    RESOLVE_AMBIGUOUS = "resolve_ambiguous"
    RELABEL_CLUSTER = "relabel_cluster"
    MOVE_NODE = "move_node"
    ANNOTATE_NODE = "annotate_node"
    UNRESOLVABLE = "unresolvable"


class RefinementStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    STALE = "stale"
    REDUNDANT = "redundant"
    REVERTED = "reverted"
    PINNED = "pinned"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class Tier(StrEnum):
    A = "A"
    B = "B"
    C = "C"


#: statuses a build applies. `pinned` is never auto-staled, only marked `drifted` (spec 5.7).
ACTIVE_STATUSES = frozenset({RefinementStatus.ACTIVE, RefinementStatus.PINNED})

#: the only edge kinds a proposal may name (spec 9.2). The overlay's collision index is built from
#: structural edges alone, so a similarity kind would slip past it and collapse a real row.
REFINABLE_EDGE_KINDS = frozenset(
    {
        EdgeKind.CALLS,
        EdgeKind.REFERENCES_TYPE,
        EdgeKind.CALLBACK_ARG,
        EdgeKind.INHERITS,
        EdgeKind.OVERRIDES,
    }
)


class Evidence(BaseModel):
    """One source excerpt behind a proposal. Provenance only: nothing verifies against it."""

    model_config = ConfigDict(frozen=True)

    path: str
    line: int = 0
    excerpt: str = ""


class RefinementTarget(BaseModel):
    """What a refinement points at, in toplevel-relative ids. One model for the eight kinds in
    spec 5.4; the `_REQUIRED_BY_KIND` table below says what each one must fill in, and
    `Refinement` enforces it.

    ``name`` is spec 5.4's `{node_id, name}` shape, and the edge kinds carry it too: it is the only
    thing that lets a build retire the `graph_unresolved` row the refinement answers (spec 5.7).
    ``resolve_ambiguous`` names its node in ``node_id`` and its chosen dst in
    ``RefinementPayload.candidate``, and still needs ``edge_kind`` set from the queue row's
    ``fact_kind``.
    """

    model_config = ConfigDict(frozen=True)

    src: str | None = None
    dst: str | None = None
    edge_kind: EdgeKind | None = None
    from_dst: str | None = None
    to_dst: str | None = None
    members: tuple[str, ...] = ()
    node_id: str | None = None
    name: str | None = None


class RefinementPayload(BaseModel):
    """What a refinement carries beyond its target: a label, an annotation, the candidate it
    chose, the reason code it retired a pair under, and the call form it saw."""

    model_config = ConfigDict(frozen=True)

    label: str | None = None
    annotation: str | None = None
    candidate: str | None = None
    reason_code: str | None = None
    call_form: CallForm | None = None


class ToolCall(BaseModel):
    """One tool the runner used, as the run's trace records it."""

    model_config = ConfigDict(frozen=True)

    tool: str
    ts: float = 0.0
    detail: str = ""


class TriggerDetail(BaseModel):
    """What the trigger carried: the files it named and, for a gate decision, why."""

    model_config = ConfigDict(frozen=True)

    files: tuple[str, ...] = ()
    reason: str = ""


class RunUsage(BaseModel):
    """What one run cost. ``cost_estimated`` marks a price the runner did not report."""

    model_config = ConfigDict(frozen=True)

    cost_usd: float = 0.0
    cost_estimated: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    num_turns: int = 0


class EvalMetrics(BaseModel):
    """One suite stratum's measured accuracy (spec 10.2). ``lower_bound_95`` is what a tier gate
    reads, not the point estimate."""

    model_config = ConfigDict(frozen=True)

    n: int = 0
    correct: int = 0
    precision: float = 0.0
    recall: float = 0.0
    false_add_rate: float = 0.0
    false_removal_rate: float = 0.0
    lower_bound_95: float = 0.0


class Run(BaseModel):
    """One decision the observer or an agent made, model call or not (spec 5.3)."""

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    repo_identity: str
    origin_partition: str = ""
    partition_prefix: str = ""
    client: ClientKind = ClientKind.CLI
    producer: ProducerKind = ProducerKind.CLI
    runner: RunnerKind = RunnerKind.NONE
    trigger_kind: TriggerKind = TriggerKind.MANUAL
    trigger_detail: TriggerDetail = TriggerDetail()
    session_id: str | None = None
    agent_name: str | None = None
    branch: str | None = None
    commit_sha: str | None = None  # `commit` is a reserved word in SQLite
    dirty: bool = False
    model: str | None = None
    prompt: str | None = None
    system_prompt_sha: str | None = None
    tool_trace: tuple[ToolCall, ...] = ()
    usage: RunUsage = RunUsage()
    sdk_session_id: str | None = None
    status: RunStatus = RunStatus.QUEUED
    summary: str | None = None
    error: str | None = None
    started_at: float = Field(default_factory=time.time)
    finished_at: float | None = None


class RunOutcome(BaseModel):
    """A run's terminal state: what it produced, what it cost, and when it stopped (spec 5.3).

    One field per column ``finish_run`` updates, so the UPDATE's set list is derived rather than
    hand-ordered. ``finished_at`` of ``None`` means "stamp it now".
    """

    model_config = ConfigDict(frozen=True)

    status: RunStatus
    summary: str | None = None
    error: str | None = None
    usage: RunUsage = RunUsage()
    tool_trace: tuple[ToolCall, ...] = ()
    sdk_session_id: str | None = None
    finished_at: float | None = None


#: what each kind must name to be applicable at all, `payload.` for the payload half (spec 5.4).
#: The edge kinds carry `name` too: without it a build cannot retire the queue row they answer.
_REQUIRED_BY_KIND: dict[RefinementKind, tuple[str, ...]] = {
    RefinementKind.ADD_EDGE: ("src", "dst", "edge_kind", "name"),
    RefinementKind.RETARGET_EDGE: ("src", "from_dst", "to_dst", "edge_kind", "name"),
    RefinementKind.CONFIRM_EDGE: ("src", "dst", "edge_kind", "name"),
    RefinementKind.RESOLVE_AMBIGUOUS: (
        "node_id",
        "name",
        "edge_kind",
        "payload.candidate",
    ),
    RefinementKind.RELABEL_CLUSTER: ("members", "payload.label"),
    RefinementKind.MOVE_NODE: ("node_id", "members"),
    RefinementKind.ANNOTATE_NODE: ("node_id", "payload.annotation"),
    RefinementKind.UNRESOLVABLE: ("node_id", "name"),
}


class Refinement(BaseModel):
    """One correction to the graph, owned by a run and expiring on its own (spec 5.4)."""

    model_config = ConfigDict(frozen=True)

    refinement_id: int = 0  # assigned by the insert
    run_id: str
    repo_identity: str
    kind: RefinementKind
    target: RefinementTarget = Field(default_factory=RefinementTarget)
    payload: RefinementPayload = Field(default_factory=RefinementPayload)
    reason: str = ""
    evidence: tuple[Evidence, ...] = ()
    confidence: float = 0.0
    tier: Tier = Tier.C
    status: RefinementStatus = RefinementStatus.PENDING
    drifted: bool = False
    noop_builds: int = 0
    supersedes: int | None = None
    attempts: int = 0
    created_at: float = 0.0  # both stamped by the validator below when absent
    status_at: float = 0.0

    @model_validator(mode="before")
    @classmethod
    def _one_timestamp_until_it_moves(cls, data: Any) -> Any:
        """Stamp `created_at` and default `status_at` to it, rather than calling the clock twice.

        Two independent defaults disagreed about one construction in six, and `status_at` is what
        the staleness sweep reads as "has this moved since it was made?".
        """
        if not isinstance(data, dict):
            return data
        created = data.get("created_at") or time.time()
        return {
            **data,
            "created_at": created,
            "status_at": data.get("status_at") or created,
        }

    @model_validator(mode="after")
    def _the_target_matches_the_kind(self) -> "Refinement":
        """Refuse a target no build could apply: an `add_edge` with no destination is worse
        stored than rejected, and nothing downstream would ever report it."""
        missing = [
            field for field in _REQUIRED_BY_KIND[self.kind] if not self._required(field)
        ]
        if missing:
            raise ValueError(f"{self.kind.value} target is missing {missing}")
        edge_kind = self.target.edge_kind
        if edge_kind is not None and edge_kind not in REFINABLE_EDGE_KINDS:
            raise ValueError(
                f"{edge_kind.value} is not an edge kind a proposal may name"
            )
        src, dst = self.edge_pair()
        if src is not None and src == dst:
            raise ValueError(f"{self.kind.value} would point {src} at itself")
        return self

    def _required(self, path: str) -> object:
        """One required field, read from the target unless ``path`` names the payload half."""
        owner, _, name = path.rpartition(".")
        return getattr(self.payload if owner == "payload" else self.target, name)

    def edge_pair(self) -> tuple[str | None, str | None]:
        """The ``(src, dst)`` this refinement would put in the graph, toplevel-relative.

        `resolve_ambiguous` names its node in ``target.node_id`` and its chosen dst in
        ``payload.candidate``; `retarget_edge` means its ``to_dst``. The node kinds mean nothing
        here and answer ``(None, None)``.
        """
        target = self.target
        if self.kind is RefinementKind.RESOLVE_AMBIGUOUS:
            return target.node_id, self.payload.candidate
        if self.kind is RefinementKind.RETARGET_EDGE:
            return target.src, target.to_dst
        if self.kind in (RefinementKind.ADD_EDGE, RefinementKind.CONFIRM_EDGE):
            return target.src, target.dst
        return None, None


class Anchor(BaseModel):
    """One node a refinement is pinned to: its structural hash at proposal time (spec 5.5)."""

    model_config = ConfigDict(frozen=True)

    refinement_id: int = 0  # assigned by the insert
    node_id: str
    path: str
    truth_sha: str
    file_sha: str = ""


class RefinementOutcome(BaseModel):
    """What one build decided about one refinement it looked at. ``status`` of ``None`` means
    the build had no reason to move it."""

    model_config = ConfigDict(frozen=True)

    refinement_id: int
    status: RefinementStatus | None = None
    noop_builds: int = 0
    drifted: bool = False
    applied: bool = False


class TuningStatus(StrEnum):
    """The refinement statuses minus the anchor-driven ones: a tuning row is not pinned to a node,
    so it has nothing to drift against and never goes `stale`, `redundant` or `pinned`."""

    PENDING = "pending"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REVERTED = "reverted"
    REJECTED = "rejected"


class TuningRow(BaseModel):
    """One proposed knob change (spec 5.8). ``value_json`` stays a raw JSON string because a knob
    can be a float, an int or a list of stopwords."""

    model_config = ConfigDict(frozen=True)

    tuning_id: int = 0  # assigned by the insert
    repo_identity: str
    key: str
    value_json: str
    token: str = ""
    run_id: str
    reason: str = ""
    status: TuningStatus = TuningStatus.PENDING
    metrics: EvalMetrics = EvalMetrics()
    created_at: float = Field(default_factory=time.time)


class EvalRow(BaseModel):
    """One eval suite stratum's measured accuracy for a runner and model (spec 5.8, 10.2)."""

    model_config = ConfigDict(frozen=True)

    eval_id: int = 0  # assigned by the insert
    repo_identity: str
    runner: RunnerKind
    model: str
    suite: str
    stratum: str
    metrics: EvalMetrics = EvalMetrics()
    cost_usd: float = 0.0
    num_turns: int = 0
    created_at: float = Field(default_factory=time.time)


class SnapshotPhase(StrEnum):
    """Which side of a build's persist a snapshot is being taken on (spec 8.6 stage 2)."""

    BEFORE = "before"
    AFTER = "after"


#: What a rebuild calls twice inside its lock, so an assessment sees exactly one build's delta.
Snapshot = Callable[[SnapshotPhase], Awaitable[None]]
