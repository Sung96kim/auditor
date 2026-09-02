"""What `tests/graph/` shares: the refinement-run drivers, a run row, and a console render.

Graph-local on purpose. Driving a run needs `fastmcp` and `auditor.mcp`, and the tree-wide
`tests/_support.py` is imported by every test session, fast CLI tests included.
"""

import asyncio
import enum
import io
import re
import secrets
import threading
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, TypeVar

from _support import tool_data
from fastmcp import Client
from pydantic import BaseModel, ConfigDict, Field
from rich.console import Console

from auditor.database import open_repo_index
from auditor.graph.build import GraphBuilder
from auditor.graph.model import UnresolvedRow
from auditor.graph.refine.client import ClientFactory, ServerStatus
from auditor.graph.refine.models import (
    Assessment,
    AssessmentDecision,
    Decision,
    ProducerKind,
    Proposer,
    Run,
    RunnerKind,
    RunStatus,
    TriggerDetail,
    TriggerKind,
)
from auditor.graph.refine.prompts import STRUCTURED_OUTPUT_TOOL
from auditor.graph.refine.runner import FakeRun, FakeRunner, RefinementRunner
from auditor.graph.refine.sdk_runner import BoundTools, SdkOptions
from auditor.graph.refine.service import RefinementService
from auditor.mcp import mcp

PayloadT = TypeVar("PayloadT")

#: the one true correction on the `refine_repo` pair: `caller.main` calls a name `helper` defines
GOOD_PROPOSAL: Mapping[str, str] = MappingProxyType(
    {
        "kind": "add_edge",
        "src": "caller.py::main",
        "dst": "helper.py::read_event",
        "edge_kind": "calls",
        "name": "read_event",
        "reason": "main calls read_event, which helper.py defines",
    }
)


async def _drive(
    repo: Path, proposals: Sequence[Mapping[str, Any]], reason: str | None
) -> dict[str, Any]:
    """Open a run, propose into it, and end it the way ``reason`` says: abort, or commit."""
    async with Client(mcp) as client:
        begun = await client.call_tool("graph_refine_begin", {"path": str(repo)})
        run_id = tool_data(begun)["run_id"]
        for proposal in proposals:
            await client.call_tool(
                "graph_refine_propose",
                {"path": str(repo), "run_id": run_id, **proposal},
            )
        args: dict[str, Any] = {"path": str(repo), "run_id": run_id}
        if reason is None:
            return tool_data(await client.call_tool("graph_refine_commit", args))
        ended = await client.call_tool("graph_refine_abort", args | {"reason": reason})
        return tool_data(ended)


def refine_run(repo: Path, *proposals: Mapping[str, Any]) -> dict[str, Any]:
    """Drive one run through the MCP tools to its commit; answers that commit's ``CommitResult``.

    The tools are the public producer, so the rows a test reads were written the way an agent
    writes them.
    """
    return asyncio.run(_drive(repo, proposals, None))


def refine_abort(
    repo: Path, *proposals: Mapping[str, Any], reason: str
) -> dict[str, Any]:
    """Drive one run and abort it; answers the finished run row, not a commit result."""
    return asyncio.run(_drive(repo, proposals, reason))


def tool_log(repo: Path, **kw: Any) -> dict[str, Any]:
    """One page of the provenance log through the `graph_log` MCP tool, the surface the CLI mirrors."""

    async def go() -> dict[str, Any]:
        async with Client(mcp) as client:
            return tool_data(
                await client.call_tool("graph_log", {"path": str(repo), **kw})
            )

    return asyncio.run(go())


def add_observer_run(repo: Path, *, status: RunStatus, age_seconds: float) -> str:
    """One observer run row written directly and aged by hand, which is the only way a test can
    have a run older than a retention window. It carries an assessment, because that object plus
    an empty `error` is what tells the retention sweep an assessment row from a real one."""

    async def go() -> str:
        index = await open_repo_index(repo)
        try:
            return await index.runs.add_run(
                Run(
                    repo_identity=index.partition.identity,
                    producer=ProducerKind.OBSERVER,
                    runner=RunnerKind.NONE,
                    trigger_kind=TriggerKind.EDIT,
                    status=status,
                    trigger_detail=TriggerDetail(
                        files=("m.py",),
                        assessment=Assessment(
                            files=("m.py",),
                            verdict=Decision(
                                decision=AssessmentDecision.SKIP,
                                reason="no structural change",
                            ),
                        ),
                    ),
                    summary="no structural change",
                    started_at=time.time() - age_seconds,
                )
            )
        finally:
            await index.aclose()

    return asyncio.run(go())


def render_text(
    render: Callable[[Console, PayloadT], None],
    payload: PayloadT,
    *,
    width: int = 120,
    color: bool = False,
) -> str:
    """One payload through its own renderer, as text at a fixed console width.

    ``color`` forces the ANSI codes a real terminal would get: off a TTY rich drops every style,
    so a cell that is styled apart from its neighbours is invisible without it.
    """
    buf = io.StringIO()
    console = (
        Console(file=buf, width=width, force_terminal=True, color_system="standard")
        if color
        else Console(file=buf, width=width)
    )
    render(console, payload)
    return buf.getvalue()


def cells(rendered: str, first: str) -> list[str]:
    """The cells of the row whose first column is ``first``, so a value under the wrong header is
    visible where a substring search over the whole table is not."""
    for line in rendered.splitlines():
        parts = re.split(r"[│┃]", line)
        row = [c.strip() for c in parts[1:-1]]
        if len(parts) > 2 and row and row[0] == first:
            return row
    raise AssertionError(f"no row starting {first!r} in\n{rendered}")


def with_lock_timeout(
    service: RefinementService, seconds: float, *, poll: float | None = None
) -> RefinementService:
    """Shrink this service's rebuild-lock budget, so a held lock is a fast refusal.

    ``poll`` shrinks the interval between attempts as well, for a test that watches the wait end
    rather than the refusal arrive.
    """
    budget: dict[str, float] = {"rebuild_lock_timeout_seconds": seconds}
    if poll is not None:
        budget["rebuild_lock_poll_seconds"] = poll
    service.settings = service.settings.model_copy(
        update={"graph": service.settings.graph.model_copy(update=budget)}
    )
    return service


def shape_spy(monkeypatch) -> list[int]:
    """Patch ``GraphBuilder.shape`` to record the thread id of every call and delegate to the
    real implementation, so a test can assert the call left the event loop thread (S11 M2).
    """
    seen: list[int] = []
    real = GraphBuilder.shape

    async def spy(self, index, settings, *, progress=None):
        seen.append(threading.get_ident())
        return await real(self, index, settings, progress=progress)

    monkeypatch.setattr(GraphBuilder, "shape", spy)
    return seen


def blocking_shape(monkeypatch) -> tuple[threading.Event, threading.Event]:
    """Patch ``GraphBuilder.shape`` to block until released, so a test can run something else
    while a shape call is in flight (S11 M2).

    Returns ``(started, release)``: the patched call sets ``started`` as soon as it begins, waits
    on ``release``, then delegates to the real implementation.
    """
    started, release = threading.Event(), threading.Event()
    real = GraphBuilder.shape

    async def blocking(self, index, settings, *, progress=None):
        started.set()
        release.wait(10.0)
        return await real(self, index, settings, progress=progress)

    monkeypatch.setattr(GraphBuilder, "shape", blocking)
    return started, release


class Init(BaseModel):
    """A `SystemMessage(subtype="init")` as the runner duck-types it."""

    model_config = ConfigDict(frozen=True)

    data: dict[str, Any]
    subtype: str = "init"


class Tick(BaseModel):
    """Any other `SystemMessage`: a progress tick the runner must skip past."""

    model_config = ConfigDict(frozen=True)

    subtype: str = "thinking_tokens"
    data: dict[str, Any] = Field(default_factory=dict)


class Assistant(BaseModel):
    """An `AssistantMessage`, with the tool calls the fake client will actually make."""

    model_config = ConfigDict(frozen=True)

    model: str = "claude-haiku-4-5-20251001"
    content: tuple[str, ...] = ()
    error: str | None = None
    #: (tool name, arguments) per call, replayed against the bound tools
    tool_calls: tuple[tuple[str, Mapping[str, Any]], ...] = ()


class LimitInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str = "allowed_warning"
    resets_at: int | None = None
    utilization: float | None = None


class Limit(BaseModel):
    """A `RateLimitEvent`, whose `rate_limit_info` the runner reads by attribute."""

    model_config = ConfigDict(frozen=True)

    rate_limit_info: LimitInfo


class Result(BaseModel):
    """A `ResultMessage`, carrying only the fields the outcome mapping reads."""

    model_config = ConfigDict(frozen=True)

    subtype: str = "success"
    num_turns: int = 3
    total_cost_usd: float | None = 0.004
    session_id: str = "sdk-session"
    model_usage: dict[str, dict[str, Any]] | None = None
    usage: dict[str, Any] | None = None
    structured_output: Any = None
    errors: tuple[str, ...] | None = None
    is_error: bool = False
    api_error_status: int | None = None


def init_data(**overrides: Any) -> dict[str, Any]:
    """An init payload the check accepts, before the test spoils one field of it."""
    return {
        "mcp_servers": [{"name": "graph", "status": "connected"}],
        "plugins": [],
        "tools": ["Read", "Grep", "Glob", "StructuredOutput", "mcp__graph__propose"],
        "permissionMode": "dontAsk",
        "model": "claude-haiku-4-5-20251001",
        "session_id": "sdk-session",
        "claude_code_version": "2.1.239",
        **overrides,
    }


#: the same correction in the nested shape `service.propose` takes, so the two cannot drift
SCRIPTED_PROPOSAL: Mapping[str, Any] = MappingProxyType(
    {
        "kind": GOOD_PROPOSAL["kind"],
        "target": {k: GOOD_PROPOSAL[k] for k in ("src", "dst", "edge_kind", "name")},
        "reason": GOOD_PROPOSAL["reason"],
    }
)


def eval_build(
    answers: Mapping[tuple[str, str], Mapping[str, Any]], pretend: FakeRun | None = None
) -> Callable[[RefinementService, Proposer], RefinementRunner]:
    """An `EvalRun` runner factory that replays the answer prepared for each masked row.

    The batch's own rows are on the service it is handed, so a scripted eval needs no second copy
    of what the draw made.
    """

    def build(service: RefinementService, proposer: Proposer) -> RefinementRunner:
        script = tuple(
            answers[key]
            for row in service.facts.synthetic
            if (key := (row.node_id, row.name)) in answers
        )
        return FakeRunner(
            service,
            proposer=proposer,
            pretend=(pretend or FakeRun()).model_copy(update={"script": script}),
        )

    return build


class ClaudeShaped(FakeRunner):
    """A fake that reports itself as the Claude runner, so the choice logic runs unchanged.

    Both surfaces need one, and `drive.refine` picks its runner off the registry, so a test that
    wants a scripted run swaps this in for `RunnerKind.CLAUDE`.
    """

    kind: ClassVar[RunnerKind] = RunnerKind.CLAUDE
    pretends: ClassVar[FakeRun] = FakeRun(script=(SCRIPTED_PROPOSAL,))

    def __init__(
        self,
        service: RefinementService,
        client_factory: ClientFactory | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("pretend", self.pretends)
        super().__init__(service, client_factory, **kwargs)


class CodexShaped(ClaudeShaped):
    """The same fake, reporting itself as the Codex runner.

    A subclass rather than a second copy: what a surface test wants is the choice logic and one
    scripted run, and the only difference between the two runners at that level is the kind.
    """

    kind: ClassVar[RunnerKind] = RunnerKind.CODEX


class EvalClaude(ClaudeShaped):
    """A Claude-shaped runner that answers each masked row from the row itself.

    A good model, not an omniscient one: an add row names its one definer so the answer is right,
    a decoy row names several candidates and this takes the first, and a row that offers nothing to
    point at is answered `unresolvable`, which is what a control is judged on.
    """

    def __init__(
        self,
        service: RefinementService,
        client_factory: ClientFactory | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(service, client_factory, **kwargs)
        self.pretend = self.pretend.model_copy(
            update={"script": tuple(answer_for(row) for row in service.facts.synthetic)}
        )


def answer_for(row: UnresolvedRow) -> dict[str, Any]:
    """The proposal a careful reader of one brief target would make."""
    if row.externally_bound or not (row.candidates or row.definers):
        return {
            "kind": "unresolvable",
            "target": {"node_id": row.node_id, "name": row.name},
            "payload": {"reason_code": row.reason.value},
            "reason": "nothing in this repo can be pointed at for this name",
        }
    if row.candidates:
        return {
            "kind": "resolve_ambiguous",
            "target": {
                "node_id": row.node_id,
                "name": row.name,
                "edge_kind": "calls",
            },
            "payload": {"candidate": row.candidates[0]},
            "reason": "the first candidate is the one this call site reaches",
        }
    return {
        "kind": "add_edge",
        "target": {
            "src": row.node_id,
            "dst": row.definers[0],
            "edge_kind": "calls",
            "name": row.name,
        },
        "reason": "the call site and the one definition both read",
    }


#: a node the eval package never masks an edge to, so an add pointed here is plainly wrong
MISDIRECTED_DST = "other.py::match"


class WrongEvalClaude(EvalClaude):
    """The same reader pointing every add at the wrong node: the regression a later eval catches."""

    def __init__(
        self,
        service: RefinementService,
        client_factory: ClientFactory | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(service, client_factory, **kwargs)
        self.pretend = self.pretend.model_copy(
            update={"script": tuple(_misdirected(p) for p in self.pretend.script)}
        )


def _misdirected(proposal: Mapping[str, Any]) -> dict[str, Any]:
    """One proposal aimed somewhere it does not belong; the other kinds are left alone."""
    if proposal["kind"] != "add_edge":
        return dict(proposal)
    return {**proposal, "target": {**proposal["target"], "dst": MISDIRECTED_DST}}


class FailingClaude(ClaudeShaped):
    """The same runner, giving up rather than committing."""

    pretends: ClassVar[FakeRun] = FakeRun(stop="the model gave up")


def hook_payload(
    name: str, args: Mapping[str, Any], response: Any, *, duration_ms: int = 5
) -> dict[str, Any]:
    """One `PostToolUse` input in the shape the CLI really sends (spike B.1, measured).

    ``duration_ms`` and ``prompt_id`` are the two undocumented extras beyond
    `PostToolUseHookInput`; the trace reads the first of them.
    """
    return {
        "cwd": "/tmp/repo",
        "duration_ms": duration_ms,
        "hook_event_name": "PostToolUse",
        "permission_mode": "dontAsk",
        "prompt_id": "prompt-1",
        "session_id": "sdk-session",
        "tool_input": dict(args),
        "tool_name": name,
        "tool_response": response,
        "tool_use_id": f"toolu_{name}",
        "transcript_path": "/tmp/transcript.jsonl",
    }


class FakeClient:
    """A `ClientSession` that replays scripted messages and really calls the bound tools.

    An assistant message's `tool_calls` are resolved through the same `BoundTools.tools()` table
    production registers, so a mis-wired handler or a renamed tool fails here too. A name the run
    does not expose is an error, not a silent empty answer.
    """

    def __init__(self, messages: Sequence[Any], tools: BoundTools) -> None:
        self.messages = messages
        self.tools = tools
        self.prompt: str | None = None

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def query(self, prompt: str) -> None:
        self.prompt = prompt

    async def _call(self, name: str, args: Mapping[str, Any]) -> Any:
        """One scripted tool call, through the run's own table."""
        bound = {tool.qualified: tool for tool in self.tools.tools()}.get(name)
        if bound is not None:
            return await bound.handler(args)
        if name == STRUCTURED_OUTPUT_TOOL:
            # the CLI injects this one and answers for it; it never reaches our handlers
            return "Structured output provided successfully"
        raise AssertionError(
            f"the script called {name!r}, which this run does not expose"
        )

    async def _replay(self, message: Any) -> None:
        for name, args in message.tool_calls:
            response = await self._call(name, args)
            await self.tools.record(hook_payload(name, args, response))

    async def receive_response(self) -> AsyncIterator[Any]:
        for message in self.messages:
            if isinstance(message, Assistant) and message.tool_calls:
                await self._replay(message)
            yield message


def fake_factory(
    messages: Sequence[Any], seen: list[SdkOptions] | None = None
) -> ClientFactory:
    """A client factory over one scripted message list, recording the options it was handed."""

    def factory(options: SdkOptions, tools: BoundTools) -> FakeClient:
        if seen is not None:
            seen.append(options)
        return FakeClient(messages, tools)

    return factory


class Breakdown(BaseModel):
    """`TokenUsageBreakdown`'s six counters, as a fake can build them with no SDK installed."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0


class Usage(BaseModel):
    """`ThreadTokenUsage`: the thread total, and this turn's own."""

    total: Breakdown | None = None
    last: Breakdown | None = None


class TurnError(BaseModel):
    message: str = ""


class McpCall(BaseModel):
    """One `McpToolCallThreadItem`, already unwrapped: a fake needs no `.root` to build."""

    type: str = "mcpToolCall"
    server: str = "graph"
    tool: str = "propose"
    arguments: Mapping[str, Any] = MappingProxyType({})
    duration_ms: int = 0


class Command(BaseModel):
    """One `CommandExecutionThreadItem`."""

    type: str = "commandExecution"
    command: str = "rg needle"
    duration_ms: int = 0


class Rooted(BaseModel):
    """A `ThreadItem` root model, so a test can prove the runner unwraps `.root`."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    root: Any


class TurnStatus(enum.Enum):
    """The SDK's own status enum, member for member.

    A plain `Enum` and not a `StrEnum`, because `openai_codex`'s is: a double handing back a
    `str` lets code that stringifies before unwrapping `.value` pass here and fail on the SDK.
    """

    completed = "completed"
    interrupted = "interrupted"
    failed = "failed"
    in_progress = "inProgress"


class Turn(BaseModel):
    """`TurnResult`'s shape, which is a plain dataclass, so a fake can be this small."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = "turn-1"
    status: TurnStatus | str = TurnStatus.completed
    error: TurnError | None = None
    started_at: int | None = None
    items: tuple[Any, ...] = ()
    final_response: str | None = None
    usage: Usage | None = None


class FakeThread:
    """A thread whose one turn is scripted, and which really calls the shim's bound tools."""

    def __init__(self, turn: Any, tools: BoundTools) -> None:
        self.id = "thread-1"
        self.turn = turn
        self.tools = tools
        self.prompt: str | None = None

    async def run(self, prompt: str) -> Any:
        """Replay the turn, really calling the bound tools its mcp items name first."""
        self.prompt = prompt
        for item in getattr(self.turn, "items", ()):
            inner = getattr(item, "root", item)
            if getattr(inner, "type", "") == "mcpToolCall":
                await self._call(inner)
        if isinstance(self.turn, Exception):
            raise self.turn
        return self.turn

    async def _call(self, item: Any) -> None:
        """One scripted mcp call, through the run's own table, so a rename fails here too."""
        bound = {tool.name: tool for tool in self.tools.tools()}.get(item.tool)
        if bound is None:
            raise AssertionError(
                f"the script called {item.tool!r}, which this run does not expose"
            )
        await bound.handler(dict(item.arguments))


class FakeCodex:
    """A `CodexSession` over a scripted turn, one server list and one rate limit answer."""

    def __init__(
        self,
        turn: Any,
        tools: BoundTools,
        *,
        servers: tuple[str, ...] = ("graph",),
        limit: Any = None,
        handshake: str | None = None,
        answered: str | None = None,
    ) -> None:
        self.turn = turn
        self.tools = tools
        self._servers = servers
        self._limit = limit
        #: this session's own shim, minted per session the way `GraphShim` mints it
        self.handshake = secrets.token_hex(4) if handshake is None else handshake
        #: what the `graph` entry answers with, so a test can hand back another run's shim
        self._answered = answered
        self.thread: FakeThread | None = None

    async def __aenter__(self) -> "FakeCodex":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def servers(self) -> tuple[ServerStatus, ...]:
        answered = self.handshake if self._answered is None else self._answered
        return tuple(
            ServerStatus(name=name, handshake=answered if name == "graph" else None)
            for name in self._servers
        )

    async def rate_limit(self) -> Any:
        return self._limit

    async def thread_start(self, options: Any) -> FakeThread:
        self.thread = FakeThread(self.turn, self.tools)
        return self.thread


def codex_factory(
    turn: Any,
    *,
    servers: tuple[str, ...] = ("graph",),
    limit: Any = None,
    seen: list[Any] | None = None,
    sessions: list["FakeCodex"] | None = None,
    answered: str | None = None,
) -> Callable[..., FakeCodex]:
    """A Codex client factory over one scripted turn, recording the options it was handed.

    Built per call the way the real one is, so the session it hands back really holds this run's
    own tools and a renamed tool fails in the fake too. ``answered`` makes the `graph` entry
    report another run's handshake, which is what a crossed `config.toml` looks like.
    """

    def factory(options: Any, tools: Any) -> FakeCodex:
        if seen is not None:
            seen.append(options)
        session = FakeCodex(
            turn, tools, servers=servers, limit=limit, answered=answered
        )
        if sessions is not None:
            sessions.append(session)
        return session

    return factory
