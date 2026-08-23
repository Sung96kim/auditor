"""Frozen records for the refinement tables (spec 5.3, 5.4, 5.5). These cross the store boundary
in both directions, so every JSON column has a model rather than a raw dict."""

import time
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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


class Evidence(BaseModel):
    """One source excerpt behind a proposal. Provenance only: nothing verifies against it."""

    model_config = ConfigDict(frozen=True)

    path: str
    line: int = 0
    excerpt: str = ""


class RefinementTarget(BaseModel):
    """What a refinement points at, in toplevel-relative ids. One model for all five shapes in
    spec 5.4; which fields a kind requires is validated by the service, not here.

    ``name`` is spec 5.4's `{node_id, name}` shape, and the service must also set it on the edge
    kinds: it is the only thing that lets a build retire the `graph_unresolved` row the refinement
    answers (spec 5.7). ``resolve_ambiguous`` names its node in ``node_id`` and its chosen dst in
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
    trigger_detail: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    agent_name: str | None = None
    branch: str | None = None
    commit_sha: str | None = None  # `commit` is a reserved word in SQLite
    dirty: bool = False
    model: str | None = None
    prompt: str | None = None
    system_prompt_sha: str | None = None
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    cost_usd: float = 0.0
    cost_estimated: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    num_turns: int = 0
    sdk_session_id: str | None = None
    status: RunStatus = RunStatus.QUEUED
    summary: str | None = None
    error: str | None = None
    started_at: float = Field(default_factory=time.time)
    finished_at: float | None = None


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
    created_at: float = Field(default_factory=time.time)
    status_at: float = Field(default_factory=time.time)


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
