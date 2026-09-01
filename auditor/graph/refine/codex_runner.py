"""The Codex runner, and everything about it that needs no SDK (spec 9.3, spec 19).

Free of `openai_codex` on purpose, for the same reason `sdk_runner.py` is free of the Claude SDK:
CI never installs the extra, so the option set, the turn mapping and the ceilings are only
testable if they live here. `codex_client.py` is the one module that builds a real client.
"""

import asyncio
import json
import logging
import time
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from auditor.graph.refine.brief import Brief
from auditor.graph.refine.client import CodexFactory
from auditor.graph.refine.codex_home import (
    MANAGED_FILES,
    codex_home_dir,
    user_codex_home,
)
from auditor.graph.refine.models import (
    Proposer,
    RunAttribution,
    RunnerKind,
    RunOutcome,
    RunStatus,
    RunUsage,
    ToolCall,
)
from auditor.graph.refine.prices import estimate, price_for
from auditor.graph.refine.prompts import (
    GRAPH_SERVER,
    RUN_ANSWER_SCHEMA,
    SYSTEM_PROMPT,
)
from auditor.graph.refine.runner import RefinementJob, RefinementRunner, RunProduct
from auditor.graph.refine.sdk_runner import DETAIL_CHARS, BoundTools, run_answer
from auditor.graph.refine.service import RefinementService
from auditor.user_settings import UserSettings

logger = logging.getLogger(__name__)

#: the values that can hold exactly one thing, so nothing else can be expressed (Invariant 4).
#: `approval_mode` is explicit because the SDK's own default is `auto_review`, which would fail
#: Invariant 4 open.
EFFORT = "low"
SANDBOX = "read_only"
APPROVAL_MODE = "deny_all"
EPHEMERAL = True
#: one `thread.run` per run: `TurnStartParams` carries no `max_turns`, no budget and no timeout,
#: so the only turn ceiling Codex has is the number of calls this runner makes
CALLS_PER_RUN = 1
#: what a managed file above `CODEX_HOME` has to declare before it refuses a run. Content, not
#: existence: the Claude twin refuses only a managed file that declares hooks (`sdk_runner.py`).
MANAGED_KEYS = ("hooks", "mcp_servers", "mcpServers")
#: what a failed turn's message has to contain to become one of spec 8.4's pauses. Ordered, and
#: matched on the message alone: `TurnError` carries no code the loop can switch on.
TURN_ERRORS: tuple[tuple[str, RunStatus, str], ...] = (
    ("unauthorized", RunStatus.FAILED, "paused:auth"),
    ("not logged in", RunStatus.FAILED, "paused:auth"),
    ("401", RunStatus.FAILED, "paused:auth"),
    ("rate limit", RunStatus.ABORTED, "paused:ratelimit"),
    ("429", RunStatus.ABORTED, "paused:ratelimit"),
    ("quota", RunStatus.ABORTED, "paused:billing"),
    ("billing", RunStatus.ABORTED, "paused:billing"),
)
#: the turn statuses that mean the run stopped for a reason outside itself
STOPPED_STATUSES = frozenset({"interrupted"})


def managed_refusal(paths: Sequence[Path] = MANAGED_FILES) -> str | None:
    """Why a settings tier above `CODEX_HOME` forbids this run, or ``None`` (Invariant 4).

    `/etc/codex/*` is read before the private home and cannot be switched off from it, so it is
    read as a file the way the Claude runner reads its managed settings: what it declares refuses
    the run, not that it exists, or a managed machine could never run Codex at all.
    """
    for path in paths:
        if not path.is_file():
            continue
        try:
            body = _parsed(path)
        except (OSError, ValueError) as exc:
            return f"refused: {path} sits above CODEX_HOME and cannot be read ({exc})"
        declared = [key for key in MANAGED_KEYS if body.get(key)]
        if declared:
            return (
                f"refused: {path} sits above CODEX_HOME and declares "
                f"{', '.join(declared)}"
            )
    return None


def _parsed(path: Path) -> Mapping[str, Any]:
    """One managed file as a mapping, whichever of the two formats it is written in."""
    text = path.read_text(encoding="utf-8")
    body = tomllib.loads(text) if path.suffix == ".toml" else json.loads(text)
    return body if isinstance(body, dict) else {}


class CodexClientError(Exception):
    """Anything the Codex client raised, in a type this SDK-free module can name."""


class RateLimit(BaseModel):
    """What `account/rateLimits/read` said, in this repo's own shape (spec 8.4).

    `RateLimitSnapshot.primary` is optional and `used_percent` carries no range constraint, so
    the translation happens once, in `codex_client.py`, and never in the loop.
    """

    model_config = ConfigDict(frozen=True)

    used_percent: float
    resets_at: float | None = None


class CodexOptions(BaseModel):
    """Everything about one Codex run that varies. What cannot vary is a constant above."""

    model_config = ConfigDict(frozen=True)

    model: str
    cwd: Path
    home: Path
    auth: Path
    system_prompt: str
    output_schema: dict[str, Any]
    max_budget_usd: float
    max_utilization: float = 1.0

    @classmethod
    def of(
        cls,
        job: RefinementJob,
        user: UserSettings,
        root: Path,
        *,
        home: Path,
        auth: Path,
    ) -> "CodexOptions":
        """One run's options from the job and this user's configured limits.

        The model is the user's `codex_model`, never `job.model`: `RefinementJob.model` is typed
        `ClaudeModel`, so no surface can put a Codex model on a job (spec 14).
        """
        return cls(
            model=user.observer.runner.codex_model,
            cwd=root,
            home=home,
            auth=auth,
            system_prompt=SYSTEM_PROMPT,
            output_schema=dict(RUN_ANSWER_SCHEMA),
            max_budget_usd=user.observer.budget.max_budget_usd_per_run,
            max_utilization=user.observer.budget.max_utilization,
        )

    def refusal(self, servers: Sequence[str]) -> str | None:
        """Why the session the binary opened is not the one that was asked for (Invariant 4).

        Read from `mcpServerStatus/list`, which is the Codex twin of the Claude CLI's own
        `system/init` answer: the private `config.toml` is what we wrote, but `-c` overrides merge
        with the user's servers and `/etc/codex/*` sits above the home.
        """
        names = set(servers)
        if not names:
            return "refused: no mcp servers, so there is no graph server to propose through"
        if names != {GRAPH_SERVER}:
            return f"refused: unexpected mcp servers {sorted(names)}"
        return None


def codex_usage(turn: Any, *, calls: int = CALLS_PER_RUN) -> RunUsage:
    """What the turn says it spent, in tokens. Dollars are derived later and never reported.

    `usage` is ``None`` until a token-usage notification arrives, and `usage.total` is the thread
    total while `last` is this turn's; one call per run makes them agree today, and the total is
    the one that stays right when that changes. Cached tokens count as input, the same rule the
    Claude runner uses: a run that read its context from cache was charged for it.
    """
    usage = getattr(turn, "usage", None)
    total = getattr(usage, "total", None) if usage is not None else None
    if total is None:
        return RunUsage(num_turns=calls, cost_estimated=True)
    return RunUsage(
        input_tokens=_added(
            total, "input_tokens", "cached_input_tokens", "cache_write_input_tokens"
        ),
        output_tokens=_added(total, "output_tokens", "reasoning_output_tokens"),
        num_turns=calls,
        cost_estimated=True,
    )


def _added(breakdown: Any, *names: str) -> int:
    """The named counts added up, reading one the SDK left unset as zero."""
    return sum(int(getattr(breakdown, name, 0) or 0) for name in names)


def tool_trace(items: Sequence[Any]) -> tuple[ToolCall, ...]:
    """The MCP calls and shell commands this turn made, as trace rows (Invariant 2).

    `TurnResult.items` are root models over a seventeen member union, so every entry is unwrapped
    through `.root` before it is asked what it is.
    """
    trace: list[ToolCall] = []
    for item in items:
        inner = getattr(item, "root", item)
        kind = getattr(inner, "type", "")
        if kind == "mcpToolCall":
            name = f"mcp__{getattr(inner, 'server', '')}__{getattr(inner, 'tool', '')}"
            shown = json.dumps(
                getattr(inner, "arguments", None), sort_keys=True, default=str
            )
        elif kind == "commandExecution":
            name = "Bash"
            shown = str(getattr(inner, "command", ""))
        else:
            continue
        trace.append(
            ToolCall(
                tool=name,
                ts=time.time(),
                detail=f"{getattr(inner, 'duration_ms', 0) or 0} ms; {shown[:DETAIL_CHARS]}",
            )
        )
    return tuple(trace)


def paused_by(message: str) -> tuple[RunStatus, str] | None:
    """The pause a failed turn's message asks for, or ``None`` when it asks for none."""
    low = message.lower()
    return next(
        ((status, word) for needle, status, word in TURN_ERRORS if needle in low), None
    )


def rate_limited(
    limit: RateLimit | None, *, max_utilization: float = 1.0
) -> str | None:
    """Why the account's rate limit stops this run, or ``None`` when it does not (spec 8.4).

    `used_percent` is an integer percentage with no declared range, and `max_utilization` is a
    0-1 fraction, so the comparison happens on one scale here rather than at each caller. The
    sentence is the same shape the Claude runner writes, because `scheduling._resets_at` parses
    it back.
    """
    if limit is None:
        return None
    if limit.used_percent < max_utilization * 100.0:
        return None
    return f"paused:ratelimit until {limit.resets_at}"


def from_turn(
    turn: Any,
    *,
    options: CodexOptions,
    prices: Mapping[str, Any],
    thread_id: str | None,
    trace: Sequence[ToolCall] = (),
) -> RunOutcome:
    """One turn as a terminal state, with its estimated cost and its own ceiling applied.

    A failed turn raises inside the SDK and is turned back into a `TurnResult` at the adapter
    edge, so this stays one switch over a status rather than two paths.
    """
    usage = estimate(codex_usage(turn), price_for(options.model, prices))
    attribution = RunAttribution(
        usage=usage, tool_trace=tuple(trace), sdk_session_id=thread_id
    )
    status = str(getattr(turn, "status", "") or "")
    status = getattr(status, "value", status)
    if status != "completed":
        message = str(getattr(getattr(turn, "error", None), "message", "") or status)
        paused = paused_by(message)
        if paused is not None:
            return RunOutcome.of(paused[0], error=paused[1], attribution=attribution)
        stopped = RunStatus.ABORTED if status in STOPPED_STATUSES else RunStatus.FAILED
        return RunOutcome.of(
            stopped, error=f"{status}: {message}", attribution=attribution
        )
    answer = run_answer(_structured(turn))
    if answer is None:
        return RunOutcome.of(
            RunStatus.FAILED, error="no structured answer", attribution=attribution
        )
    over = _over_budget(usage, options)
    if over is not None:
        return RunOutcome.of(RunStatus.ABORTED, error=over, attribution=attribution)
    return RunOutcome.of(
        RunStatus.SUCCEEDED, summary=answer.summary, attribution=attribution
    )


def _structured(turn: Any) -> Any:
    """The JSON the run answered with, out of `final_response`.

    The SDK passes `output_schema` to the binary and validates nothing on the way back, so an
    answer that is not the schema's is read as no answer at all.
    """
    raw = getattr(turn, "final_response", None)
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _over_budget(usage: RunUsage, options: CodexOptions) -> str | None:
    """Whether the estimate for this run passed the per-run ceiling (spec 8.4).

    Checked after the turn rather than passed into it: `TurnStartParams` has no budget field at
    all, so a Codex run can only be stopped once, when its cost is already known.
    """
    if usage.cost_usd <= options.max_budget_usd:
        return None
    return f"over_budget: {usage.cost_usd:.4f} usd over {options.max_budget_usd:.4f}"


class CodexRunner(RefinementRunner):
    """Drives one run through a Codex client (spec 9.3, spec 19).

    Reads the checkout under a read-only sandbox and proposes through the loopback `graph` shim
    this process owns, so an eval's judge is injectable the way it is for the Claude runner.
    """

    kind: ClassVar[RunnerKind] = RunnerKind.CODEX

    def __init__(
        self,
        service: RefinementService,
        client_factory: CodexFactory | None,
        *,
        proposer: Proposer | None = None,
        home: Path | None = None,
        auth: Path | None = None,
        managed_settings: Sequence[Path] = MANAGED_FILES,
    ) -> None:
        super().__init__(service, client_factory, proposer=proposer)
        self.home = home if home is not None else codex_home_dir()
        self.auth = auth if auth is not None else user_codex_home() / "auth.json"
        self.managed_settings = managed_settings

    async def run(self, job: RefinementJob) -> RunProduct:
        # before the row exists: a request that cannot become options must not open one to orphan
        options = CodexOptions.of(
            job, self.service.user, self.service.root, home=self.home, auth=self.auth
        )
        refused = managed_refusal(self.managed_settings)
        run, brief = await self._open(job)
        if refused is not None:
            return await self._close(
                run, brief, RunOutcome.of(RunStatus.ABORTED, error=refused)
            )
        tools = BoundTools(
            service=self.service, run_id=run.run_id, proposer=self.proposer
        )
        try:
            outcome = await self._converse(options, tools, brief)
        except asyncio.CancelledError:
            await self._close(
                run, brief, self._stopped(tools, RunStatus.ABORTED, "cancelled")
            )
            raise
        return await self._close(run, brief, outcome)

    async def _converse(
        self, options: CodexOptions, tools: BoundTools, brief: Brief
    ) -> RunOutcome:
        """One turn, from the shim coming up to the terminal state the turn maps to.

        Every exception is caught, the SDK's own classes included, which this module cannot name;
        a cancellation is not one it owns and goes back up to `run`, which closes the row first.
        """
        prices = self.service.user.observer.runner.codex_prices
        try:
            async with self._factory()(options, tools) as client:
                denied = options.refusal(await client.servers())
                if denied is not None:
                    return self._stopped(tools, RunStatus.ABORTED, denied)
                paused = rate_limited(
                    await client.rate_limit(), max_utilization=options.max_utilization
                )
                if paused is not None:
                    return self._stopped(tools, RunStatus.ABORTED, paused)
                thread = await client.thread_start(options)
                turn = await thread.run(brief.render())
                return from_turn(
                    turn,
                    options=options,
                    prices=prices,
                    thread_id=getattr(thread, "id", None),
                    trace=tools.trace + list(tool_trace(getattr(turn, "items", ()))),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the SDK's classes cannot be named here
            return self._stopped(tools, RunStatus.FAILED, str(exc))

    def _stopped(self, tools: BoundTools, status: RunStatus, reason: str) -> RunOutcome:
        """A run that ended before its turn: the trace survives and the cost stays estimated."""
        return RunOutcome.of(
            status,
            error=reason,
            attribution=RunAttribution(
                usage=RunUsage(cost_estimated=True),
                tool_trace=tuple(tools.trace),
            ),
        )

    def _factory(self) -> CodexFactory:
        """The injected factory, refusing a runner built without one before any run happens."""
        if self.client_factory is None:
            raise CodexClientError("this runner was built with no client factory")
        return self.client_factory
