"""The Claude runner, and everything about it that needs no SDK (spec 9.3, 9.5).

Free of `claude_agent_sdk` on purpose: CI never installs that extra, so the message loop, the
init check and the outcome mapping are only testable if they live here. The client arrives through
an injected factory that answers to `ClientSession`; `sdk_client.py` is the one that builds a real
one.
"""

import asyncio
import json
import logging
import shutil
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from auditor.graph.refine.brief import Brief
from auditor.graph.refine.client import ClientFactory
from auditor.graph.refine.models import (
    Proposal,
    RunAttribution,
    RunnerKind,
    RunOutcome,
    RunStatus,
    RunUsage,
    ToolCall,
)
from auditor.graph.refine.prompts import (
    ALLOWED_TOOLS,
    BRIEF_DESCRIPTION,
    GRAPH_SERVER,
    MODEL_TOOLS,
    PROPOSE_DESCRIPTION,
    SYSTEM_PROMPT,
    RunAnswer,
)
from auditor.graph.refine.runner import (
    RefinementJob,
    RefinementRunner,
    RunProduct,
)
from auditor.graph.refine.service import RefinementRefused, RefinementService
from auditor.user_settings import ClaudeModel, UserSettings

logger = logging.getLogger(__name__)

#: the options that can hold exactly one value, so nothing else can be expressed (Invariant 4).
#: `sdk_client.py` reads them directly; the init check re-verifies the same facts from the CLI's
#: own answer, which is what actually enforces the allow-list.
EFFORT = "low"
PERMISSION_MODE = "dontAsk"
SETTING_SOURCES: tuple[()] = ()
STRICT_MCP_CONFIG = True
#: the one settings tier `setting_sources=[]` cannot switch off (spike A.8), read as a file
MANAGED_SETTINGS = Path("/etc/claude-code/managed-settings.json")
#: the subtypes that mean the run hit a cap rather than broke (spec 5.3's mapping table)
CAPPED_SUBTYPES = frozenset(
    {"error_max_turns", "error_max_budget_usd", "error_max_structured_output_retries"}
)
#: how much of a tool's input the trace keeps
DETAIL_CHARS = 80
#: every `AssistantMessageError` literal, as a status and the words S8's loop reads. A pause is
#: `aborted`: the run stopped for a reason outside itself, which is not the producer breaking.
ASSISTANT_ERRORS: dict[str, tuple[RunStatus, str]] = {
    "authentication_failed": (RunStatus.FAILED, "paused:auth"),
    "rate_limit": (RunStatus.ABORTED, "paused:ratelimit"),
    "billing_error": (RunStatus.ABORTED, "paused:billing"),
    "invalid_request": (RunStatus.FAILED, "invalid_request"),
    "server_error": (RunStatus.FAILED, "server_error"),
    "unknown": (RunStatus.FAILED, "unknown"),
}
#: the JSON Schema for a tool that takes nothing at all
NO_ARGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


class SdkErrorKind(StrEnum):
    """What went wrong under the client, translated out of the SDK's own exception classes."""

    NOT_FOUND = "not_found"
    CONNECTION = "connection"
    PROCESS = "process"
    DECODE = "decode"
    RESULT = "result"


class SdkClientError(Exception):
    """Anything the client raised, in a type this SDK-free module can name and a test can build."""

    def __init__(self, message: str, *, kind: SdkErrorKind) -> None:
        super().__init__(message)
        self.kind = kind


def claude_on_path() -> Path | None:
    """The `claude` binary a run should use, or ``None`` to let the SDK fall back to its bundle.

    The SDK's own default is the reverse (spike A.5), and spec 9.3 wants the user's own CLI first.
    """
    found = shutil.which("claude")
    return Path(found) if found else None


class SdkOptions(BaseModel):
    """Everything about one run that varies. What cannot vary is a module constant above."""

    model_config = ConfigDict(frozen=True)

    model: ClaudeModel
    cwd: Path
    cli_path: Path | None
    system_prompt: str
    max_turns: int
    max_budget_usd: float
    #: a field, not a constant: `None` reaching the SDK is a full-tool-surface run (spike A.10)
    tools: tuple[str, ...] = MODEL_TOOLS

    @classmethod
    def of(
        cls,
        job: RefinementJob,
        user: UserSettings,
        root: Path,
        *,
        cli_path: Path | None,
    ) -> "SdkOptions":
        """One run's options from the job and this user's configured limits."""
        return cls(
            model=job.model or user.observer.runner.model,
            cwd=root,
            cli_path=cli_path,
            system_prompt=SYSTEM_PROMPT,
            max_turns=user.observer.limits.max_turns,
            max_budget_usd=user.observer.budget.max_budget_usd_per_run,
        )

    def refusal(self, data: Mapping[str, Any]) -> str | None:
        """Why the session the CLI opened is not the one that was asked for (Invariant 4).

        Every clause names what it refused: a surprise here is a fact about the CLI, and a bare
        "refused" would send the reader to the source instead of the message.
        """
        servers = data.get("mcp_servers") or []
        names = {str(server.get("name")) for server in servers}
        if not names:
            return "refused: no mcp servers, so there is no graph server to propose through"
        if names != {GRAPH_SERVER}:
            return f"refused: unexpected mcp servers {sorted(names)}"
        adrift = sorted(
            str(s.get("name")) for s in servers if s.get("status") != "connected"
        )
        if adrift:
            return f"refused: mcp servers not connected {adrift}"
        if data.get("plugins"):
            return f"refused: plugins are loaded {sorted(data['plugins'], key=str)}"
        extra = sorted(set(data.get("tools") or ()) - set(ALLOWED_TOOLS))
        if extra:
            return f"refused: unexpected tools {extra}"
        mode = data.get("permissionMode")
        if mode != PERMISSION_MODE:
            return f"refused: permission mode {mode!r}, not {PERMISSION_MODE!r}"
        served = str(data.get("model") or "")
        if self.model not in served:
            return f"refused: model {served!r} is not {self.model}"
        return None


def _content(text: str, *, is_error: bool = False) -> dict[str, Any]:
    """One in-process tool's answer in the shape the SDK's `@tool` contract asks for."""
    answer: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        answer["is_error"] = True
    return answer


class BoundTool(BaseModel):
    """One in-process tool a run exposes: what the model is shown, and what runs when it calls.

    All four parts in one place: the name, the description and the schema used to live in three
    modules that had to agree, and a typo in any of them was a runtime failure nothing caught.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]]

    @property
    def qualified(self) -> str:
        """This tool as the allow-list and the trace name it, server prefix included."""
        return f"mcp__{GRAPH_SERVER}__{self.name}"


class BoundTools(BaseModel):
    """The two in-process tools one run exposes, bound to that run, plus the trace they leave.

    A mutable aggregate like `StagedRun`: it is filled in as the run proceeds and read when it
    ends. The trace is one thing owned by one object, so the `PostToolUse` sink is a method here
    rather than a second thing the factory has to be handed.
    """

    model_config = ConfigDict(frozen=False, arbitrary_types_allowed=True)

    service: RefinementService
    run_id: str
    trace: list[ToolCall] = Field(default_factory=list)

    def tools(self) -> tuple[BoundTool, ...]:
        """Every tool this run exposes, in the order `prompts.GRAPH_TOOLS` names them.

        The one table: `sdk_client.py` translates it for the SDK and nothing else decides what a
        run may be called with.
        """
        return (
            BoundTool(
                name="propose",
                description=PROPOSE_DESCRIPTION,
                # verbatim: a flat MCP shape would be a fourth hand-maintained copy of one schema
                input_schema=Proposal.model_json_schema(),
                handler=self.propose,
            ),
            BoundTool(
                name="brief",
                description=BRIEF_DESCRIPTION,
                input_schema=NO_ARGS_SCHEMA,
                handler=self.brief,
            ),
        )

    async def propose(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """Stage one proposal against this run and answer with the verdict."""
        try:
            verdict = await self.service.propose(self.run_id, args)
        except RefinementRefused as exc:
            return _content(str(exc), is_error=True)
        return _content(verdict.model_dump_json())

    async def brief(self, _args: Mapping[str, Any]) -> dict[str, Any]:
        """Re-read this run's brief, with the verdicts it has earned so far."""
        try:
            brief = await self.service.brief(self.run_id)
        except RefinementRefused as exc:
            return _content(str(exc), is_error=True)
        return _content(brief.render())

    async def record(self, hook_input: Mapping[str, Any]) -> dict[str, Any]:
        """Append one `PostToolUse` event to this run's trace (Invariant 2)."""
        shown = json.dumps(hook_input.get("tool_input", {}), sort_keys=True)[
            :DETAIL_CHARS
        ]
        self.trace.append(
            ToolCall(
                tool=str(hook_input.get("tool_name", "")),
                ts=time.time(),
                detail=f"{hook_input.get('duration_ms', 0)} ms; {shown}",
            )
        )
        return {}


def _is_init(message: Any) -> bool:
    return getattr(message, "subtype", None) == "init" and hasattr(message, "data")


def _is_result(message: Any) -> bool:
    return hasattr(message, "subtype") and hasattr(message, "num_turns")


def _is_assistant(message: Any) -> bool:
    return hasattr(message, "content") and hasattr(message, "model")


def _is_rate_limit(message: Any) -> bool:
    return hasattr(message, "rate_limit_info")


def rate_limited(message: Any) -> str | None:
    """Why a rate limit stopped this run, or ``None`` when it did not."""
    if not _is_rate_limit(message):
        return None
    info = message.rate_limit_info
    if getattr(info, "status", None) != "rejected":
        return None
    return f"paused:ratelimit until {getattr(info, 'resets_at', None)}"


def run_answer(raw: Any) -> RunAnswer | None:
    """The structured answer, or ``None`` when the run produced none the schema accepts.

    A malformed `output_format` drops the flag silently and the run still "succeeds" (spike A.6),
    so the answer is what says the run really finished.
    """
    if raw is None:
        return None
    try:
        return RunAnswer.model_validate(raw)
    except ValidationError:
        return None


def _added(entry: Mapping[str, Any], *keys: str) -> int:
    """The named counts added up, reading a key the CLI did not report as zero."""
    return sum(int(entry.get(key, 0) or 0) for key in keys)


def run_usage(message: Any) -> RunUsage:
    """What the result says the run cost. ``model_usage`` is the coherent pair: its ``costUSD``
    sums to ``total_cost_usd`` exactly, while ``usage`` disagrees with both (spike 2).

    Cached tokens count as input: a run that read its context from cache was charged for it, and
    a count that drops them under-reports every cached run.
    """
    per_model = getattr(message, "model_usage", None) or {}
    if per_model:
        entries = list(per_model.values())
        tokens = (
            sum(
                _added(
                    e, "inputTokens", "cacheCreationInputTokens", "cacheReadInputTokens"
                )
                for e in entries
            ),
            sum(_added(e, "outputTokens") for e in entries),
        )
    else:  # only when the CLI reported no per-model breakdown at all
        raw = getattr(message, "usage", None) or {}
        tokens = (
            _added(
                raw,
                "input_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            ),
            _added(raw, "output_tokens"),
        )
    return RunUsage(
        cost_usd=float(getattr(message, "total_cost_usd", None) or 0.0),
        cost_estimated=False,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        num_turns=int(getattr(message, "num_turns", 0) or 0),
    )


def from_result(
    message: Any, *, session_id: str | None, trace: Sequence[ToolCall] = ()
) -> RunOutcome:
    """The result message as a terminal state, per spec 5.3's mapping table.

    A ``success`` carrying ``is_error`` is a failure the subtype does not admit to, so the HTTP
    status the SDK puts beside it is what the row records.
    """
    attribution = RunAttribution(
        usage=run_usage(message),
        tool_trace=tuple(trace),
        sdk_session_id=session_id or getattr(message, "session_id", None),
    )
    subtype = str(getattr(message, "subtype", ""))
    errors = "; ".join(getattr(message, "errors", None) or ())
    if subtype == "success":
        if getattr(message, "is_error", False):
            status = getattr(message, "api_error_status", None)
            return RunOutcome.of(
                RunStatus.FAILED,
                error=f"the api failed with api_error_status {status}",
                attribution=attribution,
            )
        answer = run_answer(getattr(message, "structured_output", None))
        if answer is None:
            return RunOutcome.of(
                RunStatus.FAILED, error="no structured answer", attribution=attribution
            )
        return RunOutcome.of(
            RunStatus.SUCCEEDED, summary=answer.summary, attribution=attribution
        )
    status = RunStatus.ABORTED if subtype in CAPPED_SUBTYPES else RunStatus.FAILED
    return RunOutcome.of(
        status,
        error=f"{subtype}: {errors}" if errors else subtype,
        attribution=attribution,
    )


class Conversation(BaseModel):
    """One conversation with a client, and what the runner has learned from it so far.

    A mutable aggregate like `BoundTools`: the init message fills in ``started`` and the session
    id, and every stop after it reads them. Handing those through the loop as parameters is what
    this replaces.
    """

    model_config = ConfigDict(frozen=False, arbitrary_types_allowed=True)

    options: SdkOptions
    tools: BoundTools
    session_id: str | None = None
    started: bool = False

    def handle(self, message: Any) -> RunOutcome | None:
        """The terminal state this message ends the run in, or ``None`` to keep reading."""
        if _is_result(message):
            if not self.started:
                return self.stopped(
                    RunStatus.FAILED, "the run answered before it started"
                )
            return from_result(
                message, session_id=self.session_id, trace=self.tools.trace
            )
        paused = rate_limited(message)
        if paused is not None:
            return self.stopped(RunStatus.ABORTED, paused)
        if _is_assistant(message):
            return self._assistant(message)
        if not self.started and _is_init(message):
            return self._init(message)
        return None

    def stopped(self, status: RunStatus, reason: str) -> RunOutcome:
        """A run that ended before its result: the trace and the session survive, and the cost is
        marked estimated because the client never reported one."""
        return RunOutcome.of(
            status,
            error=reason,
            attribution=RunAttribution(
                usage=RunUsage(cost_estimated=True),
                tool_trace=tuple(self.tools.trace),
                sdk_session_id=self.session_id,
            ),
        )

    def _assistant(self, message: Any) -> RunOutcome | None:
        """A run that broke mid-turn, or spoke before its session was checked."""
        broke = getattr(message, "error", None)
        if broke is not None:
            status, reason = ASSISTANT_ERRORS.get(broke, (RunStatus.FAILED, str(broke)))
            return self.stopped(status, reason)
        if not self.started:
            return self.stopped(RunStatus.FAILED, "the run spoke before it started")
        return None

    def _init(self, message: Any) -> RunOutcome | None:
        """The session the CLI opened, accepted or refused by name (Invariant 4)."""
        refused = self.options.refusal(message.data)
        if refused is not None:
            return self.stopped(RunStatus.ABORTED, refused)
        self.started = True
        self.session_id = message.data.get("session_id")
        logger.info(
            "claude %s, session %s",
            message.data.get("claude_code_version"),
            self.session_id,
        )
        return None


class SdkRunner(RefinementRunner):
    """Drives one run through a Claude client (spec 9.3).

    Reads the checkout with `Read`/`Grep`/`Glob` and proposes through the in-process `graph`
    server; the init message is checked against that allow-list before a single turn is trusted.
    """

    kind: ClassVar[RunnerKind] = RunnerKind.CLAUDE

    def __init__(
        self,
        service: RefinementService,
        client_factory: ClientFactory | None,
        *,
        cli_path: Path | None = None,
        managed_settings: Path = MANAGED_SETTINGS,
    ) -> None:
        super().__init__(service, client_factory)
        self.cli_path = cli_path if cli_path is not None else claude_on_path()
        self.managed_settings = managed_settings

    async def run(self, job: RefinementJob) -> RunProduct:
        # before the row exists: a request that cannot become options must not open one to orphan
        options = SdkOptions.of(
            job, self.service.user, self.service.root, cli_path=self.cli_path
        )
        refused = self._managed_hooks()
        run, brief = await self._open(job)
        if refused is not None:
            return await self._close(
                run, brief, RunOutcome.of(RunStatus.ABORTED, error=refused)
            )
        talk = Conversation(
            options=options, tools=BoundTools(service=self.service, run_id=run.run_id)
        )
        try:
            outcome = await self._converse(talk, brief)
        except asyncio.CancelledError:
            # the caller going away must not leave the row queued and its slot held
            await self._close(run, brief, talk.stopped(RunStatus.ABORTED, "cancelled"))
            raise
        return await self._close(run, brief, outcome)

    def _managed_hooks(self) -> str | None:
        """Why a managed-settings file forbids this run, or ``None``.

        Read as a file because `system/init` reports no hooks at all (spike consequences 3), and
        `setting_sources=[]` cannot switch this tier off.
        """
        if not self.managed_settings.is_file():
            return None
        try:
            declared = json.loads(self.managed_settings.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return f"refused: {self.managed_settings} cannot be read ({exc})"
        if isinstance(declared, dict) and declared.get("hooks"):
            return f"refused: {self.managed_settings} declares hooks"
        return None

    async def _converse(self, talk: Conversation, brief: Brief) -> RunOutcome:
        """One conversation, from the first message to the result, mapped to a terminal state.

        Rendering the brief is inside the handler too, so every step that can fail once the row
        exists lands as a closed run. Every exception is caught, the SDK's own classes included,
        which this module cannot name; a cancellation is not one it owns and goes back up to
        `run`, which closes the row before letting it on.
        """
        try:
            factory = self._factory()
            async with factory(talk.options, talk.tools) as client:
                await client.query(brief.render())
                async for message in client.receive_response():
                    ended = talk.handle(message)
                    if ended is not None:
                        return ended
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the SDK's classes cannot be named here
            return talk.stopped(RunStatus.FAILED, str(exc))
        return talk.stopped(RunStatus.FAILED, "the run ended without a result")

    def _factory(self) -> ClientFactory:
        """The injected factory, refusing a runner built without one before any run happens."""
        if self.client_factory is None:
            raise SdkClientError(
                "this runner was built with no client factory",
                kind=SdkErrorKind.NOT_FOUND,
            )
        return self.client_factory
