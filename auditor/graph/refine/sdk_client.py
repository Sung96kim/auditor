"""The one module that imports `claude_agent_sdk` (spec 9.3, spec 14).

Turns an `SdkOptions` into a real `ClaudeSDKClient` with the in-process `graph` server bound to one
run, and turns the SDK's five error classes into the one this repo can name. Nothing imports it
unguarded: `drive.py` owns the `observer-claude` guard.
"""

from collections.abc import AsyncIterator
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    HookMatcher,
    ProcessError,
    ResultError,
    create_sdk_mcp_server,
    tool,
)

from auditor.graph.refine.prompts import (
    ALLOWED_TOOLS,
    BRIEF_DESCRIPTION,
    GRAPH_SERVER,
    OUTPUT_FORMAT,
    PROPOSE_DESCRIPTION,
)
from auditor.graph.refine.sdk_runner import (
    EFFORT,
    PERMISSION_MODE,
    SETTING_SOURCES,
    STRICT_MCP_CONFIG,
    BoundTools,
    ClientSession,
    SdkClientError,
    SdkErrorKind,
    SdkOptions,
)

#: the SDK error classes, most specific first: `CLINotFoundError` is a `CLIConnectionError` and
#: `ResultError` is a `ProcessError`, so order is what makes the kind exact
_ERROR_KINDS: tuple[tuple[type[Exception], SdkErrorKind], ...] = (
    (CLINotFoundError, SdkErrorKind.NOT_FOUND),
    (CLIConnectionError, SdkErrorKind.CONNECTION),
    (ResultError, SdkErrorKind.RESULT),
    (ProcessError, SdkErrorKind.PROCESS),
    (CLIJSONDecodeError, SdkErrorKind.DECODE),
)


def error_kind(exc: Exception) -> SdkErrorKind | None:
    """Which kind one SDK exception is, or ``None`` when it is not the SDK's."""
    return next((kind for cls, kind in _ERROR_KINDS if isinstance(exc, cls)), None)


class _Client:
    """The SDK client behind `ClientSession`, with its errors translated on the way out."""

    def __init__(self, client: ClaudeSDKClient) -> None:
        self._client = client

    async def __aenter__(self) -> "ClientSession":
        try:
            await self._client.__aenter__()
        except Exception as exc:
            raise _translated(exc) from exc
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        return await self._client.__aexit__(exc_type, exc, tb)

    async def query(self, prompt: str) -> None:
        try:
            await self._client.query(prompt)
        except Exception as exc:
            raise _translated(exc) from exc

    def receive_response(self) -> AsyncIterator[Any]:
        return self._client.receive_response()


def _translated(exc: Exception) -> Exception:
    """One SDK exception as `SdkClientError`, or itself when it came from somewhere else."""
    kind = error_kind(exc)
    return SdkClientError(str(exc), kind=kind) if kind is not None else exc


class SdkClientFactory:
    """Builds the client one run talks through, bound to that run's own tools."""

    def __call__(self, options: SdkOptions, tools: BoundTools) -> ClientSession:
        return _Client(ClaudeSDKClient(self.options(options, tools)))

    @staticmethod
    def options(options: SdkOptions, tools: BoundTools) -> ClaudeAgentOptions:
        """The SDK options one run runs under, pinned by a test where the extra is installed.

        `skills` is never set: setting it at all rewrites `setting_sources` back to the user's own
        files (spike A.8), which is the isolation this whole option set exists for.
        """

        async def trace(
            input_data: dict[str, Any], tool_use_id: str | None, context: Any
        ) -> dict[str, Any]:
            return await tools.record(input_data)

        server = create_sdk_mcp_server(
            name=GRAPH_SERVER, tools=[_propose(tools), _brief(tools)]
        )
        return ClaudeAgentOptions(
            model=options.model,
            cwd=options.cwd,
            cli_path=options.cli_path,
            system_prompt=options.system_prompt,
            tools=list(options.tools),
            allowed_tools=list(ALLOWED_TOOLS),
            permission_mode=PERMISSION_MODE,
            setting_sources=list(SETTING_SOURCES),
            strict_mcp_config=STRICT_MCP_CONFIG,
            mcp_servers={GRAPH_SERVER: server},
            max_turns=options.max_turns,
            max_budget_usd=options.max_budget_usd,
            effort=EFFORT,
            output_format=dict(OUTPUT_FORMAT),
            hooks={"PostToolUse": [HookMatcher(matcher=None, hooks=[trace])]},
        )


def _propose(tools: BoundTools) -> Any:
    @tool("propose", PROPOSE_DESCRIPTION, BoundTools.INPUT_SCHEMAS["propose"])
    async def propose(args: dict[str, Any]) -> dict[str, Any]:
        return await tools.propose(args)

    return propose


def _brief(tools: BoundTools) -> Any:
    @tool("brief", BRIEF_DESCRIPTION, BoundTools.INPUT_SCHEMAS["brief"])
    async def brief(args: dict[str, Any]) -> dict[str, Any]:
        return await tools.brief(args)

    return brief
