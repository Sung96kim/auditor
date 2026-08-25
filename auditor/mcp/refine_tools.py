# auditor: skip-file: PY-TYPING-UNTYPED-DICT  (MCP tool surface — JSON payloads by contract)
"""graph_refine_* — the in-session refinement producer (spec 9.5).

An agent reads `graph_unresolved`, opens a run, proposes against what it saw, and commits. The
staged proposals live in this process, so a run is opened, filled and committed through one server.
"""

from fastmcp.exceptions import ToolError

from auditor.config import GlobalPaths
from auditor.graph.model import LOG_ROW_LIMIT, enum_value, enum_values
from auditor.graph.payloads import LogFilter, RunRowPayload
from auditor.graph.query import LogQuery
from auditor.graph.refine.models import (
    ClientKind,
    ProducerKind,
    RefinementKind,
    RefinementStatus,
    TriggerKind,
)
from auditor.graph.refine.payloads import CommitPayload, RunReportPayload
from auditor.graph.refine.service import RefinementRefused, RefinementService
from auditor.mcp.helpers import (
    MUTATING,
    MUTATING_ONCE,
    READ_ONLY,
    ToolRepo,
    tool_repo,
    tool_user,
)
from auditor.mcp.server import mcp

#: the env var `GlobalPaths.refine_run` binds, named in the error that tells a caller about it
RUN_ENV = "AUDITOR_REFINE_RUN"


async def _service(repo: ToolRepo) -> RefinementService:
    """One service per tool call, over the run registry this process already shares.

    The registry is deliberately not passed: `RefinementService` defaults to
    `RunRegistry.process(identity)`, and a second one would split the staging
    `graph_refine_status` reports on. The user's settings come through `tool_user`, so the read
    stays off the event loop and a broken settings file is one line rather than a traceback.
    """
    return RefinementService(
        repo.index, repo.root, repo.settings, await tool_user(repo)
    )


def _run_id(given: str | None) -> str:
    """The run these tools act on: the argument, else the env binding, else an error saying how
    to open one."""
    run_id = given or GlobalPaths().refine_run
    if not run_id:
        raise ToolError(
            "no run to work on: call graph_refine_begin first, or set "
            f"{RUN_ENV} for a server a runner spawned"
        )
    return run_id


def _client(raw: str) -> ClientKind:
    """The client a run is attributed to, refused by name rather than logged as another one."""
    return ClientKind(enum_value(raw, ClientKind, "client"))


@mcp.tool(annotations=MUTATING_ONCE)
async def graph_refine_begin(
    path: str = ".",
    scope: str = "",
    client: str = "claude-code",
    agent_name: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Open a refinement run and get its ``run_id``. Every proposal belongs to a run, and the run
    records the branch and commit it started against, so a correction is always attributable.
    ``scope`` is a path prefix (for example ``auditor/graph/``) that every id a proposal names must
    fall under; leave it empty for the whole repo, and a prefix that could never name a node here
    is an error. ``client`` is one of claude-code, codex, cli. Read ``graph_unresolved`` first: a
    proposal that answers a queue row is the only shape that can reach tier B."""
    async with tool_repo(path) as repo:
        service = await _service(repo)
        try:
            run = await service.begin(
                scope=scope,
                producer=ProducerKind.AGENT,
                client=_client(client),
                trigger=TriggerKind.MANUAL,
                agent_name=agent_name,
                session_id=session_id,
            )
        except (RefinementRefused, ValueError) as exc:
            raise ToolError(str(exc)) from exc
    return RunRowPayload.of(run).model_dump(mode="json")


@mcp.tool(annotations=MUTATING_ONCE)
async def graph_refine_propose(
    path: str = ".",
    run_id: str | None = None,
    kind: str = "add_edge",
    reason: str = "",
    src: str | None = None,
    dst: str | None = None,
    edge_kind: str | None = None,
    from_dst: str | None = None,
    to_dst: str | None = None,
    node_id: str | None = None,
    name: str | None = None,
    members: list[str] | None = None,
    label: str | None = None,
    annotation: str | None = None,
    candidate: str | None = None,
    reason_code: str | None = None,
    call_form: str | None = None,
    evidence: list[dict] | None = None,
    confidence: float = 0.0,
) -> dict:
    """Offer one correction to the graph. Nothing is written to the graph here: the proposal is
    checked against the source file's own AST facts and staged until graph_refine_commit.

    Kinds and what each needs: ``add_edge`` (src, dst, edge_kind, name), ``retarget_edge`` (src,
    from_dst, to_dst, edge_kind, name), ``confirm_edge`` (src, dst, edge_kind, name),
    ``resolve_ambiguous`` (node_id, name, edge_kind, candidate from the queue row's candidates),
    ``relabel_cluster`` (members, label of 2 to 40 characters), ``move_node`` (node_id, members),
    ``annotate_node`` (node_id, annotation of at most 280 characters), ``unresolvable`` (node_id,
    name, reason_code). ``edge_kind`` is one of calls, references_type, callback_arg, inherits,
    overrides. ``reason`` is required on every kind.

    ``confidence`` is a 0 to 1 scale, recorded with the row as provenance; no gate reads it.

    Returns {outcome, kind, tier, status, verify, refusal, detail, refinement_id}. ``outcome`` is
    "staged" or "rejected"; ``verify`` says which check failed, and ``refusal`` is set instead when
    the proposal never reached the check at all (invalid, over_cap, out_of_scope, already_staged,
    intra_batch, out_of_partition). ``refinement_id`` is 0 while a proposal is staged: its row is
    written at commit. A payload this tool cannot even shape into a correction comes back as
    "rejected" with refusal "invalid" and is recorded like any other rejection, the unreadable
    values dropped and the complaint in ``detail``; only an unknown ``kind`` is an error instead,
    because the kind chooses the shape and there is no row to store without it.
    ``verify: "ok"`` means the source's own facts support an edge of this shape to a node that
    defines the name; where several nodes define it, it does not mean this one is the right one,
    which is why such a proposal is tier C. Until this repo has eval numbers, every ``add_edge``,
    ``retarget_edge``, ``resolve_ambiguous`` and ``move_node`` lands "pending" and needs a human to
    run `auditr graph refinements accept <id>` before a build applies it: activating your own
    correction is not something this tool set can do, by design."""
    try:
        enum_value(kind, RefinementKind, "kind")
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    # the raw arguments, not a built `Proposal`: `Proposal` owns spec 9.2's shape and text rules,
    # and `propose` stores the rejection an illegal one earns (spec 9.2)
    proposal = {
        "kind": kind,
        "target": {
            "src": src,
            "dst": dst,
            "edge_kind": edge_kind,
            "from_dst": from_dst,
            "to_dst": to_dst,
            "node_id": node_id,
            "name": name,
            "members": tuple(members or ()),
        },
        "payload": {
            "label": label,
            "annotation": annotation,
            "candidate": candidate,
            "reason_code": reason_code,
            "call_form": call_form,
        },
        "reason": reason,
        "evidence": tuple(evidence or ()),
        "confidence": confidence,
    }
    async with tool_repo(path) as repo:
        service = await _service(repo)
        try:
            verdict = await service.propose(_run_id(run_id), proposal)
        except RefinementRefused as exc:
            raise ToolError(str(exc)) from exc
    return verdict.model_dump(mode="json")


@mcp.tool(annotations=MUTATING)
async def graph_refine_commit(path: str = ".", run_id: str | None = None) -> dict:
    """Land every staged proposal and rebuild the graph. Takes this checkout's rebuild lock for the
    whole operation, so it waits while another build is running and takes about as long as
    `graph_build --no-scan`; if that wait runs out it comes back as an error naming the lock, and
    nothing is committed. Refuses if the checkout moved (a different branch or commit) since the run
    began. A run that staged nothing takes no lock and runs no build. Returns {run_id, committed,
    rejected, landed, rebuilt, build}; ``build`` is the same shape graph_build returns, and is null
    when ``rebuilt`` is false."""
    async with tool_repo(path) as repo:
        service = await _service(repo)
        try:
            result = await service.commit(_run_id(run_id))
        except RefinementRefused as exc:
            raise ToolError(str(exc)) from exc
    return CommitPayload.of(result).model_dump(mode="json")


@mcp.tool(annotations=MUTATING)
async def graph_refine_abort(
    path: str = ".", run_id: str | None = None, reason: str = "aborted by the agent"
) -> dict:
    """Drop everything staged on a run and close it. Nothing staged was ever written, so nothing is
    removed from the graph; rejections already recorded stay. Returns the run row."""
    async with tool_repo(path) as repo:
        service = await _service(repo)
        try:
            run = await service.abort(_run_id(run_id), reason)
        except RefinementRefused as exc:
            raise ToolError(str(exc)) from exc
    return RunRowPayload.of(run).model_dump(mode="json")


@mcp.tool(annotations=READ_ONLY)
async def graph_refine_status(path: str = ".", run_id: str | None = None) -> dict:
    """What one run has done: its row, what it has staged, and the ids it already owns, split into
    ``committed`` and ``rejected``. ``staged_here`` is false when the run was opened by another
    process, in which case ``staged`` is empty because staging never reaches the database."""
    async with tool_repo(path) as repo:
        service = await _service(repo)
        try:
            report = await service.status(_run_id(run_id))
        except RefinementRefused as exc:
            raise ToolError(str(exc)) from exc
    return RunReportPayload.of(report).model_dump(mode="json")


@mcp.tool(annotations=READ_ONLY)
async def graph_refinements(
    path: str = ".", status: list[str] | None = None, limit: int = LOG_ROW_LIMIT
) -> dict:
    """The corrections recorded for this checkout, oldest first, which is the order a build applies
    them in. Use graph_log with view="refinements" for the newest first. Filter with ``status``
    (pending | active | stale | redundant | reverted | pinned | superseded | rejected), a
    repeatable list; an unknown value is an error. Returns {rows, filtered}; ``filtered`` says
    whether an empty list means "nothing matched" rather than "nothing recorded". A ``pending`` row
    is waiting on a human running `auditr graph refinements accept <id>`; no tool here can activate
    one."""
    async with tool_repo(path) as repo:
        try:
            statuses = enum_values(status, RefinementStatus, "status")
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        report = await LogQuery(repo.index).refinements(
            [RefinementStatus(s) for s in statuses] if statuses else None, limit
        )
    return report.model_dump(mode="json")


@mcp.tool(annotations=READ_ONLY)
async def graph_log(
    path: str = ".",
    view: str = "runs",
    status: list[str] | None = None,
    since: str | None = None,
    skipped: bool = False,
    limit: int = LOG_ROW_LIMIT,
) -> dict:
    """The provenance log: every decision (``view="runs"``) or every correction
    (``view="refinements"``), newest first. ``status`` is validated against whichever view you
    chose, so a run status is an error in the refinements view and the message names the valid set.
    ``since`` takes a duration (90s, 45m, 2h, 7d) or an ISO date, never a git ref. Assessment-only
    runs are hidden unless ``skipped`` is true. Returns {view, runs, refinements, filtered}."""
    async with tool_repo(path) as repo:
        try:
            spec = LogFilter.of(
                view=view, status=status, since=since, skipped=skipped, limit=limit
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        return (await LogQuery(repo.index).page(spec)).model_dump(mode="json")
