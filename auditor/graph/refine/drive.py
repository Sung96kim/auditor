"""Which runner drives a run, and the one call both surfaces make (spec 9.3, 9.5, 12.2).

The CLI and the MCP tool import this module and nothing else from the runner half, so neither can
drift on the choice logic or on the payload. This is also the only place that reaches
`sdk_client.py`, behind the `observer-claude` guard.
"""

import os
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from auditor.config import AuditorSettings
from auditor.database import IndexStore
from auditor.graph.refine.client import ClientFactory, CodexFactory
from auditor.graph.refine.codex_home import user_codex_home
from auditor.graph.refine.codex_runner import CodexRunner
from auditor.graph.refine.eval import EvalRun
from auditor.graph.refine.models import (
    EvalSuite,
    Proposer,
    RunnerChoice,
    RunnerChoiceCode,
    RunnerKind,
)
from auditor.graph.refine.payloads import (
    BriefPayload,
    EvalPlan,
    EvalReport,
    RefinePayload,
)
from auditor.graph.refine.runner import (
    FakeRunner,
    RefinementJob,
    RefinementRunner,
    RunnerUnavailable,
)
from auditor.graph.refine.sdk_runner import SdkRunner
from auditor.graph.refine.service import RefinementRefused, RefinementService
from auditor.graph.refine.tiers import eval_model
from auditor.user_settings import Runner, RunnerConfig, UserSettings

# the [observer-claude] extra; `exc.name` is the package either way, so a findable one means drift
try:
    from auditor.graph.refine.sdk_client import claude_client

    SDK_AVAILABLE = True
except ImportError as exc:
    if exc.name != "claude_agent_sdk" or find_spec("claude_agent_sdk") is not None:
        raise
    SDK_AVAILABLE = False

#: what either surface answers a caller with rather than a traceback: no runner, a service that
#: refused, or a scope that could never name a node here
REFINE_ERRORS = (RefinementRefused, RunnerUnavailable, ValueError)

#: the one refusal both the chooser and the builder give, so the fix is worded once
NEEDS_EXTRA = (
    "the Claude runner needs the observer-claude extra: "
    "pip install 'auditr[observer-claude]'"
)
NEEDS_CODEX_EXTRA = (
    "the Codex runner needs the observer-codex extra: "
    "pip install 'auditr[observer-codex]'"
)
NEEDS_EITHER = (
    "no runner is installed: pip install 'auditr[observer]' for both, "
    "or one of observer-claude and observer-codex"
)
#: what `auto` says when it steps down to Codex: the cost model changes with the runner, and a
#: refusal that had to be read out of prose is what `RunnerChoice.detail` exists to prevent
FELL_BACK = "no Claude runner here, so Codex drives and costs are estimated: "
#: presence, not import: `import openai_codex` costs 0.67-0.76 s cold, 530 ms of it in one
#: generated module, and this module is on the daemon's and every `graph refine` import path
CODEX_AVAILABLE = find_spec("openai_codex") is not None
#: the env vars that stand in for a Claude credential file; Codex has no such pair
CLAUDE_TOKENS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")

RUNNERS: dict[RunnerKind, type[RefinementRunner]] = {
    RunnerKind.FAKE: FakeRunner,
    RunnerKind.CLAUDE: SdkRunner,
    RunnerKind.CODEX: CodexRunner,
}


def auth_hinted(env: Mapping[str, str] = os.environ, home: Path | None = None) -> bool:
    """Whether this machine looks logged in to Claude. ``home`` is the user's, not `$AUDITOR_HOME`."""
    credential = (home or Path.home()) / ".claude" / ".credentials.json"
    return _hinted(credential, CLAUDE_TOKENS, env)


def codex_auth_hinted(env: Mapping[str, str] = os.environ) -> bool:
    """Whether this machine looks logged in to Codex, reading `CODEX_HOME` first."""
    return _hinted(user_codex_home(env) / "auth.json", (), env)


def _hinted(credential: Path, tokens: Sequence[str], env: Mapping[str, str]) -> bool:
    """One credential hint: a token in the environment, or the CLI's own credential file.

    A hint, not a check: no auth RPC exists without a run, so a real failure is mapped from the
    run's own first messages instead. Both runners read it through here so neither can drift.
    """
    return any(env.get(name) for name in tokens) or credential.is_file()


def select_runner(
    config: RunnerConfig,
    *,
    requested: Runner | None = None,
    sdk_available: bool | None = None,
    auth_hint: bool | None = None,
    codex_available: bool | None = None,
    codex_auth_hint: bool | None = None,
) -> RunnerChoice:
    """Which runner drives this request, or why none can (spec 9.3).

    ``auto`` takes Claude, then Codex, then the most actionable refusal it reached. All four
    flags resolve inside the body, never as defaults: a default binds by value at import and
    would *call* the hint once, for the life of the process.
    """
    claude = _claude_choice(
        SDK_AVAILABLE if sdk_available is None else sdk_available,
        auth_hinted() if auth_hint is None else auth_hint,
    )
    codex = _codex_choice(
        CODEX_AVAILABLE if codex_available is None else codex_available,
        codex_auth_hinted() if codex_auth_hint is None else codex_auth_hint,
    )
    wanted = requested or config.agent
    if wanted == "claude":
        return claude
    if wanted == "codex":
        return codex
    if claude.kind is not None:
        return claude
    if codex.kind is not None:
        return codex.model_copy(update={"detail": FELL_BACK + codex.detail})
    # neither: an installed runner that is only logged out is the one worth naming, and with
    # nothing installed at all the fix is the extra that brings both (spec 9.3)
    if claude.code is RunnerChoiceCode.PAUSED_AUTH:
        return claude
    if codex.code is RunnerChoiceCode.PAUSED_AUTH:
        return codex
    return RunnerChoice(code=RunnerChoiceCode.UNAVAILABLE_NONE, detail=NEEDS_EITHER)


def _claude_choice(available: bool, logged_in: bool) -> RunnerChoice:
    """The Claude arm of spec 9.3's ladder: installed, then logged in, then chosen."""
    if not available:
        return RunnerChoice(code=RunnerChoiceCode.UNAVAILABLE_SDK, detail=NEEDS_EXTRA)
    if not logged_in:
        return RunnerChoice(
            code=RunnerChoiceCode.PAUSED_AUTH,
            detail="no Claude credentials found: run `claude` once to log in, "
            "or set ANTHROPIC_API_KEY",
        )
    return RunnerChoice(code=RunnerChoiceCode.CLAUDE, detail="the Claude SDK runner")


def _codex_choice(available: bool, logged_in: bool) -> RunnerChoice:
    """The Codex arm of the same ladder, refusal wording included."""
    if not available:
        return RunnerChoice(
            code=RunnerChoiceCode.UNAVAILABLE_CODEX, detail=NEEDS_CODEX_EXTRA
        )
    if not logged_in:
        return RunnerChoice(
            code=RunnerChoiceCode.PAUSED_AUTH,
            detail="no Codex credentials found: run `codex` once to log in",
        )
    return RunnerChoice(code=RunnerChoiceCode.CODEX, detail="the Codex runner")


def build_runner(
    kind: RunnerKind,
    service: RefinementService,
    *,
    client_factory: ClientFactory | CodexFactory | None = None,
    proposer: Proposer | None = None,
) -> RefinementRunner:
    """One runner of the given kind, with its client injected or built here."""
    runner = RUNNERS[kind]
    return runner(
        service, client_factory or _default_factory(runner), proposer=proposer
    )


def _default_factory(
    runner: type[RefinementRunner],
) -> ClientFactory | CodexFactory | None:
    """The client this runner talks through when the caller injected none.

    Keyed on the class, not the kind: a test that registers a fake under `claude` wants the choice
    logic, not the SDK.
    """
    if issubclass(runner, CodexRunner):
        if not CODEX_AVAILABLE:
            raise RunnerUnavailable(NEEDS_CODEX_EXTRA)
        return _codex_backend().CodexRunSession
    if not issubclass(runner, SdkRunner):
        return None
    if not SDK_AVAILABLE:
        raise RunnerUnavailable(NEEDS_EXTRA)
    return claude_client


def _codex_backend() -> Any:
    """The SDK-bound Codex module, loaded when a Codex run is actually being built.

    Loaded here rather than imported at module scope, and by name rather than by statement, for
    the reason `CODEX_AVAILABLE` is a `find_spec`: the import costs most of a second and every
    caller of this module would pay it (recon Q7).
    """
    return import_module("auditor.graph.refine.codex_client")


async def refine(
    index: IndexStore,
    root: Path,
    settings: AuditorSettings,
    user: UserSettings,
    *,
    job: RefinementJob,
    client_factory: ClientFactory | CodexFactory | None = None,
) -> RefinePayload:
    """Run one model-driven refinement and report what it did.

    The one call both surfaces make, so the CLI and the MCP tool cannot drift on the choice logic
    or on the payload. The runner asked for travels on the job, which is where pydantic already
    refused a value neither surface should have accepted.

    Raises:
        RunnerUnavailable: no runner can drive this request, with the reason in the message.
    """
    choice = select_runner(user.observer.runner, requested=job.runner)
    if choice.kind is None:
        raise RunnerUnavailable(choice.detail)
    service = RefinementService(index, root, settings, user)
    runner = build_runner(choice.kind, service, client_factory=client_factory)
    product = await runner.run(job)
    return RefinePayload.of(
        await service.status(product.run.run_id),
        product.brief,
        product.landed,
        choice.code,
    )


async def brief(
    index: IndexStore,
    root: Path,
    settings: AuditorSettings,
    user: UserSettings,
    scope: str,
) -> BriefPayload:
    """Render the brief a run over ``scope`` would be given, without opening one.

    Raises:
        ValueError: the scope could never name a node in this checkout.
    """
    service = RefinementService(index, root, settings, user)
    return BriefPayload.of(await service.preview(scope))


async def evaluate(
    index: IndexStore,
    root: Path,
    settings: AuditorSettings,
    user: UserSettings,
    *,
    job: RefinementJob,
    suites: Sequence[EvalSuite],
    size: int,
    seed: int,
    dry_run: bool = False,
    on_plan: Callable[[EvalPlan], None] | None = None,
    client_factory: ClientFactory | CodexFactory | None = None,
) -> EvalReport:
    """Measure this checkout's accuracy for one runner and model, and store what it measured.

    ``job`` carries the runner and model the way `refine` takes them, so the two commands cannot
    drift on the choice logic. ``on_plan`` is called with the plan before the first run opens.

    Raises:
        RunnerUnavailable: no runner can drive this request, with the reason in the message.
    """
    choice = select_runner(user.observer.runner, requested=job.runner)
    if choice.kind is None:
        raise RunnerUnavailable(choice.detail)
    run = EvalRun(
        service=RefinementService(index, root, settings, user),
        build=partial(_eval_runner, choice.kind, client_factory),
        runner=choice.kind,
        # the effective model, which is what `_open` stamps on the run row and the gate reads
        # back, resolved per runner so a Codex eval is not filed under a Claude model
        model=eval_model(choice.kind, user.observer.runner, job.model),
        size=size,
        seed=seed,
        on_plan=on_plan,
    )
    return await run.report(suites, dry_run=dry_run)


def _eval_runner(
    kind: RunnerKind,
    client_factory: ClientFactory | CodexFactory | None,
    service: RefinementService,
    proposer: Proposer,
) -> RefinementRunner:
    """One eval batch's runner: the kind this invocation chose, over that batch's masked queue."""
    return build_runner(kind, service, client_factory=client_factory, proposer=proposer)
