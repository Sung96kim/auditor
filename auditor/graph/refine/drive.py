"""Which runner drives a run, and the one call both surfaces make (spec 9.3, 9.5, 12.2).

The CLI and the MCP tool import this module and nothing else from the runner half, so neither can
drift on the choice logic or on the payload. This is also the only place that reaches
`sdk_client.py`, behind the `observer-claude` guard.
"""

import os
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from auditor.graph.refine.models import RunnerKind
from auditor.graph.refine.runner import (
    FakeRunner,
    RefinementRunner,
    RunnerUnavailable,
)
from auditor.graph.refine.sdk_runner import ClientFactory, SdkRunner
from auditor.graph.refine.service import RefinementService
from auditor.user_settings import Runner, RunnerConfig

# the [observer-claude] extra; a genuine ImportError inside `sdk_client` is not swallowed
try:
    from auditor.graph.refine.sdk_client import SdkClientFactory

    SDK_AVAILABLE = True
except ImportError as exc:
    if exc.name != "claude_agent_sdk":
        raise
    SDK_AVAILABLE = False

#: the one refusal both the chooser and the builder give, so the fix is worded once
NEEDS_EXTRA = (
    "the Claude runner needs the observer-claude extra: "
    "pip install 'auditr[observer-claude]'"
)

RUNNERS: dict[RunnerKind, type[RefinementRunner]] = {
    RunnerKind.FAKE: FakeRunner,
    RunnerKind.CLAUDE: SdkRunner,
}


class RunnerChoiceCode(StrEnum):
    """What came of asking for a runner: one runner, or one reason there is none."""

    CLAUDE = "claude"
    PAUSED_AUTH = "paused:auth"
    UNAVAILABLE_SDK = "unavailable:sdk"
    UNAVAILABLE_CODEX = "unavailable:codex"


class RunnerChoice(BaseModel):
    """The runner a request resolved to, the machine code for it, and the sentence a human reads."""

    model_config = ConfigDict(frozen=True)

    kind: RunnerKind | None
    code: RunnerChoiceCode
    detail: str = ""


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
            kind=None,
            code=RunnerChoiceCode.UNAVAILABLE_CODEX,
            detail="the Codex runner lands in S12; use --runner claude",
        )
    if not has_sdk:
        return RunnerChoice(
            kind=None,
            code=RunnerChoiceCode.UNAVAILABLE_SDK,
            detail=NEEDS_EXTRA,
        )
    if not logged_in:
        return RunnerChoice(
            kind=None,
            code=RunnerChoiceCode.PAUSED_AUTH,
            detail="no Claude credentials found: run `claude` once to log in, "
            "or set ANTHROPIC_API_KEY",
        )
    return RunnerChoice(
        kind=RunnerKind.CLAUDE,
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
    return RUNNERS[kind](service, client_factory or _default_factory(kind))


def _default_factory(kind: RunnerKind) -> ClientFactory | None:
    """The client a runner of this kind talks through when the caller injected none."""
    if kind is not RunnerKind.CLAUDE:
        return None
    if not SDK_AVAILABLE:
        raise RunnerUnavailable(NEEDS_EXTRA)
    return SdkClientFactory()
