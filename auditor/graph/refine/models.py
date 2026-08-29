"""Frozen records for the refinement tables (spec 5.3, 5.4, 5.5). These cross the store boundary
in both directions, so every JSON column has a model rather than a raw dict."""

import math
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
    computed_field,
    model_validator,
)

from auditor.graph.model import CallForm, EdgeKind
from auditor.graph.refine.namespace import file_of, to_toplevel
from auditor.payload import WirePayload


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


class RunnerChoiceCode(StrEnum):
    """What came of asking for a runner: one runner, or one reason there is none."""

    CLAUDE = "claude"
    PAUSED_AUTH = "paused:auth"
    UNAVAILABLE_SDK = "unavailable:sdk"
    UNAVAILABLE_CODEX = "unavailable:codex"


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


class VerifyStatus(StrEnum):
    """Why a proposal passed or failed the fact check."""

    OK = "ok"  # the facts support an edge of this shape, not that this dst is the only one
    UNVERIFIED = (
        "unverified"  # a kind spec 9.2 gives no verifier; accepted, tiered on shape
    )
    STALE_FILE = "stale_file"
    NO_SUCH_PATH = "no_such_path"
    NOT_LOADED = "not_loaded"
    NO_SRC_NODE = "no_src_node"
    NO_FACT = "no_fact"
    EXTERNALLY_BOUND = "externally_bound"
    NOT_A_DEFINER = "not_a_definer"
    BAD_NODE_KIND = "bad_node_kind"


class ProposalOutcome(StrEnum):
    """What `propose` did with one proposal."""

    STAGED = "staged"
    REJECTED = "rejected"


class RefusalKind(StrEnum):
    """Why a proposal was never judged against the facts at all (spec 9.2's validation rules)."""

    INVALID = "invalid"  # `Proposal`'s own validators refused it; the message is theirs
    OVER_CAP = "over_cap"
    OUT_OF_SCOPE = "out_of_scope"
    ALREADY_STAGED = "already_staged"
    INTRA_BATCH = "intra_batch"
    OUT_OF_PARTITION = "out_of_partition"


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


class NodePair(BaseModel):
    """One ``(node_id, name)`` the queue holds and a run can target (spec 5.6)."""

    model_config = ConfigDict(frozen=True)

    node_id: str
    name: str


class AssessmentDecision(StrEnum):
    RUN = "run"
    SKIP = "skip"


class Assessment(BaseModel):
    """Why one edit batch did or did not earn a refinement run (spec 8.6).

    ``files`` is attribution only: ``facts_changed_nodes`` can name nodes outside them when the
    tree moved. ``deferred_pairs`` is a count because the pairs themselves stay in the queue.
    """

    model_config = ConfigDict(frozen=True)

    files: tuple[str, ...] = ()
    added_nodes: tuple[str, ...] = ()
    removed_nodes: tuple[str, ...] = ()
    facts_changed_nodes: tuple[str, ...] = ()
    new_pairs: tuple[NodePair, ...] = ()
    resolved_pairs: tuple[NodePair, ...] = ()
    stale_refinements: tuple[int, ...] = ()
    affected_flow: tuple[str, ...] = ()
    deferred_pairs: int = 0
    decision: AssessmentDecision = AssessmentDecision.SKIP
    reason: str = ""

    @property
    def decided_to_run(self) -> bool:
        """Whether the gate let this batch through, so no caller compares enum members by hand."""
        return self.decision is AssessmentDecision.RUN


class TriggerDetail(BaseModel):
    """What the trigger carried: the files it named and, for a gate decision, why.

    For an edit batch it also carries the spec 8.6 assessment that decided it.
    """

    model_config = ConfigDict(frozen=True)

    files: tuple[str, ...] = ()
    reason: str = ""
    assessment: Assessment | None = None


class RunUsage(BaseModel):
    """What one run cost. ``cost_estimated`` marks a price the runner did not report."""

    model_config = ConfigDict(frozen=True)

    cost_usd: float = 0.0
    cost_estimated: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    num_turns: int = 0


class Spend(BaseModel):
    """What a window of model-calling runs cost this checkout: spec 8.4's two day ceilings."""

    model_config = ConfigDict(frozen=True)

    cost_usd: float = 0.0
    runs: int = 0


class Stratum(StrEnum):
    """Where a measured row belongs: an add stratum, or the one bucket a control is stored under.

    The tier B gate reads the add stratum matching the proposal's own shape, because a repo's
    strata do not measure alike; here they hold 883 / 1,321 / 38 of the add suite's truths, out of
    5,590 resolved `calls` edges splitting 49 / 46 / 5 per cent.
    """

    SAME_MODULE = "same-module"
    DIRECT_IMPORT = "direct-import"
    NEITHER = "neither"
    #: every control suite's one bucket, so one type covers both halves of a stored row (spec 10.2)
    ALL = "all"

    @classmethod
    def add_strata(cls) -> tuple["Stratum", ...]:
        """The three the add suite draws, which is every member `of` can answer with."""
        return (cls.SAME_MODULE, cls.DIRECT_IMPORT, cls.NEITHER)

    @classmethod
    def of(cls, src: str, dst: str, *, imports: Collection[str]) -> "Stratum":
        """The stratum one proposed edge falls in; ``imports`` is the repo module ids the source's
        own module imports."""
        src_module, dst_module = file_of(src), file_of(dst)
        if src_module == dst_module:
            return cls.SAME_MODULE
        return cls.DIRECT_IMPORT if dst_module in imports else cls.NEITHER


def key_of(suite: str, stratum: Stratum) -> str:
    """How a report and a go/no-go name one measured stratum.

    The one builder: a reader that needs the halves back reads the row's own fields, never this
    string taken apart again.
    """
    return f"{suite}/{stratum}"


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


class Checkout(BaseModel):
    """The branch and commit a run is pinned to (spec 5.5).

    Two fields rather than a bare pair, because the only thing most callers want is the commit,
    and ``head()[1]`` at a call site is one transposition away from silently pinning the branch.
    """

    model_config = ConfigDict(frozen=True)

    branch: str | None = None
    commit_sha: str | None = None


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
        checkout: Checkout,
        client: ClientKind,
        producer: ProducerKind,
        runner: RunnerKind,
        trigger: TriggerKind,
        model: str | None = None,
        session_id: str | None = None,
        agent_name: str | None = None,
        detail: TriggerDetail | None = None,
    ) -> "Run":
        """A queued run, from what its caller has to derive and what it was told (Invariant 2).

        The partition pair, the scope and the branch/HEAD pair each fan out into more than one
        stored column, which is what made this a thirteen-argument construction at the caller. A
        caller holding a whole detail passes it, because a batch's file list is not its scope.
        """
        identity, prefix = partition
        return cls(
            repo_identity=identity,
            origin_partition=origin,
            partition_prefix=prefix,
            trigger_detail=detail or TriggerDetail(files=(scope,) if scope else ()),
            branch=checkout.branch,
            commit_sha=checkout.commit_sha,
            client=client,
            producer=producer,
            runner=runner,
            trigger_kind=trigger,
            model=model,
            session_id=session_id,
            agent_name=agent_name,
        )


#: the runner each code that resolved to one names; every other code is a refusal with no runner
_KIND_BY_CODE: dict[RunnerChoiceCode, RunnerKind] = {
    RunnerChoiceCode.CLAUDE: RunnerKind.CLAUDE
}


class RunnerChoice(BaseModel):
    """The machine code a request resolved to, and the sentence a human reads.

    The code is what the wire carries and the detail is what a person acts on, so a refusal never
    has to be parsed out of prose. The runner is read off the code rather than stored beside it:
    a pair that can disagree is a pair that eventually does.
    """

    model_config = ConfigDict(frozen=True)

    code: RunnerChoiceCode
    detail: str = ""

    @property
    def kind(self) -> RunnerKind | None:
        """The runner this resolved to, or ``None`` when it resolved to a refusal."""
        return _KIND_BY_CODE.get(self.code)


class Verdict(BaseModel):
    """The service's answer about one proposal (spec 9.1).

    ``refinement_id`` is filled the moment a row exists: at `propose` for a rejection, at `commit`
    for an acceptance.
    """

    model_config = ConfigDict(frozen=True)

    outcome: ProposalOutcome
    kind: RefinementKind
    tier: Tier = Tier.C
    status: RefinementStatus = RefinementStatus.PENDING
    verify: VerifyStatus = VerifyStatus.UNVERIFIED
    #: set when the proposal never reached the verifier, so "unverified" is not read as a check
    refusal: RefusalKind | None = None
    detail: str = ""
    refinement_id: int = 0


#: What a runner hands every raw proposal to, by run id: the service, or an eval's judge (spec 10.2)
Proposer = Callable[[str, Mapping[str, Any]], Awaitable[Verdict]]


class RunReport(BaseModel):
    """One run's state. ``staged_here`` is false in a process that did not open the run, so a
    reader never mistakes another process's staging for an empty run.

    ``committed`` and ``rejected`` are the run's stored rows split by fate: a rejection is stored
    the moment it is made, so a run that committed nothing still owns rows.
    """

    model_config = ConfigDict(frozen=True)

    run: Run
    staged: tuple[Verdict, ...] = ()
    staged_here: bool = True
    committed: tuple[int, ...] = ()
    rejected: tuple[int, ...] = ()


class RefinementCounts(BaseModel):
    """How many refinements one run owns, split by fate (spec 9.2 stores every rejection).

    One shape for every surface: the run log's column, `graph_refine_status` and the summary a
    finished run records all read it, so a run cannot be credited with the work it rejected.
    """

    model_config = ConfigDict(frozen=True)

    committed: int = 0
    rejected: int = 0

    @property
    def total(self) -> int:
        """Every row the run owns, which is what a `COUNT(*)` over its id answers."""
        return self.committed + self.rejected

    @property
    def summary(self) -> str:
        """The one line a finished run records about what it produced."""
        return f"{self.committed} committed, {self.rejected} rejected"

    def plus(self, status: RefinementStatus, rows: int) -> "RefinementCounts":
        """This count with ``rows`` more of one status folded in, so which fate a status belongs
        to is decided here and not by each reader."""
        field = "rejected" if status is RefinementStatus.REJECTED else "committed"
        return self.model_copy(update={field: getattr(self, field) + rows})


class PruneOutcome(WirePayload):
    """What one retention sweep did (spec 5.1, 5.7): rows deleted, and stranded runs finished.
    This is the wire shape too, so `auditr graph refinements prune --json` has no second copy.

    Three numbers rather than one, because a sweep that deletes a run deletes the rejections it
    owns with it, and a caller told only "1 run removed" cannot see that.
    """

    removed_runs: int = 0
    removed_refinements: int = 0
    stranded_runs: int = 0


class RunAttribution(BaseModel):
    """What a producer records about how a run was driven: what it cost, what it called, the
    session it ran under, and the one line it has to say about it (Invariant 2).

    Separate from the outcome because a runner carries it through a run that may end any of four
    ways, and a run that failed still cost money.
    """

    model_config = ConfigDict(frozen=True)

    usage: RunUsage = RunUsage()
    tool_trace: tuple[ToolCall, ...] = ()
    sdk_session_id: str | None = None
    #: ``None`` from a producer that has nothing of its own to say, which is every hand-driven run
    summary: str | None = None


class RunOutcome(RunAttribution):
    """A run's terminal state: what it produced, what it cost, and when it stopped (spec 5.3).

    One field per column ``finish_run`` updates, so the UPDATE's set list is derived rather than
    hand-ordered. ``finished_at`` of ``None`` means "stamp it now".
    """

    status: RunStatus
    summary: str | None = None
    error: str | None = None
    finished_at: float | None = None

    @classmethod
    def of(
        cls,
        status: RunStatus,
        *,
        summary: str | None = None,
        error: str | None = None,
        attribution: RunAttribution | None = None,
        finished_at: float | None = None,
    ) -> "RunOutcome":
        """A terminal state with a producer's attribution folded in, defaulted when there is none.

        The service finishes runs from three places and only one of them has an attribution, so the
        merge lives here rather than at each caller. A caller that names no ``summary`` keeps the
        producer's own.
        """
        base = attribution or RunAttribution()
        return cls(
            status=status,
            summary=summary if summary is not None else base.summary,
            error=error,
            finished_at=finished_at,
            usage=base.usage,
            tool_trace=base.tool_trace,
            sdk_session_id=base.sdk_session_id,
        )


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


def _without(raw: Mapping[str, Any], exc: ValidationError) -> Mapping[str, Any]:
    """``raw`` with every value the validator could not read dropped, so the rest still reaches
    the stored rejection: an unreadable ``edge_kind`` costs its own field, not the whole target.

    A `Mapping`, like every other pre-validation payload here: the caller validates it, and the
    values inside are only as typed as what a tool was called with.
    """
    data: Mapping[str, Any] = dict(raw)
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

    def points_at(self) -> str:
        """What this correction is about, in one line: its edge, else the node or name it names."""
        src, dst = self.edge_pair()
        if src and dst:
            return f"{src} -> {dst}"
        return self.target.node_id or self.target.name or "(no target)"

    @classmethod
    def read(cls, raw: "Proposal | Mapping[str, Any]") -> tuple["Proposal", str]:
        """One proposal, and the validator's complaint about it when it is not a legal one.

        Spec 9.2 stores every rejection, so an illegal payload is re-read with the values the
        validator could not read dropped, and becomes one row carrying the complaint. ``kind`` is
        the exception: it chooses the shape, and there is no row to store without it.
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
    stratum: Stratum
    metrics: EvalMetrics = EvalMetrics()
    cost_usd: float = 0.0
    num_turns: int = 0
    created_at: float = Field(default_factory=time.time)


class EvalSuite(StrEnum):
    """The suites `auditr graph eval` can draw trials from (spec 10.2).

    ``FIXTURES`` is in the vocabulary but not in S7, so the CLI can refuse it by name rather than
    as an unknown value.
    """

    ADD = "add"
    COLLISION = "collision"
    NEGATIVE = "negative"
    DECOY = "decoy"
    FIXTURES = "fixtures"


#: the suites `--suite all` means; `fixtures` is a follow-up and is refused by name
ALL_SUITES = (EvalSuite.ADD, EvalSuite.COLLISION, EvalSuite.NEGATIVE, EvalSuite.DECOY)

#: the suites a Wilson bound gates, the only ones a flawless floor can rule out (spec 10.2)
PRECISION_SUITES = (EvalSuite.ADD, EvalSuite.DECOY)

#: the two call forms a tier B proposal can be made of, which is what the add suite draws from
BOUNDED_FORMS = (CallForm.BARE, CallForm.SELF)

#: how far the flawless floor search goes before it answers "no run of any size clears this"
FLAWLESS_FLOOR_MAX = 10_000


def wilson_lower(correct: int, total: int, *, z: float = 1.96) -> float:
    """The Wilson score interval's lower bound for ``correct`` of ``total``, ``0.0`` on no trials.

    What a tier gate reads (spec 10.2): the point estimate of a short flawless run says 1.0, which
    is why the bound and not the estimate decides activation.
    """
    if total <= 0:
        return 0.0
    phat = correct / total
    denominator = 1.0 + z**2 / total
    centre = phat + z**2 / (2 * total)
    spread = z * math.sqrt((phat * (1.0 - phat) + z**2 / (4 * total)) / total)
    return max(0.0, (centre - spread) / denominator)


def flawless_floor(min_precision: float, *, z: float = 1.96) -> int | None:
    """The smallest number of trials whose flawless run clears ``min_precision`` (spec 10.4).

    73 at the default 0.95, so a stratum with fewer truths than that on a repo cannot be proven
    there however well a runner does. Searches to `FLAWLESS_FLOOR_MAX` trials and answers ``None``
    beyond it, because `wilson_lower(n, n)` is below 1.0 for every finite ``n``.
    """
    for n in range(1, FLAWLESS_FLOOR_MAX + 1):
        if wilson_lower(n, n, z=z) >= min_precision:
            return n
    return None


class SuiteSpend(BaseModel):
    """What one stratum's runs cost, summed off the closed rows (spec 5.3)."""

    model_config = ConfigDict(frozen=True)

    cost_usd: float = 0.0
    num_turns: int = 0
    runs: int = 0

    def plus(self, usage: RunUsage) -> "SuiteSpend":
        """This spend with one more closed run's usage added."""
        return SuiteSpend(
            cost_usd=self.cost_usd + usage.cost_usd,
            num_turns=self.num_turns + usage.num_turns,
            runs=self.runs + 1,
        )


class SuiteTally(BaseModel):
    """One suite stratum's judged trials, and the runs they cost (spec 10.2).

    The four rates are computed once, on `metrics`, rather than recomputed by every reader.
    """

    model_config = ConfigDict(frozen=True)

    suite: str
    stratum: Stratum
    n: int = 0
    correct: int = 0
    wrong: int = 0
    missed: int = 0
    #: false adds against a trial, plus the off-target adds a real run would have refused
    false_adds: int = 0
    #: proposals about a node and name no trial in the batch asked about, scored or not
    off_target: int = 0
    spend: SuiteSpend = SuiteSpend()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def metrics(self) -> EvalMetrics:
        """This stratum's accuracy. ``false_removal_rate`` is 0.0: S7 evaluates no removal kind."""
        judged = self.correct + self.wrong
        precision = self.correct / judged if judged else 0.0
        return EvalMetrics(
            n=self.n,
            correct=self.correct,
            precision=precision,
            recall=self.correct / self.n if self.n else 0.0,
            false_add_rate=(self.wrong + self.false_adds) / self.n if self.n else 0.0,
            lower_bound_95=wilson_lower(self.correct, judged),
        )

    def row(self, *, identity: str, runner: RunnerKind, model: str) -> EvalRow:
        """This tally as the `graph_evals` row a tier gate later reads."""
        return EvalRow(
            repo_identity=identity,
            runner=runner,
            model=model,
            suite=self.suite,
            stratum=self.stratum,
            metrics=self.metrics,
            cost_usd=self.spend.cost_usd,
            num_turns=self.spend.num_turns,
        )


class SnapshotPhase(StrEnum):
    """Which side of a build's persist a snapshot is being taken on (spec 8.6 stage 2)."""

    BEFORE = "before"
    AFTER = "after"


#: What a rebuild calls twice inside its lock, so an assessment sees exactly one build's delta.
Snapshot = Callable[[SnapshotPhase], Awaitable[None]]
