"""The one module that imports `openai_codex` (spec 9.3, spec 14, spec 19).

Pure translation: a `CodexOptions` becomes a real `AsyncCodex` thread, two wrapper-less RPCs
become two typed answers, and a failed turn's `RuntimeError` becomes the `TurnResult` the SDK
would have returned. It decides nothing about what a run may be called with.
"""

import os
from typing import Any

from codex_cli_bin import bundled_codex_path, bundled_path_dir
from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
from openai_codex._run import TurnResult
from openai_codex.generated.v2_all import (
    GetAccountRateLimitsResponse,
    ListMcpServerStatusResponse,
    TurnError,
    TurnStatus,
)

from auditor.graph.refine.client import CodexSession, CodexThread, ServerStatus
from auditor.graph.refine.codex_home import BEARER_ENV, CodexHome
from auditor.graph.refine.codex_mcp import GraphShim
from auditor.graph.refine.codex_runner import (
    APPROVAL_MODE,
    EFFORT,
    EPHEMERAL,
    SANDBOX,
    CodexClientError,
    CodexOptions,
    RateLimit,
)
from auditor.graph.refine.sdk_runner import BoundTools

RATE_LIMITS = "account/rateLimits/read"
MCP_STATUS = "mcpServerStatus/list"


def _path_with_bundled_tools() -> str:
    """`PATH` with the SDK's own tool directory first, which is where its `rg` lives.

    The SDK prepends it only when it resolves the binary itself, and naming `codex_bin` turns
    that off, so a run granted `Grep` would otherwise depend on the host having ripgrep.
    """
    path = os.environ.get("PATH", "")
    bundled = bundled_path_dir()
    if bundled is None:
        return path
    return os.pathsep.join(
        [str(bundled), *(part for part in path.split(os.pathsep) if part)]
    )


def codex_config(options: CodexOptions, token: str) -> CodexConfig:
    """The process one run's binary runs as: our own home, our own token, the user's checkout.

    The bundled binary rather than the one on PATH: the protocol bindings are generated against
    it, and the two versions drift by whole minors.
    """
    return CodexConfig(
        codex_bin=str(bundled_codex_path()),
        cwd=str(options.cwd),
        env={
            "CODEX_HOME": str(options.home),
            "PATH": _path_with_bundled_tools(),
            BEARER_ENV: token,
        },
    )


def _failed(exc: Exception) -> TurnResult:
    """A raised turn as the result the SDK does not return for one (drift D1).

    `_raise_for_failed_turn` raises before a `TurnResult` reaches the caller, so the runner would
    otherwise need a second terminal path for exactly the case the first one already maps.
    """
    return TurnResult(
        id="",
        status=TurnStatus.failed,
        error=TurnError(message=str(exc)),
        started_at=None,
        completed_at=None,
        duration_ms=None,
        final_response=None,
        items=[],
        usage=None,
    )


class _Thread:
    """One Codex thread behind `CodexThread`, with a failed turn translated on the way out."""

    def __init__(self, thread: Any, options: CodexOptions) -> None:
        self._thread = thread
        self._options = options
        self.id = getattr(thread, "id", None)

    async def run(self, prompt: str) -> TurnResult:
        try:
            return await self._thread.run(
                prompt, output_schema=self._options.output_schema, effort=EFFORT
            )
        except RuntimeError as exc:
            return _failed(exc)


class CodexRunSession:
    """The `AsyncCodex` behind `CodexSession`, with the run's loopback `graph` server under it.

    The order in `__aenter__` is the contract: the shim has to own a port before the config that
    names it is written, and the config has to exist before the binary reads it. The class is
    the `CodexFactory`: its constructor already takes the two arguments one would be called with.
    """

    def __init__(self, options: CodexOptions, tools: BoundTools) -> None:
        self._options = options
        self._shim = GraphShim(tools)
        self._codex: AsyncCodex | None = None

    @property
    def handshake(self) -> str:
        """What this run's own shim answers with in `serverInfo.version`."""
        return self._shim.handshake

    async def __aenter__(self) -> CodexSession:
        try:
            await self._start()
        except BaseException:
            # `async with` never calls `__aexit__` for an enter that raised, so the shim would
            # otherwise keep a live loopback listener holding this run's credential
            await self.__aexit__(None, None, None)
            raise
        return self

    async def _start(self) -> None:
        """The shim, then the config that names it, then the binary that reads the config."""
        await self._shim.__aenter__()
        CodexHome(
            home=self._options.home,
            root=self._options.cwd,
            server_url=self._shim.url,
            model=self._options.model,
        ).write(auth=self._options.auth)
        self._codex = AsyncCodex(codex_config(self._options, self._shim.token))
        await self._codex.__aenter__()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        try:
            if self._codex is not None:
                await self._codex.__aexit__(exc_type, exc, tb)
        finally:
            await self._shim.__aexit__(exc_type, exc, tb)
        return None

    def _opened(self) -> AsyncCodex:
        """The client this session entered, refusing rather than raising `AttributeError`."""
        if self._codex is None:
            raise CodexClientError("this codex session was used before it was entered")
        return self._codex

    async def servers(self) -> tuple[ServerStatus, ...]:
        """Every mcp server the binary loaded, with its handshake. No wrapper exists for this RPC."""
        answer = await self._opened()._client.request(
            MCP_STATUS, {}, response_model=ListMcpServerStatusResponse
        )
        return tuple(
            ServerStatus(
                name=status.name,
                handshake=None
                if status.server_info is None
                else status.server_info.version,
            )
            for status in answer.data
        )

    async def rate_limit(self) -> RateLimit | None:
        """The account's primary window, or ``None``: the notification lands on an unread queue."""
        answer = await self._opened()._client.request(
            RATE_LIMITS, None, response_model=GetAccountRateLimitsResponse
        )
        primary = answer.rate_limits.primary
        if primary is None:
            return None
        return RateLimit(
            used_percent=float(primary.used_percent),
            resets_at=None if primary.resets_at is None else float(primary.resets_at),
        )

    async def thread_start(self, options: CodexOptions) -> CodexThread:
        thread = await self._opened().thread_start(
            sandbox=Sandbox[SANDBOX],
            approval_mode=ApprovalMode[APPROVAL_MODE],
            ephemeral=EPHEMERAL,
            cwd=str(options.cwd),
            model=options.model or None,
            base_instructions=options.system_prompt,
        )
        return _Thread(thread, options)
