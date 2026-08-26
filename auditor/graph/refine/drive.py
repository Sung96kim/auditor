"""Which runner drives a run, and the one call both surfaces make (spec 9.3, 9.5, 12.2).

The CLI and the MCP tool import this module and nothing else from the runner half, so neither can
drift on the choice logic or on the payload. This is also the only place that reaches
`sdk_client.py`, behind the `observer-claude` guard.
"""

import os
from collections.abc import Mapping
from pathlib import Path

from auditor.config import AuditorSettings
from auditor.database import IndexStore
from auditor.graph.refine.client import ClientFactory
from auditor.graph.refine.models import RunnerChoice, RunnerChoiceCode, RunnerKind
from auditor.graph.refine.payloads import BriefPayload, RefinePayload
from auditor.graph.refine.runner import (
    FakeRunner,
    RefinementJob,
    RefinementRunner,
    RunnerUnavailable,
)
from auditor.graph.refine.sdk_runner import SdkRunner
from auditor.graph.refine.service import RefinementRefused, RefinementService
from auditor.user_settings import Runner, RunnerConfig, UserSettings

# the [observer-claude] extra; a genuine ImportError inside `sdk_client` is not swallowed
try:
    from auditor.graph.refine.sdk_client import claude_client

    SDK_AVAILABLE = True
except ImportError as exc:
    if exc.name != "claude_agent_sdk":
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

RUNNERS: dict[RunnerKind, type[RefinementRunner]] = {
    RunnerKind.FAKE: FakeRunner,
    RunnerKind.CLAUDE: SdkRunner,
}


def auth_hinted(env: Mapping[str, str] = os.environ, home: Path | None = None) -> bool:
    """Whether this machine looks logged in to Claude.

    A hint, not a check: no auth RPC exists without a run, so a real failure is mapped from the
    run's own first messages instead. ``home`` is the user's home, not ``$AUDITOR_HOME``.
    """
    if env.get("ANTHROPIC_API_KEY") or env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    return ((home or Path.home()) / ".claude" / ".credentials.json").exists()


def select_runner(
    config: RunnerConfig,
    *,
    requested: Runner | None = None,
    sdk_available: bool | None = None,
    auth_hint: bool | None = None,
) -> RunnerChoice:
    """Which runner drives this request, or why none can.

    ``sdk_available`` and ``auth_hint`` resolve inside the body, never as defaults: a default binds
    the flag by value at import and would *call* `auth_hinted` once, for the life of the process.
    """
    has_sdk = SDK_AVAILABLE if sdk_available is None else sdk_available
    logged_in = auth_hinted() if auth_hint is None else auth_hint
    if (requested or config.agent) == "codex":
        return RunnerChoice(
            code=RunnerChoiceCode.UNAVAILABLE_CODEX,
            detail="the Codex runner lands in S12; use --runner claude",
        )
    if not has_sdk:
        return RunnerChoice(
            code=RunnerChoiceCode.UNAVAILABLE_SDK,
            detail=NEEDS_EXTRA,
        )
    if not logged_in:
        return RunnerChoice(
            code=RunnerChoiceCode.PAUSED_AUTH,
            detail="no Claude credentials found: run `claude` once to log in, "
            "or set ANTHROPIC_API_KEY",
        )
    return RunnerChoice(
        code=RunnerChoiceCode.CLAUDE,
        detail="the Claude SDK runner",
    )


def build_runner(
    kind: RunnerKind,
    service: RefinementService,
    *,
    client_factory: ClientFactory | None = None,
) -> RefinementRunner:
    """One runner of the given kind, with its client injected or built here."""
    runner = RUNNERS[kind]
    return runner(service, client_factory or _default_factory(runner))


def _default_factory(runner: type[RefinementRunner]) -> ClientFactory | None:
    """The client this runner talks through when the caller injected none.

    Keyed on the class, not the kind: a test that registers a fake under `claude` wants the choice
    logic, not the SDK.
    """
    if not issubclass(runner, SdkRunner):
        return None
    if not SDK_AVAILABLE:
        raise RunnerUnavailable(NEEDS_EXTRA)
    return claude_client


async def refine(
    index: IndexStore,
    root: Path,
    settings: AuditorSettings,
    user: UserSettings,
    *,
    job: RefinementJob,
    client_factory: ClientFactory | None = None,
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
