"""The Claude runner, and everything about it that needs no SDK (spec 9.3, 9.5).

Free of `claude_agent_sdk` on purpose: CI never installs that extra, so the message loop, the
init check and the outcome mapping are only testable if they live here. The client arrives through
an injected factory that answers to `ClientSession`; `sdk_client.py` is the one that builds a real
one.
"""

import json
import logging
import shutil
import time
from collections.abc import AsyncIterator, Callable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from auditor.graph.refine.models import (
    Proposal,
    RunAttribution,
    RunnerKind,
    RunStatus,
    RunUsage,
    ToolCall,
)
from auditor.graph.refine.prompts import (
    ALLOWED_TOOLS,
    GRAPH_SERVER,
    MODEL_TOOLS,
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


def _content(text: str, *, is_error: bool = False) -> dict[str, Any]:
    """One in-process tool's answer in the shape the SDK's `@tool` contract asks for."""
    answer: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        answer["is_error"] = True
    return answer


class BoundTools(BaseModel):
    """The two in-process tools one run exposes, bound to that run, plus the trace they leave.

    A mutable aggregate like `StagedRun`: it is filled in as the run proceeds and read when it
    ends. The trace is one thing owned by one object, so the `PostToolUse` sink is a method here
    rather than a second thing the factory has to be handed.
    """

    model_config = ConfigDict(frozen=False, arbitrary_types_allowed=True)

    #: registered verbatim: `Proposal` already generates this, and the flat MCP shape would be a
    #: fourth hand-maintained copy of one schema
    INPUT_SCHEMAS: ClassVar[dict[str, dict[str, Any]]] = {
        "propose": Proposal.model_json_schema(),
        "brief": {"type": "object", "properties": {}, "additionalProperties": False},
    }

    service: RefinementService
    run_id: str
    trace: list[ToolCall] = Field(default_factory=list)

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


class ClientSession(Protocol):
    """The four members of the SDK client this runner uses.

    A protocol rather than an ABC: the object is a third party's, and a test double must not have
    to inherit ours to stand in for it.
    """

    async def __aenter__(self) -> "ClientSession": ...

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any: ...

    async def query(self, prompt: str) -> None: ...

    #: a plain `def`: the SDK's own is an async-generator method, awaited by `async for`
    def receive_response(self) -> AsyncIterator[Any]: ...


ClientFactory = Callable[[SdkOptions, BoundTools], ClientSession]


class StreamResult(BaseModel):
    """What one conversation with the client came to, before the run is closed on it."""

    model_config = ConfigDict(frozen=True)

    status: RunStatus
    reason: str = ""
    attribution: RunAttribution = RunAttribution()


def _is_init(message: Any) -> bool:
    return getattr(message, "subtype", None) == "init" and hasattr(message, "data")


def _is_result(message: Any) -> bool:
    return hasattr(message, "subtype") and hasattr(message, "num_turns")


def _is_assistant(message: Any) -> bool:
    return hasattr(message, "content") and hasattr(message, "model")


def _is_rate_limit(message: Any) -> bool:
    return hasattr(message, "rate_limit_info")


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
        refused = self._managed_hooks()
        run, brief = await self._open(job)
        if refused is not None:
            return await self._close(
                run,
                brief,
                status=RunStatus.ABORTED,
                reason=refused,
                attribution=RunAttribution(),
            )
        options = SdkOptions.of(
            job, self.service.user, self.service.root, cli_path=self.cli_path
        )
        tools = BoundTools(service=self.service, run_id=run.run_id)
        result = await self._converse(options, tools, brief.render())
        return await self._close(
            run,
            brief,
            status=result.status,
            reason=result.reason,
            attribution=result.attribution,
        )

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

    async def _converse(
        self, options: SdkOptions, tools: BoundTools, prompt: str
    ) -> StreamResult:
        """One conversation, from the first message to the result, mapped to a terminal state.

        Every exception is caught, including the SDK's own classes, which this module cannot name.
        """
        session_id: str | None = None
        started = False
        try:
            factory = self._factory()
            async with factory(options, tools) as client:
                await client.query(prompt)
                async for message in client.receive_response():
                    if _is_result(message):
                        if not started:
                            return self._stopped(
                                RunStatus.FAILED,
                                "the run answered before it started",
                                tools,
                                session_id,
                            )
                        return self._from_result(message, options, tools, session_id)
                    paused = _rate_limited(message)
                    if paused is not None:
                        return self._stopped(
                            RunStatus.ABORTED, paused, tools, session_id
                        )
                    if _is_assistant(message):
                        broke = getattr(message, "error", None)
                        if broke is not None:
                            return self._stopped(
                                RunStatus.FAILED,
                                _ERRORS.get(broke, broke),
                                tools,
                                session_id,
                            )
                        if not started:
                            return self._stopped(
                                RunStatus.FAILED,
                                "the run spoke before it started",
                                tools,
                                session_id,
                            )
                    elif not started and _is_init(message):
                        refused = self._init_refusal(message.data, options)
                        if refused is not None:
                            return self._stopped(
                                RunStatus.ABORTED, refused, tools, session_id
                            )
                        started = True
                        session_id = message.data.get("session_id")
                        logger.info(
                            "claude %s, session %s",
                            message.data.get("claude_code_version"),
                            session_id,
                        )
        except Exception as exc:  # noqa: BLE001  (the SDK's classes cannot be named here)
            return self._stopped(RunStatus.FAILED, str(exc), tools, session_id)
        return self._stopped(
            RunStatus.FAILED, "the run ended without a result", tools, session_id
        )

    def _factory(self) -> ClientFactory:
        """The injected factory, refusing a runner built without one before any run happens."""
        if self.client_factory is None:
            raise SdkClientError(
                "this runner was built with no client factory",
                kind=SdkErrorKind.NOT_FOUND,
            )
        return self.client_factory

    @staticmethod
    def _init_refusal(data: Mapping[str, Any], options: SdkOptions) -> str | None:
        """Why the session the CLI opened is not the one that was asked for (Invariant 4).

        Every clause names what it refused: a surprise here is a fact about the CLI, and a bare
        "refused" would send the reader to the source instead of the message.
        """
        servers = data.get("mcp_servers") or []
        names = {str(server.get("name")) for server in servers}
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
        if options.model not in served:
            return f"refused: model {served!r} is not {options.model}"
        return None

    def _from_result(
        self,
        message: Any,
        options: SdkOptions,
        tools: BoundTools,
        session_id: str | None,
    ) -> StreamResult:
        """The result message as a terminal state, per spec 5.3's mapping table."""
        attribution = RunAttribution(
            usage=_usage(message),
            tool_trace=tuple(tools.trace),
            sdk_session_id=session_id or getattr(message, "session_id", None),
        )
        subtype = str(getattr(message, "subtype", ""))
        errors = "; ".join(getattr(message, "errors", None) or ())
        if subtype == "success":
            answer = _answer(getattr(message, "structured_output", None))
            if answer is None:
                return StreamResult(
                    status=RunStatus.FAILED,
                    reason="no structured answer",
                    attribution=attribution,
                )
            return StreamResult(
                status=RunStatus.SUCCEEDED,
                reason=answer.summary,
                attribution=attribution,
            )
        status = RunStatus.ABORTED if subtype in CAPPED_SUBTYPES else RunStatus.FAILED
        return StreamResult(
            status=status,
            reason=f"{subtype}: {errors}" if errors else subtype,
            attribution=attribution,
        )

    @staticmethod
    def _stopped(
        status: RunStatus, reason: str, tools: BoundTools, session_id: str | None
    ) -> StreamResult:
        """A run that ended before its result: the trace and the session survive, the cost is
        whatever the client never reported."""
        return StreamResult(
            status=status,
            reason=reason,
            attribution=RunAttribution(
                tool_trace=tuple(tools.trace), sdk_session_id=session_id
            ),
        )


#: what an assistant-level error means to the daemon, in the words S8's loop reads
_ERRORS = {"authentication_failed": "paused:auth"}


def _rate_limited(message: Any) -> str | None:
    """Why a rate limit stopped this run, or ``None`` when it did not."""
    if not _is_rate_limit(message):
        return None
    info = message.rate_limit_info
    if getattr(info, "status", None) != "rejected":
        return None
    return f"paused:ratelimit until {getattr(info, 'resets_at', None)}"


def _answer(raw: Any) -> RunAnswer | None:
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


def _usage(message: Any) -> RunUsage:
    """What the result says the run cost. ``model_usage`` is the coherent pair: its ``costUSD``
    sums to ``total_cost_usd`` exactly, while ``usage`` disagrees with both (spike 2)."""
    per_model = getattr(message, "model_usage", None) or {}
    if per_model:
        entries = list(per_model.values())
        tokens = (
            sum(int(entry.get("inputTokens", 0)) for entry in entries),
            sum(int(entry.get("outputTokens", 0)) for entry in entries),
        )
    else:  # only when the CLI reported no per-model breakdown at all
        raw = getattr(message, "usage", None) or {}
        tokens = (int(raw.get("input_tokens", 0)), int(raw.get("output_tokens", 0)))
    return RunUsage(
        cost_usd=float(getattr(message, "total_cost_usd", None) or 0.0),
        cost_estimated=False,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        num_turns=int(getattr(message, "num_turns", 0) or 0),
    )
