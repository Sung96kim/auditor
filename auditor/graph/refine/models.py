"""Frozen records for the refinement tables (spec 5.3, 5.4, 5.5). These cross the store boundary
in both directions, so every JSON column has a model rather than a raw dict."""

import re
import time
import uuid
from collections.abc import Awaitable, Callable, Collection, Mapping
from enum import StrEnum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    model_validator,
)

from auditor.graph.model import CallForm, EdgeKind
from auditor.graph.refine.namespace import file_of, to_toplevel


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

#: the proposal kinds that put an edge in the graph: the only ones a verifier can check and the
#: only ones that can collide with prior work at commit (spec 9.1, 9.2)
EDGE_PROPOSAL_KINDS = frozenset(
    {
        RefinementKind.ADD_EDGE,
        RefinementKind.RETARGET_EDGE,
        RefinementKind.RESOLVE_AMBIGUOUS,
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


class Stratum(StrEnum):
    """The add suite's strata (spec 10.2): how far a proposal's destination is from its source.

    The tier B gate reads the one matching the proposal's own shape, because a repo's strata do
    not measure alike; here they run 47 / 23 / 30 per cent of the add suite.
    """

    SAME_MODULE = "same-module"
    DIRECT_IMPORT = "direct-import"
    NEITHER = "neither"

    @classmethod
    def of(cls, src: str, dst: str, *, imports: Collection[str]) -> "Stratum":
        """The stratum one proposed edge falls in; ``imports`` is the repo module ids the source's
        own module imports."""
        src_module, dst_module = file_of(src), file_of(dst)
        if src_module == dst_module:
            return cls.SAME_MODULE
        return cls.DIRECT_IMPORT if dst_module in imports else cls.NEITHER


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

    @classmethod
    def begin(
        cls,
        *,
        partition: tuple[str, str],
        origin: str,
        scope: str,
        checkout: tuple[str | None, str | None],
        client: ClientKind,
        producer: ProducerKind,
        runner: RunnerKind,
        trigger: TriggerKind,
        model: str | None = None,
        session_id: str | None = None,
        agent_name: str | None = None,
    ) -> "Run":
        """A queued run, from what its caller has to derive and what it was told (Invariant 2).

        The partition pair, the scope and the branch/HEAD pair each fan out into more than one
        stored column, which is what made this a thirteen-argument construction at the caller.
        """
        identity, prefix = partition
        branch, commit_sha = checkout
        return cls(
            repo_identity=identity,
            origin_partition=origin,
            partition_prefix=prefix,
            trigger_detail=TriggerDetail(files=(scope,) if scope else ()),
            branch=branch,
            commit_sha=commit_sha,
            client=client,
            producer=producer,
            runner=runner,
            trigger_kind=trigger,
            model=model,
            session_id=session_id,
            agent_name=agent_name,
        )


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


#: a cluster label a proposal may not choose: it is the clusterer's own fallback (spec 9.2)
_FALLBACK_LABEL = re.compile(r"^cluster-\d+$")
LABEL_LENGTH = (2, 40)
ANNOTATION_MAX = 280

#: validation context flag `_refinement_from_row` passes: a row written before a text rule was
#: tightened still reads back, and every other construction is judged by the current rules
STORED_ROW = "stored_row"
#: validation context flag for a read that is a rejection being recorded: spec 9.2 stores every
#: rejection, and a row that exists to carry a complaint needs no target a build could apply
REJECTED_ROW = "rejected_row"


def _recording_a_rejection(model: BaseModel, info: ValidationInfo) -> bool:
    """Whether this read is a stored rejection, either being recorded or read back again."""
    context = info.context or {}
    if not context.get(STORED_ROW):
        return False
    return bool(context.get(REJECTED_ROW)) or (
        getattr(model, "status", None) is RefinementStatus.REJECTED
    )


def _without(raw: Mapping[str, Any], exc: ValidationError) -> dict[str, Any]:
    """``raw`` with every value the validator could not read dropped, so the rest still reaches
    the stored rejection: an unreadable ``edge_kind`` costs its own field, not the whole target."""
    data = dict(raw)
    for error in exc.errors():
        data = _dropped(data, tuple(error["loc"]))
    return data


def _dropped(data: Any, loc: tuple[Any, ...]) -> Any:
    """``data`` without the value at ``loc``; a location inside a sequence drops the sequence,
    which is how one unreadable evidence item costs the evidence and nothing else."""
    if not loc or not isinstance(data, Mapping):
        return data
    key, *rest = loc
    if key not in data:
        return data
    inner = data[key]
    if rest and isinstance(inner, Mapping):
        return {**data, key: _dropped(inner, tuple(rest))}
    return {k: v for k, v in data.items() if k != key}


class ProposedEdge(BaseModel):
    """The edge a proposal puts in the graph, with the queue name it answers.

    `GraphEdge` is the stored row and carries a weight and a provenance no proposal sets, so a
    proposal's edge is its own record.
    """

    model_config = ConfigDict(frozen=True)

    src: str
    dst: str
    kind: EdgeKind
    name: str


class Proposal(BaseModel):
    """One correction a caller offers, before the service judges it (spec 9.2).

    `Refinement` is the stored form of exactly this shape, so the per-kind rules live here and a
    target no build could ever apply is refused before a run row is written.
    """

    model_config = ConfigDict(frozen=True)

    kind: RefinementKind
    target: RefinementTarget = Field(default_factory=RefinementTarget)
    payload: RefinementPayload = Field(default_factory=RefinementPayload)
    reason: str = ""
    evidence: tuple[Evidence, ...] = ()
    confidence: float = 0.0

    @model_validator(mode="after")
    def _the_target_matches_the_kind(self, info: ValidationInfo) -> "Proposal":
        """Refuse a target no build could apply: an `add_edge` with no destination is worse
        stored than rejected, and nothing downstream would ever report it.

        A rejection is the exception spec 9.2 asks for: its row exists to carry the complaint, so
        it is stored and read back whatever its target says.
        """
        if _recording_a_rejection(self, info):
            return self
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

    @model_validator(mode="after")
    def _the_values_a_reader_sees_are_usable(self, info: ValidationInfo) -> "Proposal":
        """A reason, a label that names the cluster, an annotation that fits on a card, a
        confidence on the scale it is read on (spec 9.2).

        Whitespace is not text a reader can use, so the lengths are measured on the stripped value.
        A row read back under `STORED_ROW` keeps whatever it was written with.
        """
        if (info.context or {}).get(STORED_ROW):
            return self
        if not self.reason.strip():
            raise ValueError(f"{self.kind.value} needs a reason")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence is a 0 to 1 scale")
        label = self.payload.label
        low, high = LABEL_LENGTH
        if label is not None and (
            not low <= len(label.strip()) <= high
            or _FALLBACK_LABEL.match(label.strip())
        ):
            raise ValueError(
                f"label must be {low} to {high} characters and not the clusterer's own cluster-N"
            )
        annotation = self.payload.annotation
        if annotation is not None and len(annotation.strip()) > ANNOTATION_MAX:
            raise ValueError(f"annotation must be at most {ANNOTATION_MAX} characters")
        return self

    def _required(self, path: str) -> object:
        """One required field, read from the target unless ``path`` names the payload half."""
        owner, _, name = path.rpartition(".")
        return getattr(self.payload if owner == "payload" else self.target, name)

    def edge(self) -> ProposedEdge | None:
        """The edge this proposal would put in the graph, toplevel-relative.

        ``None`` for the node and cluster kinds, and for a stored row read back without the fields
        its kind needs; `resolve_ambiguous` keeps its dst in ``payload.candidate``.
        """
        target = self.target
        if self.kind is RefinementKind.RESOLVE_AMBIGUOUS:
            src, dst = target.node_id, self.payload.candidate
        elif self.kind is RefinementKind.RETARGET_EDGE:
            src, dst = target.src, target.to_dst
        elif self.kind in (RefinementKind.ADD_EDGE, RefinementKind.CONFIRM_EDGE):
            src, dst = target.src, target.dst
        else:
            return None
        if src is None or dst is None or target.edge_kind is None or not target.name:
            return None
        return ProposedEdge(src=src, dst=dst, kind=target.edge_kind, name=target.name)

    def edge_pair(self) -> tuple[str | None, str | None]:
        """The ``(src, dst)`` of :meth:`edge`, or ``(None, None)`` when it names no edge."""
        edge = self.edge()
        return (edge.src, edge.dst) if edge is not None else (None, None)

    @classmethod
    def read(cls, raw: "Proposal | Mapping[str, Any]") -> tuple["Proposal", str]:
        """One proposal, and the validator's complaint about it when it is not a legal one.

        Spec 9.2 stores every rejection, so an illegal payload is re-read with the values the
        validator could not read dropped: a target no kind could fill, an unreadable enum and a
        malformed evidence item all become one stored row carrying the complaint. ``kind`` is the
        exception, because it chooses the shape and there is no row to store without it.
        """
        if isinstance(raw, Proposal):
            return raw, ""
        try:
            return cls.model_validate(raw), ""
        except ValidationError as exc:
            lenient = cls.model_validate(
                _without(raw, exc), context={STORED_ROW: True, REJECTED_ROW: True}
            )
            return lenient, str(exc.errors()[0]["msg"])

    def rebased(self, prefix: str) -> "Proposal":
        """This proposal with every node id in the toplevel-relative form identity rows store.

        A caller names ids the way its own partition sees them, because that is what the queue and
        the graph show it (spec 5.2).
        """
        if not prefix:
            return self
        target = self.target
        moved = {
            field: to_toplevel(value, prefix)
            for field, value in (
                ("src", target.src),
                ("dst", target.dst),
                ("from_dst", target.from_dst),
                ("to_dst", target.to_dst),
                ("node_id", target.node_id),
            )
            if value is not None
        }
        candidate = self.payload.candidate
        return self.model_copy(
            update={
                "target": target.model_copy(
                    update={
                        **moved,
                        "members": tuple(
                            to_toplevel(m, prefix) for m in target.members
                        ),
                    }
                ),
                "payload": self.payload.model_copy(
                    update={"candidate": to_toplevel(candidate, prefix)}
                )
                if candidate
                else self.payload,
            }
        )

    def anchored_ids(self) -> tuple[str, ...]:
        """Every node id this proposal is pinned to (spec 5.5), each one once.

        Its endpoints, its target node, and the members a cluster kind moves: the cluster kinds
        depend on every member, so an anchor per member is what "the nodes it depends on" means.
        """
        src, dst = self.edge_pair()
        ids = (src, dst, self.target.node_id, *self.target.members)
        return tuple(dict.fromkeys(i for i in ids if i))


class Refinement(Proposal):
    """One correction to the graph, owned by a run and expiring on its own (spec 5.4)."""

    refinement_id: int = 0  # assigned by the insert
    run_id: str
    repo_identity: str
    tier: Tier = Tier.C
    status: RefinementStatus = RefinementStatus.PENDING
    drifted: bool = False
    noop_builds: int = 0
    supersedes: int | None = None
    attempts: int = 0
    created_at: float = 0.0  # both stamped by the validator below when absent
    status_at: float = 0.0

    @classmethod
    def of(
        cls,
        proposal: Proposal,
        *,
        run_id: str,
        repo_identity: str,
        tier: Tier,
        status: RefinementStatus,
        supersedes: int | None = None,
    ) -> "Refinement":
        """The stored form of one accepted proposal (spec 9.1's commit step).

        Only the proposal half is copied, so a stored refinement can be re-confirmed into a fresh
        row of its own run with ``supersedes`` set (spec 5.7).
        """
        return cls(
            **proposal.model_dump(include=set(Proposal.model_fields)),
            run_id=run_id,
            repo_identity=repo_identity,
            tier=tier,
            status=status,
            supersedes=supersedes,
        )

    @classmethod
    def rejected(
        cls,
        proposal: Proposal,
        *,
        run_id: str,
        repo_identity: str,
        detail: str,
        status: RefinementStatus = RefinementStatus.REJECTED,
    ) -> "Refinement":
        """The stored form of one refusal, with the reason a reader sees carrying it (spec 9.2).

        Read under `STORED_ROW` because an illegal payload is exactly the thing that has to be
        recordable; ``status`` is `redundant` when the resolver already produces the edge.
        """
        annotated = proposal.model_copy(
            update={"reason": f"{proposal.reason} [rejected: {detail}]".strip()}
        )
        return cls.model_validate(
            {
                **annotated.model_dump(include=set(Proposal.model_fields)),
                "run_id": run_id,
                "repo_identity": repo_identity,
                "tier": Tier.C,
                "status": status,
            },
            context={STORED_ROW: True},
        )

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


class Anchor(BaseModel):
    """One node a refinement is pinned to: its structural hash at proposal time (spec 5.5)."""

    model_config = ConfigDict(frozen=True)

    refinement_id: int = 0  # assigned by the insert
    node_id: str
    path: str
    truth_sha: str
    file_sha: str = ""

    def rebased(self, prefix: str) -> "Anchor":
        """This anchor in the toplevel-relative form identity rows store."""
        if not prefix:
            return self
        return self.model_copy(
            update={
                "node_id": to_toplevel(self.node_id, prefix),
                "path": to_toplevel(self.path, prefix),
            }
        )


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
